// Package live turns the streaming minute bars from broker-gateway into final bars on the bus, in
// the cache, and in the lake (section 05, ADR-0022, rules MD-002..MD-009):
//
//   - a vendor minute bar is published at once as a partial (UI only) and becomes final at
//     ts_close + grace (60 s, after Alpaca's updatedBars window); available_at is the actual local
//     time it became final;
//   - updatedBars before finality replace the pending bar; after finality they become a correction
//     (revision + 1) that strategies ignore for decisions already made;
//   - higher timeframes are built from final minute bars aligned to the session part (pre, regular,
//     post) and never cross a part boundary;
//   - after a stream reconnect the missed interval is repaired from REST and flagged repaired.
package live

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/ingest"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

// Sink receives bars (NATS + cache + lake in production).
type Sink interface {
	Final(ctx context.Context, b *mdv1.Bar) error
	Correction(ctx context.Context, b *mdv1.Bar) error
	Partial(ctx context.Context, b *mdv1.Bar)
}

// Metrics of the live pipeline.
type Metrics struct {
	Final        *prometheus.CounterVec
	Corrections  *prometheus.CounterVec
	Repaired     prometheus.Counter
	Partials     prometheus.Counter
	FinalToBus   prometheus.Histogram
	CloseToBus   *prometheus.HistogramVec
	StaleSymbols prometheus.Gauge
	Unknown      prometheus.Counter
}

// NewMetrics registers live metrics (nil registerer: unregistered).
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Final:        prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_bars_final_total", Help: "Final bars published by timeframe."}, []string{"timeframe"}),
		Corrections:  prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_bar_corrections_total", Help: "Corrections of already-final bars."}, []string{"timeframe"}),
		Repaired:     prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_md_bars_repaired_total", Help: "Final bars recovered from REST after a stream gap."}),
		Partials:     prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_md_bars_partial_total", Help: "Partial (UI-only) bars published."}),
		FinalToBus:   prometheus.NewHistogram(prometheus.HistogramOpts{Name: "tb_md_bar_final_to_bus_seconds", Help: "Bar finality decision to bus/cache/lake hand-off (U8).", Buckets: []float64{.0005, .001, .0025, .005, .01, .025, .05, .1, .25, .5, 1}}),
		CloseToBus:   prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "tb_md_bar_close_to_bus_seconds", Help: "Bar close to publication (includes the 60 s finality window).", Buckets: []float64{30, 45, 60, 61, 62, 65, 70, 90, 120, 300}}, []string{"timeframe"}),
		StaleSymbols: prometheus.NewGauge(prometheus.GaugeOpts{Name: "tb_md_stale_symbols", Help: "Subscribed instruments without a recent final bar during a session."}),
		Unknown:      prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_md_unknown_symbols_total", Help: "Streamed bars whose symbol did not resolve to an instrument."}),
	}
	if reg != nil {
		reg.MustRegister(m.Final, m.Corrections, m.Repaired, m.Partials, m.FinalToBus, m.CloseToBus, m.StaleSymbols, m.Unknown)
	}
	return m
}

type pending struct {
	bar      *mdv1.Bar
	received int64
	repaired bool
}

type bucket struct {
	inst        string
	tf          mdv1.Timeframe
	open, close int64
	session     mdv1.Session
	minutes     map[int64]*mdv1.Bar // by ts_open
	final       bool
	revision    int32
	published   *mdv1.Bar
}

// Pipeline is safe for concurrent use.
type Pipeline struct {
	Cal        *calendar.Calendar
	Ref        ingest.Resolver
	Sink       Sink
	Feed       string
	Grace      time.Duration
	Timeframes []mdv1.Timeframe // resampled timeframes (5m, 15m, 30m, 1h)
	Now        func() time.Time
	Log        *slog.Logger
	M          *Metrics
	// Observe, when set, receives each published final bar with its finality-to-bus latency.
	Observe func(b *mdv1.Bar, d time.Duration)

	mu        sync.Mutex
	symbols   map[string]string // symbol -> instrument_id (current universe)
	pending   map[string]*pending
	finals    map[string]*mdv1.Bar // recent final minute bars by key
	buckets   map[string]*bucket   // inst|tf|open
	lastFinal map[string]int64     // inst|tf -> ts_close of latest final
	connected bool
	lastMsg   int64
}

