package ingest

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strconv"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/test/bufconn"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
)

func d(f float64) *commonv1.Decimal {
	return &commonv1.Decimal{Value: strconv.FormatFloat(f, 'f', -1, 64)}
}

// fakeVendor generates deterministic vendor bars (Alpaca shape: daily stamped 00:00 New York).
type fakeVendor struct {
	mu        sync.Mutex
	cal       *calendar.Calendar
	holes     map[string][2]time.Time // symbol -> [from,to) permanently missing
	transient map[string][2]time.Time // symbol -> missing in multi-symbol requests, present when asked alone (repair)
	served    map[string]bool
	calls     int
	failFrom  time.Time // fail once on the first request whose window starts at or after this time
	failed    bool
	starts    []time.Time
}

func (f *fakeVendor) bars(symbols []string, tf mdv1.Timeframe, start, end time.Time) ([]*mdv1.Bar, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	f.starts = append(f.starts, start)
	if !f.failFrom.IsZero() && !f.failed && !start.Before(f.failFrom) {
		f.failed = true
		return nil, errors.New("vendor down")
	}
	var out []*mdv1.Bar
	for _, sym := range symbols {
		for _, s := range f.cal.Sessions(start.Add(-48*time.Hour), end.Add(48*time.Hour)) {
			var opens []time.Time
			if tf == mdv1.Timeframe_TIMEFRAME_1D {
				t, _ := time.ParseInLocation("2006-01-02", s.Date, nyLoc)
				opens = []time.Time{t}
			} else {
				opens = s.BarOpens(TimeframeDur[tf], false)
			}
			for i, op := range opens {
				if op.Before(start) || !op.Before(end) {
					continue
				}
				if h, ok := f.holes[sym]; ok && !op.Before(h[0]) && op.Before(h[1]) {
					continue
				}
				if h, ok := f.transient[sym]; ok && !op.Before(h[0]) && op.Before(h[1]) && len(symbols) > 1 {
					continue
				}
				px := 100 + float64(i)/100
				out = append(out, &mdv1.Bar{Symbol: sym, Timeframe: tf, TsOpen: op.UnixNano(), TsClose: op.Add(TimeframeDur[tf]).UnixNano(),
					Open: d(px), High: d(px + 0.1), Low: d(px - 0.1), Close: d(px + 0.05), Volume: d(1000), Vwap: d(px), TradeCount: 10, Source: "alpaca"})
			}
		}
	}
	return out, nil
}

// fakeBrokerMD serves BrokerMarketDataService over gRPC with pagination (exercises BrokerSource).
type fakeBrokerMD struct {
	brokerv1.UnimplementedBrokerMarketDataServiceServer
	v *fakeVendor
}

func (s *fakeBrokerMD) GetHistoricalBars(_ context.Context, r *brokerv1.GetHistoricalBarsRequest) (*brokerv1.GetHistoricalBarsResponse, error) {
	all, err := s.v.bars(r.GetSymbols(), r.GetTimeframe(), time.Unix(0, r.GetStart()), time.Unix(0, r.GetEnd()))
	if err != nil {
		return nil, err
	}
	off := 0
	if r.GetPageToken() != "" {
		off, _ = strconv.Atoi(r.GetPageToken())
	}
	const page = 500
	end := min(off+page, len(all))
	resp := &brokerv1.GetHistoricalBarsResponse{Bars: all[off:end]}
	if end < len(all) {
		resp.NextPageToken = strconv.Itoa(end)
	}
	return resp, nil
}

func brokerSource(t *testing.T, v *fakeVendor) BrokerSource {
	t.Helper()
	lis := bufconn.Listen(1 << 20)
	gs := grpc.NewServer()
	brokerv1.RegisterBrokerMarketDataServiceServer(gs, &fakeBrokerMD{v: v})
	go func() { _ = gs.Serve(lis) }()
	t.Cleanup(gs.Stop)
	conn, err := grpc.NewClient("passthrough:///x", grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) { return lis.DialContext(ctx) }),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return BrokerSource{Client: brokerv1.NewBrokerMarketDataServiceClient(conn), FeedStr: "alpaca.sip"}
}

