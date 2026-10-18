package ingest

import (
	"cmp"
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/protobuf/proto"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/dq"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
)

// Job is a backfill request with its progress.
type Job struct {
	ID          string
	Source      string
	Timeframes  []mdv1.Timeframe
	Instruments []string
	Start, End  time.Time
	State       mdv1.JobState
	Report      *mdv1.BackfillReport
	Err         string
	StartedAt   time.Time
	FinishedAt  time.Time
}

// Chunk is one resumable unit: one timeframe, a batch of instruments, one window (MD-001).
type Chunk struct {
	Timeframe   mdv1.Timeframe
	Instruments []string
	Start, End  time.Time
}

// Key identifies a chunk within its job (checkpoint key).
func (c Chunk) Key() string {
	return fmt.Sprintf("%s|%d|%d|%s", c.Timeframe, c.Start.Unix(), c.End.Unix(), c.Instruments[0]+".."+c.Instruments[len(c.Instruments)-1])
}

// GapRecord is a gap found by DQ and what happened to it.
type GapRecord struct {
	JobID        string
	Source       string
	InstrumentID string
	Timeframe    string
	From, To     time.Time
	Missing      int
	Status       string // repaired | confirmed_missing | open
}

// Store persists jobs, chunk checkpoints, and gaps (Postgres in production).
type Store interface {
	SaveJob(ctx context.Context, j *Job) error
	LoadJob(ctx context.Context, id string) (*Job, error)
	ChunkDone(ctx context.Context, jobID, key string) (bool, error)
	MarkChunk(ctx context.Context, jobID, key string, rows int64) error
	SaveGap(ctx context.Context, g GapRecord) error
	Unfinished(ctx context.Context) ([]*Job, error)
}

// MemoryStore is an in-memory Store.
type MemoryStore struct {
	mu     sync.Mutex
	jobs   map[string]*Job
	chunks map[string]int64
	Gaps   []GapRecord
}

// NewMemoryStore returns an empty store.
func NewMemoryStore() *MemoryStore {
	return &MemoryStore{jobs: map[string]*Job{}, chunks: map[string]int64{}}
}

func cloneJob(j *Job) *Job {
	c := *j
	if j.Report != nil {
		c.Report = proto.Clone(j.Report).(*mdv1.BackfillReport)
	}
	return &c
}

// SaveJob implements Store.
func (m *MemoryStore) SaveJob(_ context.Context, j *Job) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.jobs[j.ID] = cloneJob(j)
	return nil
}

// LoadJob implements Store.
func (m *MemoryStore) LoadJob(_ context.Context, id string) (*Job, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	j, ok := m.jobs[id]
	if !ok {
		return nil, errors.New("backfill: unknown job")
	}
	return cloneJob(j), nil
}

// ChunkDone implements Store.
func (m *MemoryStore) ChunkDone(_ context.Context, jobID, key string) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	_, ok := m.chunks[jobID+"#"+key]
	return ok, nil
}

// MarkChunk implements Store.
func (m *MemoryStore) MarkChunk(_ context.Context, jobID, key string, rows int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.chunks[jobID+"#"+key] = rows
	return nil
}

// SaveGap implements Store.
func (m *MemoryStore) SaveGap(_ context.Context, g GapRecord) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Gaps = append(m.Gaps, g)
	return nil
}

// Unfinished implements Store.
func (m *MemoryStore) Unfinished(_ context.Context) ([]*Job, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []*Job
	for _, j := range m.jobs {
		if j.State == mdv1.JobState_JOB_STATE_QUEUED || j.State == mdv1.JobState_JOB_STATE_RUNNING {
			out = append(out, cloneJob(j))
		}
	}
	return out, nil
}

// SymbolLookup returns an instrument's ticker spans (instrument master).
type SymbolLookup interface {
	History(ctx context.Context, instrumentID string) []refdata.SymbolSpan
}