// New returns a pipeline with defaults (grace 60 s; 5m/15m/30m/1h).
func New(cal *calendar.Calendar, ref ingest.Resolver, sink Sink, feed string, log *slog.Logger, m *Metrics) *Pipeline {
	if m == nil {
		m = NewMetrics(nil)
	}
	return &Pipeline{Cal: cal, Ref: ref, Sink: sink, Feed: feed, Grace: 60 * time.Second, Now: time.Now, Log: log, M: m,
		Timeframes: []mdv1.Timeframe{mdv1.Timeframe_TIMEFRAME_5M, mdv1.Timeframe_TIMEFRAME_15M, mdv1.Timeframe_TIMEFRAME_30M, mdv1.Timeframe_TIMEFRAME_1H},
		symbols:    map[string]string{}, pending: map[string]*pending{}, finals: map[string]*mdv1.Bar{}, buckets: map[string]*bucket{}, lastFinal: map[string]int64{}}
}

// SetSymbols sets the universe (symbol → instrument_id) and returns the symbols added and removed.
func (p *Pipeline) SetSymbols(m map[string]string) (added, removed []string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for s := range m {
		if _, ok := p.symbols[s]; !ok {
			added = append(added, s)
		}
	}
	for s := range p.symbols {
		if _, ok := m[s]; !ok {
			removed = append(removed, s)
		}
	}
	p.symbols = map[string]string{}
	for k, v := range m {
		p.symbols[k] = v
	}
	sort.Strings(added)
	sort.Strings(removed)
	return added, removed
}

// Symbols returns the current universe symbols.
func (p *Pipeline) Symbols() map[string]string {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make(map[string]string, len(p.symbols))
	for k, v := range p.symbols {
		out[k] = v
	}
	return out
}

func key(inst string, tf mdv1.Timeframe, tsOpen int64) string {
	return inst + "|" + tf.String() + "|" + strconv.FormatInt(tsOpen, 10)
}

// MsgID is the bus dedupe ID: hash of the natural key plus revision (master "Bus subjects").
func MsgID(b *mdv1.Bar) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s|%s|%d|%s|%d", b.GetInstrumentId(), b.GetTimeframe(), b.GetTsOpen(), b.GetFeed(), b.GetRevision())))
	return hex.EncodeToString(h[:12])
}

func (p *Pipeline) resolve(ctx context.Context, b *mdv1.Bar) (*mdv1.Bar, bool) {
	b = proto.Clone(b).(*mdv1.Bar)
	if b.GetInstrumentId() == "" {
		if id, ok := p.symbols[b.GetSymbol()]; ok {
			b.InstrumentId = id
		} else if p.Ref != nil {
			id, err := p.Ref.Resolve(ctx, b.GetSymbol(), b.GetTsOpen())
			if err != nil {
				p.M.Unknown.Inc()
				return nil, false
			}
			b.InstrumentId = id
		}
	}
	s, part, ok := p.Cal.SessionAt(time.Unix(0, b.GetTsOpen()).UTC())
	if !ok {
		return nil, false
	}
	b.TsClose = s.BarClose(time.Unix(0, b.GetTsOpen()).UTC(), time.Minute).UnixNano()
	b.Session = partSession(part)
	if b.GetFeed() == "" {
		b.Feed = p.Feed
	}
	b.AdjustmentBasis = mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW
	return b, true
}

func partSession(p calendar.Part) mdv1.Session {
	switch p {
	case calendar.PartPre:
		return mdv1.Session_SESSION_PRE
	case calendar.PartPost:
		return mdv1.Session_SESSION_POST
	default:
		return mdv1.Session_SESSION_REGULAR
	}
}

// OnBar handles a vendor minute bar: publish as partial, hold until final (MD-002).
func (p *Pipeline) OnBar(ctx context.Context, vb *mdv1.Bar) {
	p.mu.Lock()
	p.lastMsg = p.Now().UnixNano()
	b, ok := p.resolve(ctx, vb)
	if !ok {
		p.mu.Unlock()
		return
	}
	k := key(b.GetInstrumentId(), mdv1.Timeframe_TIMEFRAME_1M, b.GetTsOpen())
	if _, done := p.finals[k]; done {
		p.mu.Unlock()
		p.OnUpdatedBar(ctx, vb) // a re-sent bar after finality is treated like a correction
		return
	}
	b.IsFinal = false
	p.pending[k] = &pending{bar: b, received: p.Now().UnixNano()}
	part := proto.Clone(b).(*mdv1.Bar)
	p.mu.Unlock()
	p.M.Partials.Inc()
	p.Sink.Partial(ctx, part)
}

