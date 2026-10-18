package api

import (
	"context"
	"testing"
	"time"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/live"
)

type nopSink struct{}

func (nopSink) Final(context.Context, *mdv1.Bar) error      { return nil }
func (nopSink) Correction(context.Context, *mdv1.Bar) error { return nil }
func (nopSink) Partial(context.Context, *mdv1.Bar)          {}

func TestMD007IsFreshFailsClosedAndAllowlist(t *testing.T) {
	p := live.New(calendar.XNYS(), nil, nopSink{}, "alpaca.sip", nil, nil)
	s := &Server{Pipe: p, Feed: "alpaca.sip", Now: time.Now}
	r, err := s.IsFresh(context.Background(), &mdv1.IsFreshRequest{InstrumentId: "I-X"})
	if err != nil || r.GetFresh() || r.GetReason() == "" {
		t.Fatalf("unknown/disconnected instrument must not be fresh: %+v %v", r, err)
	}
	if _, err := s.IsFresh(context.Background(), &mdv1.IsFreshRequest{}); err == nil {
		t.Fatal("missing instrument_id accepted")
	}
	none := &Server{Feed: "alpaca.sip", Now: time.Now}
	if r, _ := none.IsFresh(context.Background(), &mdv1.IsFreshRequest{InstrumentId: "I-X"}); r.GetFresh() {
		t.Fatal("streaming disabled must not report fresh")
	}
	for m, callers := range AllowedCallers {
		for _, c := range callers {
			if c == "ledger" || c == "marketdata" {
				t.Fatalf("%s allows %s", m, c)
			}
		}
	}
	if len(AllowedCallers[svc+"IsFresh"]) == 0 || AllowedCallers[svc+"IsFresh"][0] != "execution" {
		t.Fatal("execution must be able to call IsFresh before orders (RM-004)")
	}
	fs, _ := s.GetFeedStatus(context.Background(), &mdv1.GetFeedStatusRequest{})
	if len(fs.GetFeeds()) != 1 || fs.GetFeeds()[0].GetConnected() {
		t.Fatalf("feed status %+v", fs)
	}
}

type ringStub []*mdv1.Bar

func (r ringStub) Ring(_ context.Context, tf, id string, n int) ([]*mdv1.Bar, error) {
	if tf != "15m" || id != "I1" {
		return nil, nil
	}
	return r, nil
}

// UI-010: chart history comes from the cache ring, filtered to the requested window.
func TestUI010GetBarsFromCacheRing(t *testing.T) {
	s := &Server{Bars: ringStub{{InstrumentId: "I1", TsOpen: 1}, {InstrumentId: "I1", TsOpen: 2}, {InstrumentId: "I1", TsOpen: 3}}}
	r, err := s.GetBars(context.Background(), &mdv1.GetBarsRequest{InstrumentId: "I1", Timeframe: mdv1.Timeframe_TIMEFRAME_15M, Start: 2})
	if err != nil || len(r.GetBars()) != 2 || r.GetSource() != "cache" {
		t.Fatalf("GetBars = %v %v", r, err)
	}
	if _, err := s.GetBars(context.Background(), &mdv1.GetBarsRequest{InstrumentId: "I1"}); err == nil {
		t.Fatal("timeframe is required")
	}
	if AllowedCallers["/tb.marketdata.v1.MarketDataService/GetBars"][0] != "api-gateway" {
		t.Fatal("GetBars must be allowlisted for api-gateway")
	}
}

type tickerStub map[string]string

func (m tickerStub) FromTickers(_ context.Context, tickers []string, _ time.Time) (ids, unknown []string) {
	for _, t := range tickers {
		if id, ok := m[t]; ok {
			ids = append(ids, id)
		} else {
			unknown = append(unknown, t)
		}
	}
	return ids, unknown
}

// UI-011: tickers resolve to instrument IDs by exact, case-insensitive match; unknown ones are listed.
func TestUI011ResolveSymbols(t *testing.T) {
	s := &Server{Symbols: tickerStub{"AAPL": "I-AAPL"}, Now: time.Now}
	r, err := s.ResolveSymbols(context.Background(), &mdv1.ResolveSymbolsRequest{Symbols: []string{" aapl", "ZZZZ"}})
	if err != nil || len(r.GetMatches()) != 1 || r.GetMatches()[0].GetInstrumentId() != "I-AAPL" || r.GetMatches()[0].GetSymbol() != "AAPL" ||
		len(r.GetUnknown()) != 1 || r.GetUnknown()[0] != "ZZZZ" {
		t.Fatalf("ResolveSymbols = %v %v", r, err)
	}
	if _, err := s.ResolveSymbols(context.Background(), &mdv1.ResolveSymbolsRequest{}); err == nil {
		t.Fatal("empty request must be rejected")
	}
}