// Backfiller runs jobs one at a time (the source's rate limiter bounds throughput, MD-002).
type Backfiller struct {
	Sources map[string]Source
	Lake    *lake.Lake
	Norm    Normalizer
	Symbols SymbolLookup
	Store   Store
	Cal     *calendar.Calendar
	Log     *slog.Logger
	// MinGapRun1m: 1-minute gaps shorter than this are recorded but not re-requested (illiquid names
	// have minutes without trades, research 04 §6.1).
	MinGapRun1m int
	Metrics     *Metrics
	queue       chan string
}

// Metrics of backfills.
type Metrics struct {
	Chunks    *prometheus.CounterVec
	Rows      *prometheus.CounterVec
	Gaps      *prometheus.CounterVec
	ChunkTime prometheus.Histogram
}

// NewMetrics registers backfill metrics.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Chunks:    prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_backfill_chunks_total", Help: "Backfill chunks by outcome."}, []string{"source", "outcome"}),
		Rows:      prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_backfill_rows_total", Help: "Backfill rows by kind (fetched, added, revised, unchanged)."}, []string{"source", "kind"}),
		Gaps:      prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_gaps_total", Help: "Gaps by status (found, repaired, confirmed_missing)."}, []string{"source", "status"}),
		ChunkTime: prometheus.NewHistogram(prometheus.HistogramOpts{Name: "tb_md_backfill_chunk_seconds", Help: "Backfill chunk duration.", Buckets: prometheus.ExponentialBuckets(0.05, 2, 12)}),
	}
	if reg != nil {
		reg.MustRegister(m.Chunks, m.Rows, m.Gaps, m.ChunkTime)
	}
	return m
}

// Windows per timeframe keep each request well under the vendor page limits.
var windowFor = map[mdv1.Timeframe]time.Duration{
	mdv1.Timeframe_TIMEFRAME_1M: 7 * 24 * time.Hour, mdv1.Timeframe_TIMEFRAME_5M: 30 * 24 * time.Hour,
	mdv1.Timeframe_TIMEFRAME_15M: 60 * 24 * time.Hour, mdv1.Timeframe_TIMEFRAME_30M: 90 * 24 * time.Hour,
	mdv1.Timeframe_TIMEFRAME_1H: 180 * 24 * time.Hour, mdv1.Timeframe_TIMEFRAME_1D: 3 * 365 * 24 * time.Hour,
}

// Plan splits a job into chunks.
func (b *Backfiller) Plan(j *Job, batch int) []Chunk {
	ids := append([]string(nil), j.Instruments...)
	sort.Strings(ids)
	var out []Chunk
	for _, tf := range j.Timeframes {
		w := windowFor[tf]
		for s := j.Start; s.Before(j.End); s = s.Add(w) {
			e := s.Add(w)
			if e.After(j.End) {
				e = j.End
			}
			for i := 0; i < len(ids); i += batch {
				out = append(out, Chunk{Timeframe: tf, Instruments: ids[i:min(i+batch, len(ids))], Start: s, End: e})
			}
		}
	}
	return out
}

// Submit queues a new job and returns its ID.
func (b *Backfiller) Submit(ctx context.Context, j *Job) (string, error) {
	if _, ok := b.Sources[j.Source]; !ok {
		return "", fmt.Errorf("backfill: unknown source %q", j.Source)
	}
	if !j.End.After(j.Start) || len(j.Timeframes) == 0 || len(j.Instruments) == 0 {
		return "", errors.New("backfill: need instruments, timeframes, and start < end")
	}
	for _, tf := range j.Timeframes {
		if tf == mdv1.Timeframe_TIMEFRAME_1H {
			// Vendor hourly bars start on the clock hour and would straddle the 09:30 open (MD-004).
			return "", errors.New("backfill: 1h bars are built from 1m bars, not fetched")
		}
	}
	if j.ID != "" {
		// Caller-chosen IDs make submission idempotent: an existing job is never queued twice
		// (unfinished jobs are resumed by Run).
		if _, err := b.Store.LoadJob(ctx, j.ID); err == nil {
			return j.ID, nil
		}
	} else {
		j.ID = ulid.MustNew(ulid.Now(), rand.Reader).String()
	}
	j.State = mdv1.JobState_JOB_STATE_QUEUED
	j.Report = &mdv1.BackfillReport{DqFindings: map[string]int64{}}
	if err := b.Store.SaveJob(ctx, j); err != nil {
		return "", err
	}
	b.ensureQueue()
	b.queue <- j.ID
	return j.ID, nil
}