// OnUpdatedBar handles Alpaca updatedBars: before finality it replaces the pending bar; after
// finality it publishes a correction with revision + 1 and re-derives affected higher-timeframe
// bars (MD-002, DF-007).
func (p *Pipeline) OnUpdatedBar(ctx context.Context, vb *mdv1.Bar) {
	p.mu.Lock()
	p.lastMsg = p.Now().UnixNano()
	b, ok := p.resolve(ctx, vb)
	if !ok {
		p.mu.Unlock()
		return
	}
	k := key(b.GetInstrumentId(), mdv1.Timeframe_TIMEFRAME_1M, b.GetTsOpen())
	if pd, ok := p.pending[k]; ok {
		b.IsFinal = false
		pd.bar = b
		p.mu.Unlock()
		return
	}
	prev, ok := p.finals[k]
	if !ok || sameOHLCV(prev, b) {
		p.mu.Unlock()
		return
	}
	now := p.Now().UnixNano()
	b.IsFinal, b.Revision, b.AvailableAt, b.IngestedAt, b.Repaired = true, prev.GetRevision()+1, now, now, prev.GetRepaired()
	p.finals[k] = b
	corr := proto.Clone(b).(*mdv1.Bar)
	reagg := p.addToBuckets(b)
	p.mu.Unlock()
	p.M.Corrections.WithLabelValues("1m").Inc()
	if err := p.Sink.Correction(ctx, corr); err != nil && p.Log != nil {
		p.Log.Warn("correction publish failed", "err", err)
	}
	p.publishCorrections(ctx, reagg)
}

func sameOHLCV(a, b *mdv1.Bar) bool {
	return a.GetOpen().GetValue() == b.GetOpen().GetValue() && a.GetHigh().GetValue() == b.GetHigh().GetValue() &&
		a.GetLow().GetValue() == b.GetLow().GetValue() && a.GetClose().GetValue() == b.GetClose().GetValue() &&
		a.GetVolume().GetValue() == b.GetVolume().GetValue() && a.GetTradeCount() == b.GetTradeCount() && a.GetVwap().GetValue() == b.GetVwap().GetValue()
}

// Tick finalizes due minute bars and higher-timeframe buckets (call every ~250 ms).
func (p *Pipeline) Tick(ctx context.Context) {
	now := p.Now()
	p.mu.Lock()
	decided := time.Now()
	var due []*pending
	for k, pd := range p.pending {
		if now.UnixNano() >= pd.bar.GetTsClose()+p.Grace.Nanoseconds() {
			due = append(due, pd)
			delete(p.pending, k)
		}
	}
	sort.Slice(due, func(i, j int) bool { return due[i].bar.GetTsOpen() < due[j].bar.GetTsOpen() })
	var finals []*mdv1.Bar
	for _, pd := range due {
		finals = append(finals, p.finalizeLocked(pd.bar, now, pd.repaired))
	}
	aggs := p.dueBucketsLocked(now)
	p.pruneLocked(now)
	p.mu.Unlock()
	p.publishFinals(ctx, append(finals, aggs...), decided)
}

// finalizeLocked marks a minute bar final (available_at = now, MD-003) and feeds the resampler.
func (p *Pipeline) finalizeLocked(b *mdv1.Bar, now time.Time, repaired bool) *mdv1.Bar {
	b = proto.Clone(b).(*mdv1.Bar)
	b.IsFinal, b.Repaired = true, repaired
	b.AvailableAt, b.IngestedAt = now.UnixNano(), now.UnixNano()
	if b.GetAvailableAt() < b.GetTsClose() {
		b.AvailableAt = b.GetTsClose()
	}
	k := key(b.GetInstrumentId(), mdv1.Timeframe_TIMEFRAME_1M, b.GetTsOpen())
	p.finals[k] = b
	lk := b.GetInstrumentId() + "|" + mdv1.Timeframe_TIMEFRAME_1M.String()
	if b.GetTsClose() > p.lastFinal[lk] {
		p.lastFinal[lk] = b.GetTsClose()
	}
	p.addToBuckets(b)
	return b
}