func setup(t *testing.T, v *fakeVendor) (*Backfiller, *refdata.Memory, []string) {
	t.Helper()
	cal := calendar.XNYS()
	ref := refdata.NewMemory()
	ctx := context.Background()
	var ids []string
	for i := range 6 {
		id, _, _ := ref.EnsureInstrument(ctx, fmt.Sprintf("SYM%d", i), 0, refdata.Instrument{AssetClass: "us_equity", ListedAt: 1}, "test")
		ids = append(ids, id)
	}
	// A ticker change in the middle of the window: SYM0 becomes NEW0 on 2026-03-04.
	_ = ref.RenameSymbol(ctx, ids[0], "NEW0", time.Date(2026, 3, 4, 5, 0, 0, 0, time.UTC).UnixNano(), "test")
	b := &Backfiller{
		Sources: map[string]Source{"alpaca.sip": brokerSource(t, v)},
		Lake:    lake.New(t.TempDir(), lake.NewMemoryManifest()),
		Norm:    Normalizer{Cal: cal, Ref: ref, Delays: DefaultDelays, Now: time.Now},
		Symbols: ref, Store: NewMemoryStore(), Cal: cal, MinGapRun1m: 5, Metrics: NewMetrics(nil),
	}
	return b, ref, ids
}

func TestMD001BackfillIdempotentWithGapRepairAndTickerChange(t *testing.T) {
	ctx := context.Background()
	start, end := time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), time.Date(2026, 3, 7, 0, 0, 0, 0, time.UTC)
	v := &fakeVendor{cal: calendar.XNYS(), served: map[string]bool{},
		holes:     map[string][2]time.Time{"SYM2": {time.Date(2026, 3, 3, 15, 0, 0, 0, time.UTC), time.Date(2026, 3, 3, 15, 30, 0, 0, time.UTC)}},
		transient: map[string][2]time.Time{"SYM3": {time.Date(2026, 3, 5, 16, 0, 0, 0, time.UTC), time.Date(2026, 3, 5, 16, 20, 0, 0, time.UTC)}}}
	b, _, ids := setup(t, v)
	tfs := []mdv1.Timeframe{mdv1.Timeframe_TIMEFRAME_1M, mdv1.Timeframe_TIMEFRAME_15M, mdv1.Timeframe_TIMEFRAME_1D}
	id, err := b.Submit(ctx, &Job{Source: "alpaca.sip", Timeframes: tfs, Instruments: ids, Start: start, End: end})
	if err != nil {
		t.Fatal(err)
	}
	if err := b.RunJob(ctx, <-b.queue); err != nil {
		t.Fatal(err)
	}
	j, _ := b.Store.LoadJob(ctx, id)
	r := j.Report
	// 6 instruments x 5 sessions: 1m 390/session, 15m 26/session, 1d 1/session; SYM2 misses 30 1m bars, 2 15m bars.
	wantAdded := int64(6*5*(390+26+1) - 30 - 2)
	if r.GetRowsAdded() != wantAdded || r.GetRowsRevised() != 0 {
		t.Fatalf("added %d (want %d), revised %d", r.GetRowsAdded(), wantAdded, r.GetRowsRevised())
	}
	if r.GetGapsRepaired() < 2 || r.GetGapsConfirmedMissing() < 2 || j.State != mdv1.JobState_JOB_STATE_SUCCEEDED {
		t.Fatalf("gaps: found=%d repaired=%d missing=%d state=%s", r.GetGapsFound(), r.GetGapsRepaired(), r.GetGapsConfirmedMissing(), j.State)
	}
	// Rerun of the same backfill adds zero rows (Gate 1).
	id2, _ := b.Submit(ctx, &Job{Source: "alpaca.sip", Timeframes: tfs, Instruments: ids, Start: start, End: end})
	if err := b.RunJob(ctx, <-b.queue); err != nil {
		t.Fatal(err)
	}
	j2, _ := b.Store.LoadJob(ctx, id2)
	if j2.Report.GetRowsAdded() != 0 || j2.Report.GetRowsRevised() != 0 || j2.Report.GetRowsFetched() == 0 {
		t.Fatalf("rerun: added=%d revised=%d fetched=%d", j2.Report.GetRowsAdded(), j2.Report.GetRowsRevised(), j2.Report.GetRowsFetched())
	}
	// Daily bars are aligned to the session (MD-004) and the renamed ticker keeps its instrument.
	part := lake.BarPartition("bars", "alpaca.sip", "1d", start.UnixNano())
	rows, _ := b.Lake.ReadBars(part)
	s, _ := calendar.XNYS().Session("2026-03-02")
	found := 0
	for _, row := range rows {
		if row.InstrumentID == ids[0] {
			found++
			if row.TsOpen == s.Open.UnixNano() && (row.TsClose != s.Close.UnixNano() || row.AvailableAt != s.Close.Add(time.Minute).UnixNano()) {
				t.Fatalf("daily bar alignment/availability: %+v", row)
			}
		}
	}
	if found != 5 {
		t.Fatalf("renamed instrument has %d daily bars, want 5 across the ticker change", found)
	}
}

