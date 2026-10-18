package live

import (
	"context"
	"math/rand/v2"
	"sort"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	natsserver "github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/redis/go-redis/v9"
	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/cache"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) Now() time.Time      { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) Set(t time.Time)     { c.mu.Lock(); c.t = t; c.mu.Unlock() }
func (c *clock) Add(d time.Duration) { c.mu.Lock(); c.t = c.t.Add(d); c.mu.Unlock() }

type recSink struct {
	mu          sync.Mutex
	finals      []*mdv1.Bar
	corrections []*mdv1.Bar
	partials    []*mdv1.Bar
}

func (s *recSink) Final(_ context.Context, b *mdv1.Bar) error {
	s.mu.Lock()
	s.finals = append(s.finals, b)
	s.mu.Unlock()
	return nil
}
func (s *recSink) Correction(_ context.Context, b *mdv1.Bar) error {
	s.mu.Lock()
	s.corrections = append(s.corrections, b)
	s.mu.Unlock()
	return nil
}
func (s *recSink) Partial(_ context.Context, b *mdv1.Bar) {
	s.mu.Lock()
	s.partials = append(s.partials, b)
	s.mu.Unlock()
}
func (s *recSink) count(tf mdv1.Timeframe) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, b := range s.finals {
		if b.GetTimeframe() == tf {
			n++
		}
	}
	return n
}

func dd(f float64) *commonv1.Decimal {
	return &commonv1.Decimal{Value: strconv.FormatFloat(f, 'f', -1, 64)}
}

func vbar(sym string, open time.Time, px float64) *mdv1.Bar {
	return &mdv1.Bar{Symbol: sym, Timeframe: mdv1.Timeframe_TIMEFRAME_1M, TsOpen: open.UnixNano(), TsClose: open.Add(time.Minute).UnixNano(),
		Open: dd(px), High: dd(px + 0.2), Low: dd(px - 0.2), Close: dd(px + 0.1), Volume: dd(100), Vwap: dd(px), TradeCount: 3, Feed: "alpaca.sip", Source: "alpaca"}
}

func newPipe(t *testing.T) (*Pipeline, *recSink, *clock) {
	t.Helper()
	ck := &clock{}
	sink := &recSink{}
	p := New(calendar.XNYS(), nil, sink, "alpaca.sip", nil, nil)
	p.Now = ck.Now
	p.SetSymbols(map[string]string{"AAA": "I-AAA", "BBB": "I-BBB"})
	p.SetConnected(true)
	return p, sink, ck
}

var sess = func() calendar.Session { s, _ := calendar.XNYS().Session("2026-03-06"); return s }()

func TestMD002FinalityWindowAndCorrections(t *testing.T) {
	p, sink, ck := newPipe(t)
	ctx := context.Background()
	op := sess.Open.Add(10 * time.Minute)
	ck.Set(op.Add(61 * time.Second))
	p.OnBar(ctx, vbar("AAA", op, 100))
	if len(sink.partials) != 1 || sink.partials[0].GetIsFinal() {
		t.Fatal("bar must be published as a non-final partial first")
	}
	ck.Set(op.Add(time.Minute + 30*time.Second))
	upd := vbar("AAA", op, 100)
	upd.Close = dd(100.15) // late trade within the updatedBars window
	p.OnUpdatedBar(ctx, upd)
	ck.Set(op.Add(time.Minute + 59*time.Second))
	p.Tick(ctx)
	if sink.count(mdv1.Timeframe_TIMEFRAME_1M) != 0 {
		t.Fatal("final before ts_close + 60 s")
	}
	ck.Set(op.Add(2 * time.Minute))
	p.Tick(ctx)
	if sink.count(mdv1.Timeframe_TIMEFRAME_1M) != 1 {
		t.Fatal("not final at ts_close + 60 s")
	}
	f := sink.finals[0]
	if !f.GetIsFinal() || f.GetRevision() != 0 || f.GetClose().GetValue() != "100.15" || f.GetAvailableAt() != op.Add(2*time.Minute).UnixNano() ||
		f.GetInstrumentId() != "I-AAA" || f.GetSession() != mdv1.Session_SESSION_REGULAR {
		t.Fatalf("final bar %+v", f)
	}
	// A correction after finality: revision 1 on the correction subject (DF-007), never a new final.
	ck.Set(op.Add(5 * time.Minute))
	late := vbar("AAA", op, 100)
	late.Close = dd(100.2)
	p.OnUpdatedBar(ctx, late)
	if len(sink.corrections) == 0 || sink.corrections[0].GetRevision() != 1 || sink.corrections[0].GetAvailableAt() != op.Add(5*time.Minute).UnixNano() {
		t.Fatalf("corrections %+v", sink.corrections)
	}
	if sink.count(mdv1.Timeframe_TIMEFRAME_1M) != 1 {
		t.Fatal("a correction must not create a second final bar")
	}
	p.OnUpdatedBar(ctx, late) // identical correction is ignored
	if len(sink.corrections) != 1 {
		t.Fatal("duplicate correction published")
	}
}