func (p *Pipeline) bucketOf(t time.Time, d time.Duration) (open, close time.Time, sess mdv1.Session, ok bool) {
	s, part, ok := p.Cal.SessionAt(t)
	if !ok {
		return time.Time{}, time.Time{}, 0, false
	}
	base := s.Open
	switch part {
	case calendar.PartPre:
		base = s.PreOpen
	case calendar.PartPost:
		base = s.Close
	}
	open = base.Add(t.Sub(base) / d * d)
	return open, s.BarClose(open, d), partSession(part), true
}

// addToBuckets adds a final minute bar to its higher-timeframe buckets and returns buckets that
// were already final and must be re-published as corrections.
func (p *Pipeline) addToBuckets(b *mdv1.Bar) []*bucket {
	var reagg []*bucket
	t := time.Unix(0, b.GetTsOpen()).UTC()
	for _, tf := range p.Timeframes {
		open, cl, sess, ok := p.bucketOf(t, ingest.TimeframeDur[tf])
		if !ok {
			continue
		}
		k := key(b.GetInstrumentId(), tf, open.UnixNano())
		bk := p.buckets[k]
		if bk == nil {
			bk = &bucket{inst: b.GetInstrumentId(), tf: tf, open: open.UnixNano(), close: cl.UnixNano(), session: sess, minutes: map[int64]*mdv1.Bar{}}
			p.buckets[k] = bk
		}
		bk.minutes[b.GetTsOpen()] = b
		if bk.final {
			reagg = append(reagg, bk)
		}
	}
	return reagg
}

// Aggregate builds a higher-timeframe bar from minute bars (O first, H max, L min, C last, V sum,
// VWAP volume-weighted, trade count sum).
func Aggregate(inst, feed, source string, tf mdv1.Timeframe, open, close int64, sess mdv1.Session, minutes []*mdv1.Bar) *mdv1.Bar {
	sort.Slice(minutes, func(i, j int) bool { return minutes[i].GetTsOpen() < minutes[j].GetTsOpen() })
	dec := func(x *commonv1.Decimal) decimal.Decimal { v, _ := decimal.NewFromString(x.GetValue()); return v }
	o, c := dec(minutes[0].GetOpen()), dec(minutes[len(minutes)-1].GetClose())
	h, l := dec(minutes[0].GetHigh()), dec(minutes[0].GetLow())
	vol, pv := decimal.Zero, decimal.Zero
	var n int64
	repaired := false
	for _, m := range minutes {
		h = decimal.Max(h, dec(m.GetHigh()))
		l = decimal.Min(l, dec(m.GetLow()))
		v := dec(m.GetVolume())
		vol = vol.Add(v)
		pv = pv.Add(dec(m.GetVwap()).Mul(v))
		n += m.GetTradeCount()
		repaired = repaired || m.GetRepaired()
	}
	vwap := c
	if vol.IsPositive() {
		vwap = pv.DivRound(vol, 6)
	}
	d := func(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }
	return &mdv1.Bar{InstrumentId: inst, Symbol: minutes[len(minutes)-1].GetSymbol(), Timeframe: tf, TsOpen: open, TsClose: close,
		Open: d(o), High: d(h), Low: d(l), Close: d(c), Volume: d(vol), Vwap: d(vwap), TradeCount: n, Feed: feed, Source: source,
		IsFinal: true, Session: sess, Repaired: repaired, AdjustmentBasis: mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW}
}

func (p *Pipeline) buildLocked(bk *bucket, now time.Time) *mdv1.Bar {
	ms := make([]*mdv1.Bar, 0, len(bk.minutes))
	for _, m := range bk.minutes {
		ms = append(ms, m)
	}
	b := Aggregate(bk.inst, ms[0].GetFeed(), ms[0].GetSource(), bk.tf, bk.open, bk.close, bk.session, ms)
	b.AvailableAt, b.IngestedAt = now.UnixNano(), now.UnixNano()
	if b.GetAvailableAt() < b.GetTsClose() {
		b.AvailableAt = b.GetTsClose()
	}
	return b
}

