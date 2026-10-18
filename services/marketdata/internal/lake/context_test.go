package lake

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"google.golang.org/protobuf/types/known/structpb"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	contextv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/context/v1"
)

func ctxEvent(t *testing.T, key string, avail time.Time, payload map[string]any, ids ...string) *contextv1.ContextEvent {
	t.Helper()
	st, err := structpb.NewStruct(payload)
	if err != nil {
		t.Fatal(err)
	}
	return &contextv1.ContextEvent{
		EventId: "ev-" + key, Kind: contextv1.ContextKind_CONTEXT_KIND_FUNDAMENTALS, InstrumentIds: ids, Source: "fmp", RecordKey: key,
		Provenance: &commonv1.Provenance{EventTime: time.Date(2026, 6, 30, 0, 0, 0, 0, time.UTC).UnixNano(), AvailableAt: avail.UnixNano(), IngestedAt: avail.UnixNano()},
		Payload:    st,
	}
}

func TestCX001InvalidContextEventsRejected(t *testing.T) {
	ok := ctxEvent(t, "k", time.Now(), map[string]any{"x": "1"}, "I1")
	cases := map[string]func(e *contextv1.ContextEvent){
		"kind":         func(e *contextv1.ContextEvent) { e.Kind = contextv1.ContextKind_CONTEXT_KIND_UNSPECIFIED },
		"available_at": func(e *contextv1.ContextEvent) { e.Provenance.AvailableAt = 0 },
		"event_time":   func(e *contextv1.ContextEvent) { e.Provenance.EventTime = 0 },
		"record_key":   func(e *contextv1.ContextEvent) { e.RecordKey = "" },
		"source":       func(e *contextv1.ContextEvent) { e.Source = "" },
	}
	for name, mut := range cases {
		e := ctxEvent(t, "k", time.Now(), map[string]any{"x": "1"}, "I1")
		mut(e)
		if _, err := ContextRowsFromEvent(e, 1); !errors.Is(err, ErrInvalidContext) {
			t.Fatalf("%s: want ErrInvalidContext, got %v", name, err)
		}
	}
	rows, err := ContextRowsFromEvent(ok, 1)
	if err != nil || len(rows) != 1 || rows[0].Kind != "fundamentals" || rows[0].RecordKey != "k" {
		t.Fatalf("valid event: %+v %v", rows, err)
	}
}

func TestCX002OneRowPerInstrumentAndCanonicalPayload(t *testing.T) {
	a := ctxEvent(t, "n1", time.Now(), map[string]any{"b": "2", "a": "1"}, "I1", "I2")
	a.Symbols = []string{"AAA", "BBB"}
	rows, err := ContextRowsFromEvent(a, 1)
	if err != nil || len(rows) != 2 || rows[0].InstrumentID != "I1" || rows[1].Symbol != "BBB" {
		t.Fatalf("rows %+v %v", rows, err)
	}
	if rows[0].PayloadJSON != `{"a":"1","b":"2"}` {
		t.Fatalf("payload not canonical: %s", rows[0].PayloadJSON)
	}
	market := ctxEvent(t, "cpi", time.Now(), map[string]any{"v": "1"})
	rows, _ = ContextRowsFromEvent(market, 1)
	if len(rows) != 1 || rows[0].InstrumentID != "" {
		t.Fatalf("market-wide event must be one row without instrument: %+v", rows)
	}
}

// CX-003: compaction keeps every content change with its own available_at (A, B, A stays three
// versions) and drops redeliveries and unchanged re-fetches.
// CX-003: context-data hashes payloads byte-identically (tbcore.context.canonical); the Python
// test test_cx003_canonical_json_matches_go pins the same digest.
func TestCX003CanonicalPayloadDigest(t *testing.T) {
	e := ctxEvent(t, "k", time.Now(), map[string]any{"b": 100.0, "a": "x<y & z>", "c": []any{1.5, true, nil}, "d": map[string]any{"z": "\u2028"}}, "I1")
	rows, err := ContextRowsFromEvent(e, 1)
	if err != nil {
		t.Fatal(err)
	}
	const wantJSON = `{"a":"x\u003cy \u0026 z\u003e","b":100,"c":[1.5,true,null],"d":{"z":"\u2028"}}`
	if rows[0].PayloadJSON != wantJSON {
		t.Fatalf("payload %s", rows[0].PayloadJSON)
	}
	if rows[0].PayloadSHA256 != "8b51f256dc7659d481e166d1d6d15eb8ed8fa90a084aa81c8a50da2113e395b1" {
		t.Fatalf("digest %s", rows[0].PayloadSHA256)
	}
}