func (b *Backfiller) ensureQueue() {
	if b.queue == nil {
		b.queue = make(chan string, 1024)
	}
}

// Run processes queued jobs (and resumes unfinished ones from the store) until ctx ends.
func (b *Backfiller) Run(ctx context.Context) {
	b.ensureQueue()
	if jobs, err := b.Store.Unfinished(ctx); err == nil {
		for _, j := range jobs {
			b.queue <- j.ID
		}
	}
	for {
		select {
		case <-ctx.Done():
			return
		case id := <-b.queue:
			if err := b.RunJob(ctx, id); err != nil && b.Log != nil {
				b.Log.Error("backfill job failed", "job", id, "err", err)
			}
		}
	}
}

// RunJob executes (or resumes) one job: chunks already checkpointed are skipped, so a crash or
// restart repeats at most the chunk in flight, which the idempotent lake write absorbs (ST-004).
func (b *Backfiller) RunJob(ctx context.Context, id string) error {
	j, err := b.Store.LoadJob(ctx, id)
	if err != nil {
		return err
	}
	src := b.Sources[j.Source]
	j.State, j.StartedAt = mdv1.JobState_JOB_STATE_RUNNING, time.Now()
	if j.Report == nil {
		j.Report = &mdv1.BackfillReport{}
	}
	if j.Report.DqFindings == nil {
		j.Report.DqFindings = map[string]int64{}
	}
	chunks := b.Plan(j, src.MaxSymbols()/2)
	j.Report.ChunksTotal = int64(len(chunks))
	j.Report.ChunksDone = 0 // recounted from checkpoints on every (re)start
	_ = b.Store.SaveJob(ctx, j)
	for _, c := range chunks {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		done, err := b.Store.ChunkDone(ctx, j.ID, c.Key())
		if err != nil {
			return err
		}
		if done {
			j.Report.ChunksDone++
			continue
		}
		t := time.Now()
		if err := b.runChunk(ctx, j, src, c); err != nil {
			b.Metrics.Chunks.WithLabelValues(j.Source, "error").Inc()
			j.State, j.Err, j.FinishedAt = mdv1.JobState_JOB_STATE_FAILED, err.Error(), time.Now()
			_ = b.Store.SaveJob(ctx, j)
			return err
		}
		b.Metrics.ChunkTime.Observe(time.Since(t).Seconds())
		b.Metrics.Chunks.WithLabelValues(j.Source, "ok").Inc()
		j.Report.ChunksDone++
		if err := b.Store.MarkChunk(ctx, j.ID, c.Key(), j.Report.RowsFetched); err != nil {
			return err
		}
		_ = b.Store.SaveJob(ctx, j)
	}
	j.State, j.FinishedAt = mdv1.JobState_JOB_STATE_SUCCEEDED, time.Now()
	if j.Report.GapsFound > j.Report.GapsRepaired+j.Report.GapsConfirmedMissing || len(j.Report.DqFindings) > 0 {
		j.State = mdv1.JobState_JOB_STATE_SUCCEEDED_WITH_GAPS
	}
	return b.Store.SaveJob(ctx, j)
}

// symbolsFor returns, per ticker, the instruments to request in [s, e) (ticker valid at the window
// end; a ticker change inside the window splits it, MD-001).
type req struct {
	Symbol       string
	InstrumentID string
	Start, End   time.Time
}