// dueBucketsLocked finalizes buckets whose close + grace has passed or whose last minute is final.
func (p *Pipeline) dueBucketsLocked(now time.Time) []*mdv1.Bar {
	var out []*mdv1.Bar
	keys := make([]string, 0, len(p.buckets))
	for k := range p.buckets {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		bk := p.buckets[k]
		if bk.final || len(bk.minutes) == 0 {
			continue
		}
		_, lastMinute := bk.minutes[bk.close-int64(time.Minute)]
		if !lastMinute && now.UnixNano() < bk.close+p.Grace.Nanoseconds() {
			continue
		}
		b := p.buildLocked(bk, now)
		bk.final, bk.published = true, b
		lk := b.GetInstrumentId() + "|" + b.GetTimeframe().String()
		if b.GetTsClose() > p.lastFinal[lk] {
			p.lastFinal[lk] = b.GetTsClose()
		}
		out = append(out, b)
	}
	return out
}

func (p *Pipeline) publishCorrections(ctx context.Context, bks []*bucket) {
	for _, bk := range bks {
		p.mu.Lock()
		b := p.buildLocked(bk, p.Now())
		if sameOHLCV(b, bk.published) {
			p.mu.Unlock()
			continue
		}
		bk.revision++
		b.Revision = bk.revision
		bk.published = b
		p.mu.Unlock()
		p.M.Corrections.WithLabelValues(lake.TimeframeName[b.GetTimeframe()]).Inc()
		_ = p.Sink.Correction(ctx, b)
	}
}

// BatchSink is implemented by sinks that can publish many final bars concurrently (JetStream
// async publish: one fsync batch instead of one per bar).
type BatchSink interface {
	FinalBatch(ctx context.Context, bars []*mdv1.Bar) error
}

// publishFinals hands the bars of one finality decision to the sinks and records the latency from
// that decision to acknowledgement (U8).
func (p *Pipeline) publishFinals(ctx context.Context, bars []*mdv1.Bar, decided time.Time) {
	if len(bars) == 0 {
		return
	}
	var err error
	if bs, ok := p.Sink.(BatchSink); ok {
		err = bs.FinalBatch(ctx, bars)
	} else {
		for _, b := range bars {
			err = errors.Join(err, p.Sink.Final(ctx, b))
		}
	}
	if err != nil && p.Log != nil {
		p.Log.Error("final bar publish failed", "bars", len(bars), "err", err)
	}
	lat := time.Since(decided)
	for _, b := range bars {
		p.M.FinalToBus.Observe(lat.Seconds())
		if p.Observe != nil {
			p.Observe(b, lat)
		}
		tf := lake.TimeframeName[b.GetTimeframe()]
		p.M.Final.WithLabelValues(tf).Inc()
		p.M.CloseToBus.WithLabelValues(tf).Observe(time.Duration(p.Now().UnixNano() - b.GetTsClose()).Seconds())
		if b.GetRepaired() {
			p.M.Repaired.Inc()
		}
	}
}

// pruneLocked forgets finalized state older than two hours (corrections later than that are
// handled by the nightly re-pull, MD-006).
func (p *Pipeline) pruneLocked(now time.Time) {
	cut := now.Add(-2 * time.Hour).UnixNano()
	for k, b := range p.finals {
		if b.GetTsClose() < cut {
			delete(p.finals, k)
		}
	}
	for k, bk := range p.buckets {
		if bk.final && bk.close < cut {
			delete(p.buckets, k)
		}
	}
}

// SetConnected records feed connectivity and returns true on a reconnect (gap repair needed).
func (p *Pipeline) SetConnected(c bool) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	was := p.connected
	p.connected = c
	return c && !was
}

// HistSource fetches minute bars for gap repair.
type HistSource interface {
	Bars(ctx context.Context, symbols []string, tf mdv1.Timeframe, start, end time.Time) ([]*mdv1.Bar, error)
}

