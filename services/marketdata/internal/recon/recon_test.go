package recon

import (
	"context"
	"encoding/json"
	"strconv"
	"testing"
	"time"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/fmp"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

func TestMD010Tolerances(t *testing.T) {
	base := Daily{Open: 100, High: 101, Low: 99, Close: 100, Volume: 1_000_000}
	cases := []struct {
		name   string
		p      Daily
		vol    bool
		status string
	}{
		{"identical", base, true, "ok"},
		{"close within one tick", Daily{Open: 100, High: 101, Low: 99, Close: 100.01, Volume: 1_000_000}, true, "ok"},
		{"close off 0.1%", Daily{Open: 100, High: 101, Low: 99, Close: 100.1, Volume: 1_000_000}, true, "breach"},
		{"high off 0.2%", Daily{Open: 100, High: 101.2, Low: 99, Close: 100, Volume: 1_000_000}, true, "breach"},
		{"volume off 4%", Daily{Open: 100, High: 101, Low: 99, Close: 100, Volume: 960_000}, true, "ok"},
		{"volume off 10%", Daily{Open: 100, High: 101, Low: 99, Close: 100, Volume: 900_000}, true, "breach"},
		{"IEX volume never compared", Daily{Open: 100, High: 101, Low: 99, Close: 100, Volume: 30_000}, false, "ok"},
	}
	for _, c := range cases {
		if got, _, _, _, _, _ := Compare(Default, c.p, base, c.vol); got != c.status {
			t.Errorf("%s: %s want %s", c.name, got, c.status)
		}
	}
}

type fakeEOD map[string][]fmp.EOD

func (f fakeEOD) NonSplitAdjustedEOD(_ context.Context, symbol string, _, _ time.Time) ([]fmp.EOD, error) {
	return f[symbol], nil
}

func dec(v float64) *commonv1.Decimal {
	return &commonv1.Decimal{Value: strconv.FormatFloat(v, 'f', -1, 64)}
}

func TestMD010BreachQuarantinesSymbolDay(t *testing.T) {
	ctx := context.Background()
	cal := calendar.XNYS()
	l := lake.New(t.TempDir(), lake.NewMemoryManifest())
	var bars []*mdv1.Bar
	days := []string{"2026-03-02", "2026-03-03"}
	for _, inst := range []string{"I-GOOD", "I-BAD"} {
		for _, dd := range days {
			s, _ := cal.Session(dd)
			bars = append(bars, &mdv1.Bar{InstrumentId: inst, Timeframe: mdv1.Timeframe_TIMEFRAME_1D, TsOpen: s.Open.UnixNano(), TsClose: s.Close.UnixNano(),
				Open: dec(100), High: dec(101), Low: dec(99), Close: dec(100), Volume: dec(1e6), Vwap: dec(100), Feed: "alpaca.sip", Source: "alpaca",
				IsFinal: true, AvailableAt: s.Close.Add(time.Minute).UnixNano(), IngestedAt: s.Close.Add(time.Hour).UnixNano()})
		}
	}
	if _, err := l.StageBars(ctx, bars); err != nil {
		t.Fatal(err)
	}
	if _, err := l.CompactAll(ctx); err != nil {
		t.Fatal(err)
	}
	ok := func(d string, c float64) fmp.EOD {
		return fmp.EOD{Date: d, Open: json.Number("100"), High: json.Number("101"), Low: json.Number("99"), Close: json.Number(strconv.FormatFloat(c, 'f', -1, 64)), Volume: json.Number("1000000")}
	}
	store := NewMemoryStore()
	var alerts []string
	r := &Reconciler{Lake: l, Cal: cal, Store: store, Tol: Default, PrimaryFeed: "alpaca.sip",
		EOD:      fakeEOD{"GOOD": {ok("2026-03-02", 100), ok("2026-03-03", 100)}, "BAD": {ok("2026-03-02", 100), ok("2026-03-03", 103.5)}},
		SymbolAt: func(_ context.Context, id string, _ int64) string { return id[2:] },
		Alert:    func(_ context.Context, _, msg string) { alerts = append(alerts, msg) }}
	sum, err := r.Run(ctx, []string{"I-GOOD", "I-BAD"}, time.Date(2026, 3, 2, 0, 0, 0, 0, time.UTC), time.Date(2026, 3, 3, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if sum.Checked != 4 || sum.OK != 3 || sum.Breaches != 1 || len(alerts) != 1 {
		t.Fatalf("summary %+v alerts %v", sum, alerts)
	}
	q, _ := store.Quarantined(ctx)
	if len(q) != 1 || q[0].InstrumentID != "I-BAD" || q[0].SessionDate != "2026-03-03" {
		t.Fatalf("quarantine %+v", q)
	}
	live, _ := l.Manifest.Live(ctx)
	found := false
	for _, f := range live {
		found = found || f.Path == "ref/quarantine.parquet"
	}
	if !found {
		t.Fatal("quarantine not exported to the lake for Python readers")
	}
}