func TestCX003CompactionKeepsChangesDropsRepeats(t *testing.T) {
	ctx := context.Background()
	l := New(t.TempDir(), NewMemoryManifest())
	t0 := time.Date(2026, 7, 1, 12, 0, 0, 0, time.UTC)
	seq := []struct {
		at  time.Duration
		eps string
	}{{0, "1.10"}, {time.Hour, "1.10"}, {2 * time.Hour, "1.12"}, {3 * time.Hour, "1.10"}, {3 * time.Hour, "1.10"}}
	var stage []ContextRow
	for i, s := range seq {
		rs, err := ContextRowsFromEvent(ctxEvent(t, "AAPL|income|2026Q2", t0.Add(s.at), map[string]any{"eps": s.eps}, "I1"), int64(i+1))
		if err != nil {
			t.Fatal(err)
		}
		stage = append(stage, rs...)
	}
	// Deliver out of order across two staging files.
	if err := l.StageContext(ctx, stage[3:]); err != nil {
		t.Fatal(err)
	}
	if err := l.StageContext(ctx, stage[:3]); err != nil {
		t.Fatal(err)
	}
	res, err := l.CompactAllContext(ctx)
	if err != nil || len(res) != 1 {
		t.Fatalf("compact %v %v", res, err)
	}
	rows, _ := l.ReadContext(res[0].Partition)
	if len(rows) != 3 || res[0].Dropped != 2 {
		t.Fatalf("want 3 versions (A,B,A) and 2 dropped, got %d rows dropped=%d", len(rows), res[0].Dropped)
	}
	want := []string{`{"eps":"1.10"}`, `{"eps":"1.12"}`, `{"eps":"1.10"}`}
	for i, r := range rows {
		if r.PayloadJSON != want[i] || r.AvailableAt != t0.Add([]time.Duration{0, 2 * time.Hour, 3 * time.Hour}[i]).UnixNano() {
			t.Fatalf("version %d: %s at %d", i, r.PayloadJSON, r.AvailableAt)
		}
	}
	// Re-running with the same data adds nothing (ST-004 for context).
	if err := l.StageContext(ctx, stage); err != nil {
		t.Fatal(err)
	}
	if _, err := l.CompactAllContext(ctx); err != nil {
		t.Fatal(err)
	}
	if rows, _ = l.ReadContext(res[0].Partition); len(rows) != 3 {
		t.Fatalf("rerun changed the partition: %d rows", len(rows))
	}
}

// TestGenerateContextFixtureLake writes tests/fixtures/lake_context, read by the Python context
// query tests to prove Go-written context files are readable (one compacted part, one staging
// file). Run with TB_UPDATE_FIXTURES=1.
func TestGenerateContextFixtureLake(t *testing.T) {
	if os.Getenv("TB_UPDATE_FIXTURES") != "1" {
		t.Skip("set TB_UPDATE_FIXTURES=1 to regenerate tests/fixtures/lake_context")
	}
	root := "../../../../tests/fixtures/lake_context"
	_ = os.RemoveAll(root)
	ctx := context.Background()
	l := New(root, NewMemoryManifest())
	mk := func(kind contextv1.ContextKind, key string, ev, avail time.Time, vintage string, payload map[string]any, ids ...string) []ContextRow {
		e := ctxEvent(t, key, avail, payload, ids...)
		e.Kind, e.Vintage = kind, vintage
		e.Provenance.EventTime = ev.UnixNano()
		if kind == contextv1.ContextKind_CONTEXT_KIND_MACRO {
			e.Source = "alfred"
		}
		rs, err := ContextRowsFromEvent(e, avail.UnixNano())
		if err != nil {
			t.Fatal(err)
		}
		return rs
	}
	may := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	first := time.Date(2026, 6, 5, 12, 30, 0, 0, time.UTC)
	revised := time.Date(2026, 7, 3, 12, 30, 0, 0, time.UTC)
	var rows []ContextRow
	rows = append(rows, mk(contextv1.ContextKind_CONTEXT_KIND_MACRO, "PAYEMS|2026-05-01", may, first, "2026-06-05",
		map[string]any{"series_id": "PAYEMS", "period": "2026-05-01", "value": "159100"})...)
	if err := l.StageContext(ctx, rows); err != nil {
		t.Fatal(err)
	}
	if _, err := l.CompactAllContext(ctx); err != nil {
		t.Fatal(err)
	}
	rows = mk(contextv1.ContextKind_CONTEXT_KIND_MACRO, "PAYEMS|2026-05-01", may, revised, "2026-07-03",
		map[string]any{"series_id": "PAYEMS", "period": "2026-05-01", "value": "159020"})
	if err := l.StageContext(ctx, rows); err != nil {
		t.Fatal(err)
	}
}