// Repair fetches minute bars for the interval missed while disconnected (every bar that closed by
// now) and treats each bar not already final as streamed: final at once if its finality window has
// passed, otherwise pending; either way flagged repaired (MD-006). Returns the bars recovered.
func (p *Pipeline) Repair(ctx context.Context, src HistSource, from time.Time) (int, error) {
	decided := time.Now()
	now := p.Now()
	to := now.Truncate(time.Minute)
	if !to.After(from) {
		return 0, nil
	}
	syms := p.Symbols()
	list := make([]string, 0, len(syms))
	for s := range syms {
		list = append(list, s)
	}
	sort.Strings(list)
	n := 0
	for i := 0; i < len(list); i += 100 {
		batch := list[i:min(i+100, len(list))]
		bars, err := src.Bars(ctx, batch, mdv1.Timeframe_TIMEFRAME_1M, from, to)
		if err != nil {
			return n, err
		}
		p.mu.Lock()
		var finals []*mdv1.Bar
		for _, vb := range bars {
			b, ok := p.resolve(ctx, vb)
			if !ok || b.GetTsClose() > now.UnixNano() {
				continue
			}
			k := key(b.GetInstrumentId(), mdv1.Timeframe_TIMEFRAME_1M, b.GetTsOpen())
			if _, done := p.finals[k]; done {
				continue
			}
			if _, streaming := p.pending[k]; streaming {
				continue // the live stream already delivered it; finality proceeds normally
			}
			n++
			if now.UnixNano() >= b.GetTsClose()+p.Grace.Nanoseconds() {
				finals = append(finals, p.finalizeLocked(b, now, true))
			} else {
				b.IsFinal = false
				p.pending[k] = &pending{bar: b, received: now.UnixNano(), repaired: true}
			}
		}
		aggs := p.dueBucketsLocked(now)
		p.mu.Unlock()
		p.publishFinals(ctx, append(finals, aggs...), decided)
	}
	return n, nil
}

// LastFinal returns the latest final bar close per instrument for a timeframe.
func (p *Pipeline) LastFinal(inst string, tf mdv1.Timeframe) int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.lastFinal[inst+"|"+tf.String()]
}

// EarliestLastFinal is the oldest "latest final bar close" across the universe (repair start).
func (p *Pipeline) EarliestLastFinal() int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	var out int64
	for _, id := range p.symbols {
		v := p.lastFinal[id+"|"+mdv1.Timeframe_TIMEFRAME_1M.String()]
		if v > 0 && (out == 0 || v < out) {
			out = v
		}
	}
	return out
}

// IsFresh reports whether the instrument's latest final bar is recent enough (MD-007, RM-004):
// stale when the feed is disconnected, outside a session, or when the last final bar closed more
// than maxAge (default two bar intervals) plus the finality grace ago. Fails closed.
func (p *Pipeline) IsFresh(inst string, tf mdv1.Timeframe, maxAge time.Duration) (bool, int64, string) {
	now := p.Now()
	p.mu.Lock()
	last := p.lastFinal[inst+"|"+tf.String()]
	connected := p.connected
	p.mu.Unlock()
	if maxAge <= 0 {
		maxAge = 2 * ingest.TimeframeDur[tf]
	}
	switch {
	case !connected:
		return false, last, "feed disconnected"
	case last == 0:
		return false, 0, "no final bar yet"
	}
	if _, _, ok := p.Cal.SessionAt(now); !ok {
		return false, last, "outside trading session"
	}
	if age := now.Sub(time.Unix(0, last)); age > maxAge+p.Grace {
		return false, last, fmt.Sprintf("last final bar closed %s ago (max %s + grace %s)", age.Round(time.Second), maxAge, p.Grace)
	}
	return true, last, ""
}

// StaleSymbols lists universe symbols without a final minute bar within maxAge during a regular
// session (MD-007).
func (p *Pipeline) StaleSymbols(maxAge time.Duration) []string {
	now := p.Now()
	if !p.Cal.IsOpen(now.Add(-p.Grace - maxAge)) {
		return nil
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	var out []string
	for sym, id := range p.symbols {
		if now.Sub(time.Unix(0, p.lastFinal[id+"|"+mdv1.Timeframe_TIMEFRAME_1M.String()])) > maxAge+p.Grace {
			out = append(out, sym)
		}
	}
	sort.Strings(out)
	p.M.StaleSymbols.Set(float64(len(out)))
	return out
}

// LastMessageAt returns the last stream message time (ns).
func (p *Pipeline) LastMessageAt() int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.lastMsg
}

// Connected reports feed connectivity.
func (p *Pipeline) Connected() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.connected
}
