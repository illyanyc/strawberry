package lake

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"testing"
	"time"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
)

func dec(f float64) *commonv1.Decimal {
	return &commonv1.Decimal{Value: strconv.FormatFloat(f, 'f', -1, 64)}
}

var t0 = time.Date(2026, 3, 2, 14, 30, 0, 0, time.UTC)

func bar(inst string, i int, px float64, avail time.Duration) *mdv1.Bar {
	open := t0.Add(time.Duration(i) * time.Minute)
	return &mdv1.Bar{InstrumentId: inst, Symbol: inst, Timeframe: mdv1.Timeframe_TIMEFRAME_1M, TsOpen: open.UnixNano(),
		TsClose: open.Add(time.Minute).UnixNano(), Open: dec(px), High: dec(px + 0.5), Low: dec(px - 0.5), Close: dec(px + 0.1),
		Volume: dec(1000 + float64(i)), Vwap: dec(px), TradeCount: int64(10 + i), Feed: "alpaca.sip", Source: "alpaca", IsFinal: true,
		Session: mdv1.Session_SESSION_REGULAR, AdjustmentBasis: mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW,
		AvailableAt: open.Add(time.Minute + avail).UnixNano(), IngestedAt: open.Add(2 * time.Minute).UnixNano()}
}

func series(n int) []*mdv1.Bar {
	var out []*mdv1.Bar
	for _, inst := range []string{"INSTB", "INSTA"} {
		for i := range n {
			out = append(out, bar(inst, i, 100+float64(i), time.Minute))
		}
	}
	return out
}

func TestDFI2IdempotentWritesAndCompaction(t *testing.T) {
	ctx := context.Background()
	l := New(t.TempDir(), NewMemoryManifest())
	wm, err := l.StageBars(ctx, series(120))
	if err != nil {
		t.Fatal(err)
	}
	part := BarPartition("bars", "alpaca.sip", "1m", t0.UnixNano())
	if len(wm) != 1 || wm[part] != t0.Add(119*time.Minute).UnixNano() {
		t.Fatalf("watermarks %v", wm)
	}
	r1, err := l.Compact(ctx, part)
	if err != nil || r1.Added != 240 || r1.Rows != 240 {
		t.Fatalf("first compaction %+v %v", r1, err)
	}
	// Rerun of the same backfill: zero rows added (Gate 1 "rerun adds zero rows").
	if _, err := l.StageBars(ctx, series(120)); err != nil {
		t.Fatal(err)
	}
	r2, err := l.Compact(ctx, part)
	if err != nil || r2.Added != 0 || r2.Revised != 0 || r2.Unchanged != 240 || r2.Rows != 240 {
		t.Fatalf("rerun compaction %+v %v", r2, err)
	}
	rows, err := l.ReadBars(part)
	if err != nil || len(rows) != 240 {
		t.Fatalf("read %d %v", len(rows), err)
	}
	// Sort order: instrument, then time.
	if !sort.SliceIsSorted(rows, func(i, j int) bool {
		if rows[i].InstrumentID != rows[j].InstrumentID {
			return rows[i].InstrumentID < rows[j].InstrumentID
		}
		return rows[i].TsOpen < rows[j].TsOpen
	}) {
		t.Fatal("compacted file not sorted")
	}
	parts, _ := l.listFiles(part, "")
	if len(parts) != 1 {
		t.Fatalf("expected one part file after compaction, got %v", parts)
	}
	res, err := l.Verify(ctx)
	if err != nil || !res.OK() || res.Checked != 1 {
		t.Fatalf("verify %+v %v", res, err)
	}
}

func TestDF007CorrectionsKeepBothVersions(t *testing.T) {
	ctx := context.Background()
	l := New(t.TempDir(), NewMemoryManifest())
	orig := bar("INSTA", 0, 100, 30*time.Second)
	if _, err := l.StageBars(ctx, []*mdv1.Bar{orig}); err != nil {
		t.Fatal(err)
	}
	part := BarPartition("bars", "alpaca.sip", "1m", orig.GetTsOpen())
	if _, err := l.Compact(ctx, part); err != nil {
		t.Fatal(err)
	}
	corr := bar("INSTA", 0, 100, 2*time.Hour)
	corr.Close = dec(100.25) // late trade changed the close (updatedBars / nightly re-pull)
	if _, err := l.StageBars(ctx, []*mdv1.Bar{corr}); err != nil {
		t.Fatal(err)
	}
	r, err := l.Compact(ctx, part)
	if err != nil || r.Revised != 1 {
		t.Fatalf("%+v %v", r, err)
	}
	rows, _ := l.ReadBars(part)
	if len(rows) != 1 || rows[0].Revision != 1 || rows[0].Close != 100.25 {
		t.Fatalf("latest %+v", rows)
	}
	revPart := "bar_revisions" + part[len("bars"):]
	names, _ := l.listFiles(revPart, "part-")
	if len(names) != 1 {
		t.Fatalf("revisions files %v", names)
	}
	old, _ := readRows[BarRow](l.Root, revPart, names[0])
	if len(old) != 1 || old[0].Revision != 0 || old[0].Close != 100.1 {
		t.Fatalf("superseded version not retained: %+v", old)
	}
}

