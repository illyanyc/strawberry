package refdata

import (
	"context"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

func ts(s string) int64 {
	t, _ := time.Parse("2006-01-02", s)
	return t.UnixNano()
}

func TestDF006TickerChangesRecyclingAndDelisting(t *testing.T) {
	ctx := context.Background()
	m := NewMemory()
	meta, _, _ := m.EnsureInstrument(ctx, "FB", ts("2012-05-18"), Instrument{AssetClass: "us_equity", ListedAt: ts("2012-05-18")}, "test")
	if err := m.RenameSymbol(ctx, meta, "META", ts("2022-06-09"), "test"); err != nil {
		t.Fatal(err)
	}
	if id, _ := m.Resolve(ctx, "FB", ts("2020-01-02")); id != meta {
		t.Fatal("FB in 2020 must resolve to the same instrument")
	}
	if id, _ := m.Resolve(ctx, "META", ts("2024-01-02")); id != meta {
		t.Fatal("META in 2024 must resolve to the same instrument (VP-010)")
	}
	if _, err := m.Resolve(ctx, "META", ts("2020-01-02")); err == nil {
		t.Fatal("META did not exist in 2020")
	}
	old, _, _ := m.EnsureInstrument(ctx, "XYZQ", ts("2010-01-04"), Instrument{AssetClass: "us_equity", ListedAt: ts("2010-01-04")}, "test")
	if err := m.SetUniverse(ctx, "dev", []string{meta, old}, ts("2015-01-02")); err != nil {
		t.Fatal(err)
	}
	if err := m.MarkDelisted(ctx, old, ts("2022-03-01")); err != nil {
		t.Fatal(err)
	}
	// Ticker recycled by a new company: new instrument_id.
	fresh, created, _ := m.EnsureInstrument(ctx, "XYZQ", ts("2024-06-03"), Instrument{AssetClass: "us_equity", ListedAt: ts("2024-06-03")}, "test")
	if !created || fresh == old {
		t.Fatal("recycled ticker must get a new instrument_id")
	}
	if got := m.UniverseAt(ctx, "dev", ts("2021-06-01")); len(got) != 2 {
		t.Fatalf("delisted member must be present while listed: %v", got)
	}
	if got := m.UniverseAt(ctx, "dev", ts("2023-06-01")); len(got) != 1 || got[0] != meta {
		t.Fatalf("delisted member must be gone after delisting: %v", got)
	}
	if h := m.History(ctx, meta); len(h) != 2 || h[0].Symbol != "FB" || h[1].Symbol != "META" {
		t.Fatalf("history %+v", h)
	}
}

func TestMD011CorporateActionAvailability(t *testing.T) {
	ex, decl, fetch := ts("2024-06-10"), ts("2024-05-22"), ts("2026-10-17")
	if AvailableAt(decl, ex, fetch) != decl {
		t.Fatal("declared actions are known from the declaration")
	}
	if AvailableAt(0, ex, fetch) != ex {
		t.Fatal("backfilled actions without a declaration are known from the ex-date")
	}
	early := ts("2024-06-01")
	if AvailableAt(0, ex, early) != early {
		t.Fatal("an action fetched before its ex-date is known from the fetch")
	}
	m := NewMemory()
	a := CorporateAction{Source: "alpaca", SourceID: "fs-1", Type: "split", Ratio: "10", ExDate: ex, AvailableAt: ex, IngestedAt: fetch}
	isNew, _ := m.UpsertAction(context.Background(), a)
	a.AvailableAt = fetch
	again, _ := m.UpsertAction(context.Background(), a)
	if !isNew || again || m.Actions["alpaca|fs-1"].AvailableAt != ex {
		t.Fatal("upsert must be idempotent and keep the earliest available_at")
	}
}

func TestRefdataPostgresRoundTrip(t *testing.T) {
	super := os.Getenv("TB_TEST_PG_SUPER_DSN")
	if super == "" {
		t.Skip("TB_TEST_PG_SUPER_DSN not set")
	}
	ctx := context.Background()
	admin, err := pgx.Connect(ctx, super)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = admin.Exec(ctx, "DROP DATABASE IF EXISTS refdata_test WITH (FORCE)")
	admin.Close(ctx)
	pw := map[string]string{}
	for _, r := range append(append([]string{}, db.LoginRoles...), db.AppDatabases...) {
		pw[r] = "pw-" + r
	}
	if err := db.Bootstrap(ctx, super, "refdata_test", pw); err != nil {
		t.Fatal(err)
	}
	cfg, _ := pgx.ParseConfig(super)
	c := db.Config{Host: cfg.Host, Port: int(cfg.Port), Name: "refdata_test", User: "svc_marketdata", SSLMode: "disable"}
	pool, err := pgxpool.New(ctx, c.DSN(pw["svc_marketdata"]))
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	p, err := LoadPG(ctx, pool)
	if err != nil {
		t.Fatal(err)
	}
	id, _, _ := p.EnsureInstrument(ctx, "AAPL", ts("2020-01-02"), Instrument{AssetClass: "us_equity", ListedAt: ts("1980-12-12"), SectorETF: "XLK"}, "alpaca")
	_ = p.UpsertBrokerID(ctx, BrokerID{InstrumentID: id, Broker: "alpaca", BrokerInstrumentID: "b0b6dd9d", ValidFrom: ts("2020-01-02")})
	_, _ = p.UpsertAction(ctx, CorporateAction{Source: "alpaca", SourceID: "fs-1", InstrumentID: id, Symbol: "AAPL", Type: "split", Ratio: "4", ExDate: ts("2020-08-31"), AvailableAt: ts("2020-07-30"), IngestedAt: ts("2026-10-17"), Agreement: "agreed"})
	_ = p.SetUniverse(ctx, "dev", []string{id}, ts("2020-01-02"))
	if err := p.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	if err := p.Flush(ctx); err != nil {
		t.Fatal("flush must be idempotent: ", err)
	}
	q, err := LoadPG(ctx, pool)
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := q.Resolve(ctx, "AAPL", ts("2024-01-02")); got != id {
		t.Fatal("instrument did not round-trip")
	}
	if got, _ := q.ResolveBroker(ctx, "alpaca", "b0b6dd9d", ts("2024-01-02")); got != id {
		t.Fatal("broker id did not round-trip")
	}
	if a := q.ActionsFor(ctx, id); len(a) != 1 || a[0].Ratio != "4" || a[0].Agreement != "agreed" {
		t.Fatalf("action %+v", a)
	}
	n, err := MirrorCalendar(ctx, pool, calendar.XNYS())
	if err != nil || n < 6000 {
		t.Fatalf("calendar mirror %d %v", n, err)
	}
	l := lake.New(t.TempDir(), lake.NewMemoryManifest())
	if err := q.Export(ctx, l); err != nil {
		t.Fatal(err)
	}
}