func TestMD004ResampledBarsEqualOracle(t *testing.T) {
	p, sink, ck := newPipe(t)
	ctx := context.Background()
	rng := rand.New(rand.NewPCG(1, 2)) //nolint:gosec // test data
	var minutes []*mdv1.Bar
	for t0 := sess.PreOpen; t0.Before(sess.PostClose); t0 = t0.Add(time.Minute) {
		if rng.Float64() < 0.05 {
			continue // minutes without trades
		}
		b := vbar("BBB", t0, 50+rng.Float64()*5)
		b.Volume = dd(float64(1 + rng.IntN(1000)))
		minutes = append(minutes, b)
	}
	for _, b := range minutes {
		ck.Set(time.Unix(0, b.GetTsClose()).Add(time.Second))
		p.OnBar(ctx, b)
		ck.Set(time.Unix(0, b.GetTsClose()).Add(61 * time.Second))
		p.Tick(ctx)
	}
	ck.Set(sess.PostClose.Add(2 * time.Minute))
	p.Tick(ctx)
	for _, tf := range []struct {
		tf mdv1.Timeframe
		d  time.Duration
	}{{mdv1.Timeframe_TIMEFRAME_5M, 5 * time.Minute}, {mdv1.Timeframe_TIMEFRAME_15M, 15 * time.Minute}, {mdv1.Timeframe_TIMEFRAME_1H, time.Hour}} {
		// Independent oracle: group minutes by session part and bucket.
		type agg struct {
			o, h, l, c, v, pv decimal.Decimal
			first, last       int64
		}
		want := map[int64]*agg{}
		closes := map[int64]int64{}
		for _, m := range minutes {
			mt := time.Unix(0, m.GetTsOpen()).UTC()
			base := sess.Open
			if mt.Before(sess.Open) {
				base = sess.PreOpen
			} else if !mt.Before(sess.Close) {
				base = sess.Close
			}
			bo := base.Add(mt.Sub(base) / tf.d * tf.d)
			closes[bo.UnixNano()] = sess.BarClose(bo, tf.d).UnixNano()
			px := func(x *commonv1.Decimal) decimal.Decimal { v, _ := decimal.NewFromString(x.GetValue()); return v }
			a := want[bo.UnixNano()]
			if a == nil {
				a = &agg{o: px(m.GetOpen()), h: px(m.GetHigh()), l: px(m.GetLow()), first: m.GetTsOpen()}
				want[bo.UnixNano()] = a
			}
			a.h, a.l = decimal.Max(a.h, px(m.GetHigh())), decimal.Min(a.l, px(m.GetLow()))
			a.c, a.last = px(m.GetClose()), m.GetTsOpen()
			a.v = a.v.Add(px(m.GetVolume()))
			a.pv = a.pv.Add(px(m.GetVwap()).Mul(px(m.GetVolume())))
		}
		got := map[int64]*mdv1.Bar{}
		for _, b := range sink.finals {
			if b.GetTimeframe() == tf.tf {
				if _, dup := got[b.GetTsOpen()]; dup {
					t.Fatalf("%s: duplicate final bucket", tf.tf)
				}
				got[b.GetTsOpen()] = b
			}
		}
		if len(got) != len(want) {
			t.Fatalf("%s: %d buckets, oracle %d", tf.tf, len(got), len(want))
		}
		for open, a := range want {
			b := got[open]
			if b == nil || b.GetOpen().GetValue() != a.o.String() || b.GetHigh().GetValue() != a.h.String() || b.GetLow().GetValue() != a.l.String() ||
				b.GetClose().GetValue() != a.c.String() || b.GetVolume().GetValue() != a.v.String() || b.GetTsClose() != closes[open] ||
				b.GetVwap().GetValue() != a.pv.DivRound(a.v, 6).String() {
				t.Fatalf("%s bucket %s: got %+v", tf.tf, time.Unix(0, open).UTC(), b)
			}
		}
	}
	// 1h regular buckets are aligned to 09:30 and never cross the 16:00 boundary.
	for _, b := range sink.finals {
		if b.GetTimeframe() == mdv1.Timeframe_TIMEFRAME_1H && b.GetSession() == mdv1.Session_SESSION_REGULAR {
			if (time.Unix(0, b.GetTsOpen()).Sub(sess.Open))%time.Hour != 0 || b.GetTsClose() > sess.Close.UnixNano() {
				t.Fatalf("1h bar misaligned: %s-%s", time.Unix(0, b.GetTsOpen()).UTC(), time.Unix(0, b.GetTsClose()).UTC())
			}
		}
	}
}