func TestDF007PartialAndInvalidBarsRejected(t *testing.T) {
	l := New(t.TempDir(), nil)
	p := bar("X", 0, 1, time.Minute)
	p.IsFinal = false
	if _, err := l.StageBars(context.Background(), []*mdv1.Bar{p}); err == nil {
		t.Fatal("partial bar written")
	}
	early := bar("X", 0, 1, time.Minute)
	early.AvailableAt = early.GetTsOpen()
	if _, err := l.StageBars(context.Background(), []*mdv1.Bar{early}); err == nil {
		t.Fatal("bar available before its close accepted (DF-002)")
	}
	adj := bar("X", 0, 1, time.Minute)
	adj.AdjustmentBasis = mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_SPLIT
	if _, err := l.StageBars(context.Background(), []*mdv1.Bar{adj}); err == nil {
		t.Fatal("adjusted bar stored (DF-005)")
	}
}

func TestST003VerifyDetectsCorruption(t *testing.T) {
	ctx := context.Background()
	l := New(t.TempDir(), NewMemoryManifest())
	if _, err := l.StageBars(ctx, series(5)); err != nil {
		t.Fatal(err)
	}
	if _, err := l.CompactAll(ctx); err != nil {
		t.Fatal(err)
	}
	live, _ := l.Manifest.Live(ctx)
	if err := os.WriteFile(filepath.Join(l.Root, live[0].Path), []byte("garbage"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(l.Root, "bars", "stray.parquet"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	res, _ := l.Verify(ctx)
	if len(res.Corrupt) != 1 || len(res.Untracked) != 1 {
		t.Fatalf("verify %+v", res)
	}
}

func TestST008RetentionNeverPurgesBars(t *testing.T) {
	l := New(t.TempDir(), NewMemoryManifest())
	if _, err := l.PurgeOlderThan(context.Background(), "bars", time.Hour, time.Now()); err == nil {
		t.Fatal("bars purged")
	}
	dir := filepath.Join(l.Root, "quotes", "feed=alpaca.sip", "date=2026-01-01")
	_ = os.MkdirAll(dir, 0o750)
	_ = os.WriteFile(filepath.Join(dir, "part-x.parquet"), []byte("x"), 0o600)
	n, err := l.PurgeOlderThan(context.Background(), "quotes", 90*24*time.Hour, time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC))
	if err != nil || n != 1 {
		t.Fatalf("purge %d %v", n, err)
	}
}

func TestContextSnapshotsAppendOnly(t *testing.T) {
	ctx := context.Background()
	l := New(t.TempDir(), NewMemoryManifest())
	ev := time.Date(2025, 12, 31, 0, 0, 0, 0, time.UTC).UnixNano()
	for i, payload := range []string{`{"eps":1.10}`, `{"eps":1.12}`} { // vendor restated in place
		at := time.Date(2026, 2, 1+i, 0, 0, 0, 0, time.UTC).UnixNano()
		if err := l.StageContext(ctx, []ContextRow{{Kind: "fundamentals", InstrumentID: "INSTA", EventTime: ev, AvailableAt: at, IngestedAt: at, Source: "fmp", PayloadJSON: payload}}); err != nil {
			t.Fatal(err)
		}
	}
	names, _ := l.listFiles(ContextPartition("fundamentals", ev), "staging-")
	if len(names) != 2 {
		t.Fatalf("each fetch must be its own snapshot row: %v", names)
	}
}

// TestGenerateFixtureLake writes tests/fixtures/lake (read by the Python query-layer tests to prove
// Go-written files are readable by DuckDB/Polars). Run with TB_UPDATE_FIXTURES=1.
func TestGenerateFixtureLake(t *testing.T) {
	if os.Getenv("TB_UPDATE_FIXTURES") != "1" {
		t.Skip("set TB_UPDATE_FIXTURES=1 to regenerate tests/fixtures/lake")
	}
	root := "../../../../tests/fixtures/lake"
	_ = os.RemoveAll(root)
	ctx := context.Background()
	l := New(root, NewMemoryManifest())
	var bars []*mdv1.Bar
	for i := range 30 {
		bars = append(bars, bar("01FIXTUREAAAAAAAAAAAAAAAAA", i, 50+float64(i)/10, time.Minute))
	}
	if _, err := l.StageBars(ctx, bars); err != nil {
		t.Fatal(err)
	}
	if _, err := l.CompactAll(ctx); err != nil {
		t.Fatal(err)
	}
	corr := bar("01FIXTUREAAAAAAAAAAAAAAAAA", 3, 50.3, 3*time.Hour)
	corr.Close = dec(99.99)
	if _, err := l.StageBars(ctx, []*mdv1.Bar{corr}); err != nil {
		t.Fatal(err)
	}
	if _, err := l.CompactAll(ctx); err != nil {
		t.Fatal(err)
	}
	staged := bar("01FIXTUREAAAAAAAAAAAAAAAAA", 30, 53, time.Minute)
	if _, err := l.StageBars(ctx, []*mdv1.Bar{staged}); err != nil {
		t.Fatal(err)
	}
}