func (b *Backfiller) requests(ctx context.Context, c Chunk) []req {
	var out []req
	for _, id := range c.Instruments {
		for _, sp := range b.Symbols.History(ctx, id) {
			from := time.Unix(0, sp.ValidFrom)
			to := c.End
			if sp.ValidTo != 0 && time.Unix(0, sp.ValidTo).Before(to) {
				to = time.Unix(0, sp.ValidTo)
			}
			if from.Before(c.Start) {
				from = c.Start
			}
			if to.After(from) {
				out = append(out, req{Symbol: sp.Symbol, InstrumentID: id, Start: from, End: to})
			}
		}
	}
	return out
}

func (b *Backfiller) fetch(ctx context.Context, src Source, tf mdv1.Timeframe, rs []req) ([]*mdv1.Bar, error) {
	// Group requests sharing a window into multi-symbol calls.
	type win struct{ s, e int64 }
	groups := map[win][]req{}
	for _, r := range rs {
		groups[win{r.Start.UnixNano(), r.End.UnixNano()}] = append(groups[win{r.Start.UnixNano(), r.End.UnixNano()}], r)
	}
	var out []*mdv1.Bar
	for w, g := range groups {
		bySym := map[string]string{}
		var syms []string
		for _, r := range g {
			bySym[r.Symbol] = r.InstrumentID
			syms = append(syms, r.Symbol)
		}
		sort.Strings(syms)
		bars, err := src.Bars(ctx, syms, tf, time.Unix(0, w.s), time.Unix(0, w.e))
		if err != nil {
			return nil, err
		}
		for _, bar := range bars {
			bar.InstrumentId = bySym[bar.GetSymbol()]
			if bar.GetFeed() == "" {
				bar.Feed = src.Feed()
			}
		}
		out = append(out, bars...)
	}
	return out, nil
}

func (b *Backfiller) runChunk(ctx context.Context, j *Job, src Source, c Chunk) error {
	rs := b.requests(ctx, c)
	if len(rs) == 0 {
		return nil
	}
	raw, err := b.fetch(ctx, src, c.Timeframe, rs)
	if err != nil {
		return fmt.Errorf("fetch %s: %w", c.Key(), err)
	}
	bars, _, _ := b.Norm.Normalize(ctx, raw, true)
	j.Report.RowsFetched += int64(len(bars))
	b.Metrics.Rows.WithLabelValues(j.Source, "fetched").Add(float64(len(bars)))
	if err := b.write(ctx, j, bars); err != nil {
		return err
	}
	return b.checkAndRepair(ctx, j, src, c, bars)
}

func (b *Backfiller) write(ctx context.Context, j *Job, bars []*mdv1.Bar) error {
	if len(bars) == 0 {
		return nil
	}
	parts, err := b.Lake.StageBars(ctx, bars)
	if err != nil {
		return err
	}
	for p := range parts {
		r, err := b.Lake.Compact(ctx, p)
		if err != nil {
			return err
		}
		j.Report.RowsAdded += int64(r.Added)
		j.Report.RowsRevised += int64(r.Revised)
		b.Metrics.Rows.WithLabelValues(j.Source, "added").Add(float64(r.Added))
		b.Metrics.Rows.WithLabelValues(j.Source, "revised").Add(float64(r.Revised))
		b.Metrics.Rows.WithLabelValues(j.Source, "unchanged").Add(float64(r.Unchanged))
	}
	return nil
}

func dqBars(bars []*mdv1.Bar) []dq.Bar {
	out := make([]dq.Bar, 0, len(bars))
	p := func(d interface{ GetValue() string }) float64 { v, _ := strconv.ParseFloat(d.GetValue(), 64); return v }
	for _, b := range bars {
		out = append(out, dq.Bar{InstrumentID: b.GetInstrumentId(), TsOpen: time.Unix(0, b.GetTsOpen()).UTC(), TsClose: time.Unix(0, b.GetTsClose()).UTC(),
			Open: p(b.GetOpen()), High: p(b.GetHigh()), Low: p(b.GetLow()), Close: p(b.GetClose()), Volume: p(b.GetVolume())})
	}
	return out
}