type histSrc struct {
	bars []*mdv1.Bar
}

func (h histSrc) Bars(_ context.Context, syms []string, _ mdv1.Timeframe, start, end time.Time) ([]*mdv1.Bar, error) {
	var out []*mdv1.Bar
	for _, b := range h.bars {
		if b.GetTsOpen() >= start.UnixNano() && b.GetTsOpen() < end.UnixNano() {
			out = append(out, b)
		}
	}
	return out, nil
}

func TestMD006StreamKilledThreeMinutesIsRepairedOnce(t *testing.T) {
	p, sink, ck := newPipe(t)
	ctx := context.Background()
	start := sess.Open.Add(30 * time.Minute)
	var all []*mdv1.Bar
	for i := range 10 {
		all = append(all, vbar("AAA", start.Add(time.Duration(i)*time.Minute), 100+float64(i)))
	}
	// Minutes 0-2 stream normally; the stream dies for minutes 3-5; minutes 6-9 stream after.
	feed := func(i int) {
		b := all[i]
		ck.Set(time.Unix(0, b.GetTsClose()).Add(time.Second))
		p.OnBar(ctx, b)
		ck.Set(time.Unix(0, b.GetTsClose()).Add(61 * time.Second))
		p.Tick(ctx)
	}
	for i := 0; i < 3; i++ {
		feed(i)
	}
	p.SetConnected(false)
	if fresh, _, _ := p.IsFresh("I-AAA", mdv1.Timeframe_TIMEFRAME_1M, 0); fresh {
		t.Fatal("IsFresh must be false while disconnected (MD-007)")
	}
	ck.Set(start.Add(6*time.Minute + 30*time.Second))
	if !p.SetConnected(true) {
		t.Fatal("reconnect not detected")
	}
	n, err := p.Repair(ctx, histSrc{bars: all}, time.Unix(0, p.EarliestLastFinal()))
	if err != nil || n != 3 {
		t.Fatalf("repaired %d (want 3, minutes 3-5): %v", n, err)
	}
	ck.Set(start.Add(7 * time.Minute)) // minute 5's finality window ends
	p.Tick(ctx)
	for i := 6; i < 10; i++ {
		feed(i)
	}
	seen := map[int64]int{}
	repaired := 0
	for _, b := range sink.finals {
		if b.GetTimeframe() == mdv1.Timeframe_TIMEFRAME_1M {
			seen[b.GetTsOpen()]++
			if b.GetRepaired() {
				repaired++
			}
		}
	}
	if len(seen) != 10 || repaired != 3 {
		t.Fatalf("minutes %d repaired %d", len(seen), repaired)
	}
	for ts, c := range seen {
		if c != 1 {
			t.Fatalf("minute %s published %d times", time.Unix(0, ts).UTC(), c)
		}
	}
	// The 5m bucket spanning the gap is marked repaired.
	for _, b := range sink.finals {
		if b.GetTimeframe() == mdv1.Timeframe_TIMEFRAME_5M && b.GetTsOpen() == start.UnixNano() && !b.GetRepaired() {
			t.Fatal("bucket containing repaired minutes must be flagged repaired")
		}
	}
}