func TestMD001BackfillResumesFromCheckpoint(t *testing.T) {
	ctx := context.Background()
	start, end := time.Date(2026, 2, 2, 0, 0, 0, 0, time.UTC), time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC)
	v := &fakeVendor{cal: calendar.XNYS(), served: map[string]bool{}, failFrom: start.Add(14 * 24 * time.Hour)}
	b, _, ids := setup(t, v)
	id, _ := b.Submit(ctx, &Job{Source: "alpaca.sip", Timeframes: []mdv1.Timeframe{mdv1.Timeframe_TIMEFRAME_1M}, Instruments: ids, Start: start, End: end})
	if err := b.RunJob(ctx, <-b.queue); err == nil {
		t.Fatal("expected the injected vendor failure")
	}
	j, _ := b.Store.LoadJob(ctx, id)
	if j.State != mdv1.JobState_JOB_STATE_FAILED || j.Report.GetChunksDone() == 0 {
		t.Fatalf("state %s done %d", j.State, j.Report.GetChunksDone())
	}
	v.mu.Lock()
	v.starts = nil
	v.mu.Unlock()
	added := j.Report.GetRowsAdded()
	if err := b.RunJob(ctx, id); err != nil {
		t.Fatal(err)
	}
	j, _ = b.Store.LoadJob(ctx, id)
	if j.State != mdv1.JobState_JOB_STATE_SUCCEEDED || j.Report.GetChunksDone() != j.Report.GetChunksTotal() {
		t.Fatalf("resume: %s %d/%d", j.State, j.Report.GetChunksDone(), j.Report.GetChunksTotal())
	}
	for _, s := range v.starts {
		if s.Before(v.failFrom) {
			t.Fatalf("resume refetched a checkpointed chunk starting %s", s)
		}
	}
	if j.Report.GetRowsAdded() <= added {
		t.Fatal("resume added nothing")
	}
}

func TestMD001SubmitWithIDIsIdempotentAndRejectsVendorHourly(t *testing.T) {
	ctx := context.Background()
	start, end := time.Date(2026, 2, 2, 0, 0, 0, 0, time.UTC), time.Date(2026, 2, 9, 0, 0, 0, 0, time.UTC)
	b, _, ids := setup(t, &fakeVendor{cal: calendar.XNYS(), served: map[string]bool{}})
	job := func() *Job {
		return &Job{ID: "history-x", Source: "alpaca.sip", Timeframes: []mdv1.Timeframe{mdv1.Timeframe_TIMEFRAME_1D}, Instruments: ids, Start: start, End: end}
	}
	for range 2 {
		if id, err := b.Submit(ctx, job()); err != nil || id != "history-x" {
			t.Fatalf("submit: %q %v", id, err)
		}
	}
	if len(b.queue) != 1 {
		t.Fatalf("job queued %d times", len(b.queue))
	}
	hourly := job()
	hourly.ID, hourly.Timeframes = "", []mdv1.Timeframe{mdv1.Timeframe_TIMEFRAME_1H}
	if _, err := b.Submit(ctx, hourly); err == nil {
		t.Fatal("vendor 1h bars accepted")
	}
}

func TestMD003PublicationDelays(t *testing.T) {
	ds, err := ParseDelays("alpaca.sip=90s,fmp/TIMEFRAME_1D=8h")
	if err != nil {
		t.Fatal(err)
	}
	if ds.For("alpaca.sip", mdv1.Timeframe_TIMEFRAME_1M) != 90*time.Second || ds.For("fmp", mdv1.Timeframe_TIMEFRAME_1D) != 8*time.Hour ||
		ds.For("alpaca.iex", mdv1.Timeframe_TIMEFRAME_1M) != 60*time.Second || ds.For("unknown", mdv1.Timeframe_TIMEFRAME_1M) != 60*time.Second {
		t.Fatalf("delays %v", ds)
	}
	if _, err := ParseDelays("bad"); err == nil {
		t.Fatal("bad delay accepted")
	}
}