// checkAndRepair validates the chunk (ST-010) and re-requests each gap once (MD-006); bars that
// appear are written as repaired, the rest are recorded as confirmed missing (coverage gap).
func (b *Backfiller) checkAndRepair(ctx context.Context, j *Job, src Source, c Chunk, bars []*mdv1.Bar) error {
	tf := c.Timeframe
	opt := dq.Options{Timeframe: TimeframeDur[tf], Daily: tf == mdv1.Timeframe_TIMEFRAME_1D, MinGapRun: 1}
	regular := make([]*mdv1.Bar, 0, len(bars))
	for _, bar := range bars {
		if tf == mdv1.Timeframe_TIMEFRAME_1D || bar.GetSession() == mdv1.Session_SESSION_REGULAR {
			regular = append(regular, bar)
		}
	}
	// Requests per symbol span (ticker changes) arrive in any order; DQ expects time order.
	slices.SortFunc(regular, func(x, y *mdv1.Bar) int {
		return cmp.Or(strings.Compare(x.GetInstrumentId(), y.GetInstrumentId()), cmp.Compare(x.GetTsOpen(), y.GetTsOpen()))
	})
	// Only instruments listed for the whole window are expected to have every bar.
	var expected []string
	for _, id := range c.Instruments {
		if spans := b.Symbols.History(ctx, id); len(spans) > 0 && spans[0].ValidFrom <= c.Start.UnixNano() &&
			(spans[len(spans)-1].ValidTo == 0 || spans[len(spans)-1].ValidTo >= c.End.UnixNano()) {
			expected = append(expected, id)
		}
	}
	rep := dq.Check(dqBars(regular), expected, b.Cal, c.Start, c.End, opt)
	for k, v := range rep.Counts {
		if k != "gap" {
			j.Report.DqFindings[k] += int64(v)
		}
	}
	symOf := map[string]string{}
	for _, r := range b.requests(ctx, c) {
		symOf[r.InstrumentID] = r.Symbol
	}
	minRun := 1
	if tf == mdv1.Timeframe_TIMEFRAME_1M && b.MinGapRun1m > 1 {
		minRun = b.MinGapRun1m
	}
	for _, g := range rep.Gaps {
		j.Report.GapsFound++
		b.Metrics.Gaps.WithLabelValues(j.Source, "found").Inc()
		rec := GapRecord{JobID: j.ID, Source: j.Source, InstrumentID: g.InstrumentID, Timeframe: lake.TimeframeName[tf], From: g.From, To: g.To, Missing: g.Missing, Status: "confirmed_missing"}
		if g.Missing >= minRun && symOf[g.InstrumentID] != "" {
			again, err := src.Bars(ctx, []string{symOf[g.InstrumentID]}, tf, g.From, g.To)
			if err != nil {
				return err
			}
			for _, x := range again {
				x.InstrumentId, x.Repaired = g.InstrumentID, true
				if x.GetFeed() == "" {
					x.Feed = src.Feed()
				}
			}
			fixed, _, _ := b.Norm.Normalize(ctx, again, true)
			if len(fixed) > 0 {
				if err := b.write(ctx, j, fixed); err != nil {
					return err
				}
				rec.Status = "repaired"
				if len(fixed) < g.Missing {
					rec.Status = "open"
				}
			}
		}
		switch rec.Status {
		case "repaired":
			j.Report.GapsRepaired++
		case "confirmed_missing":
			j.Report.GapsConfirmedMissing++
		}
		b.Metrics.Gaps.WithLabelValues(j.Source, rec.Status).Inc()
		if err := b.Store.SaveGap(ctx, rec); err != nil {
			return err
		}
	}
	return nil
}