func TestMD007IsFresh(t *testing.T) {
	p, _, ck := newPipe(t)
	ctx := context.Background()
	op := sess.Open.Add(time.Hour)
	ck.Set(op.Add(61 * time.Second))
	p.OnBar(ctx, vbar("AAA", op, 10))
	ck.Set(op.Add(2 * time.Minute))
	p.Tick(ctx)
	if ok, last, why := p.IsFresh("I-AAA", mdv1.Timeframe_TIMEFRAME_1M, 0); !ok || last != op.Add(time.Minute).UnixNano() {
		t.Fatalf("fresh right after a final bar: %v %s", ok, why)
	}
	ck.Set(op.Add(time.Minute + 2*time.Minute + 61*time.Second)) // > 2 intervals + grace after close
	if ok, _, why := p.IsFresh("I-AAA", mdv1.Timeframe_TIMEFRAME_1M, 0); ok || why == "" {
		t.Fatal("stalled symbol must not be fresh")
	}
	if got := p.StaleSymbols(2 * time.Minute); len(got) != 2 {
		t.Fatalf("stale symbols %v", got)
	}
	if ok, _, _ := p.IsFresh("I-UNKNOWN", mdv1.Timeframe_TIMEFRAME_1M, 0); ok {
		t.Fatal("unknown instrument must fail closed")
	}
	ck.Set(time.Date(2026, 3, 7, 15, 0, 0, 0, time.UTC)) // Saturday
	if ok, _, why := p.IsFresh("I-AAA", mdv1.Timeframe_TIMEFRAME_1M, 0); ok || why != "outside trading session" {
		t.Fatalf("weekend: %v %s", ok, why)
	}
}

// TestU8BarFinalToBusLatency measures the hand-off from the finality decision to an acknowledged
// JetStream publish plus cache write and lake buffer, against a real embedded NATS JetStream server
// (file storage, sync always) and a Redis-protocol server. U8: p99 under 500 ms.
func TestU8BarFinalToBusLatency(t *testing.T) {
	opts := &natsserver.Options{Host: "127.0.0.1", Port: -1, JetStream: true, StoreDir: t.TempDir(), NoLog: true, NoSigs: true, SyncAlways: true}
	ns, err := natsserver.NewServer(opts)
	if err != nil {
		t.Fatal(err)
	}
	go ns.Start()
	if !ns.ReadyForConnections(5 * time.Second) {
		t.Fatal("nats not ready")
	}
	defer ns.Shutdown()
	nc, err := nats.Connect(ns.ClientURL())
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	ctx := context.Background()
	if err := bus.EnsureStreams(ctx, nc); err != nil {
		t.Fatal(err)
	}
	js, _ := jetstream.New(nc)
	mr := miniredis.RunT(t)
	ls := &LakeSink{L: lake.New(t.TempDir(), lake.NewMemoryManifest())}
	sink := Multi{BusSink{JS: js}, CacheSink{C: &cache.Client{R: redis.NewClient(&redis.Options{Addr: mr.Addr()})}}, ls}
	ck := &clock{}
	p := New(calendar.XNYS(), nil, sink, "alpaca.sip", nil, nil)
	p.Now = ck.Now
	var lat []time.Duration
	p.Observe = func(b *mdv1.Bar, d time.Duration) {
		if b.GetTimeframe() == mdv1.Timeframe_TIMEFRAME_1M {
			lat = append(lat, d)
		}
	}
	syms := map[string]string{}
	for i := range 300 {
		syms["S"+strconv.Itoa(i)] = "I-" + strconv.Itoa(i)
	}
	p.SetSymbols(syms)
	p.SetConnected(true)
	sub, _ := js.CreateOrUpdateConsumer(ctx, "MARKETDATA", jetstream.ConsumerConfig{FilterSubject: "md.bar.1m.>", AckPolicy: jetstream.AckNonePolicy})
	for minute := range 5 {
		op := sess.Open.Add(time.Duration(minute) * time.Minute)
		ck.Set(op.Add(61 * time.Second))
		for sym := range syms {
			p.OnBar(ctx, vbar(sym, op, 100))
		}
		ck.Set(op.Add(2 * time.Minute))
		p.Tick(ctx)
	}
	msgs, _ := sub.Fetch(2000, jetstream.FetchMaxWait(2*time.Second))
	got := 0
	for range msgs.Messages() {
		got++
	}
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	p99 := lat[len(lat)*99/100]
	t.Logf("U8: %d final 1m bars (300 symbols x 5 minutes; 5m buckets too) finality-to-bus-ack p50=%v p99=%v max=%v; bus received %d", len(lat), lat[len(lat)/2], p99, lat[len(lat)-1], got)
	if got < 1500 {
		t.Fatalf("bus received %d of 1500 final 1m bars", got)
	}
	// U8 is asserted without the race detector (make perf runs it); under -race on shared CI
	// runners only a gross regression fails.
	limit := 500 * time.Millisecond
	if raceEnabled {
		limit = 5 * time.Second
	}
	if p99 > limit {
		t.Fatalf("p99 %v exceeds %v (U8 target 500 ms without -race)", p99, limit)
	}
	if err := ls.Flush(ctx); err != nil {
		t.Fatal(err)
	}
}
