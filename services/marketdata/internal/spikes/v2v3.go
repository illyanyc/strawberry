package spikes

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"time"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/services/marketdata/internal/fmp"
)

// BarSource fetches historical bars (ingest.BrokerSource in production).
type BarSource interface {
	Bars(ctx context.Context, symbols []string, tf mdv1.Timeframe, start, end time.Time) ([]*mdv1.Bar, error)
}

// DelistedProbe is a known delisted ticker and a window when it traded.
type DelistedProbe struct {
	Symbol     string
	From, To   string // YYYY-MM-DD
	DelistedOn string
}

// V2Probes are well-known delistings across 2022–2023 (acquisition, bank failure, bankruptcy).
var V2Probes = []DelistedProbe{
	{"TWTR", "2022-10-03", "2022-10-28", "2022-11-08"},
	{"SIVB", "2023-03-01", "2023-03-10", "2023-03-28"},
	{"FRC", "2023-04-03", "2023-04-28", "2023-05-01"},
	{"BBBY", "2023-04-03", "2023-04-21", "2023-05-03"},
	{"ATVI", "2023-10-02", "2023-10-12", "2023-10-13"},
}

// V2Row is one probe result.
type V2Row struct {
	Symbol     string `json:"symbol"`
	Window     string `json:"window"`
	DailyBars  int    `json:"daily_bars"`
	MinuteBars int    `json:"minute_bars_first_day"`
	Error      string `json:"error,omitempty"`
	Conclusion string `json:"conclusion"`
}

// V2 queries daily (and first-day minute) bars for delisted tickers through the platform's market-data
// path (broker-gateway → Alpaca with asof = window end) and reports coverage.
func V2(ctx context.Context, src BarSource) []V2Row {
	var out []V2Row
	for _, p := range V2Probes {
		from, _ := time.Parse("2006-01-02", p.From)
		to, _ := time.Parse("2006-01-02", p.To)
		row := V2Row{Symbol: p.Symbol, Window: p.From + ".." + p.To}
		d, err := src.Bars(ctx, []string{p.Symbol}, mdv1.Timeframe_TIMEFRAME_1D, from, to.Add(24*time.Hour))
		if err != nil {
			row.Error = err.Error()
		}
		row.DailyBars = len(d)
		m, err := src.Bars(ctx, []string{p.Symbol}, mdv1.Timeframe_TIMEFRAME_1M, from, from.Add(24*time.Hour))
		if err == nil {
			row.MinuteBars = len(m)
		}
		switch {
		case row.DailyBars > 0 && row.MinuteBars > 0:
			row.Conclusion = "covered (daily and intraday)"
		case row.DailyBars > 0:
			row.Conclusion = "daily only"
		default:
			row.Conclusion = "missing"
		}
		out = append(out, row)
	}
	return out
}

// V3Row is one FMP endpoint probe.
type V3Row struct {
	Path       string            `json:"path"`
	Status     int               `json:"status"`
	Bytes      int               `json:"bytes"`
	RateHeader map[string]string `json:"rate_headers,omitempty"`
	Error      string            `json:"error,omitempty"`
}

// V3 probes the owner's FMP key against `/stable` and the legacy `/api/v3` (plan tier, legacy
// access, intraday entitlement, rate-limit headers). The key never appears in the output.
func V3(ctx context.Context, c *fmp.Client) []V3Row {
	day := time.Now().AddDate(0, 0, -7).Format("2006-01-02")
	probes := []struct {
		path string
		q    url.Values
	}{
		{"/stable/profile", url.Values{"symbol": {"AAPL"}}},
		{"/stable/historical-price-eod/non-split-adjusted", url.Values{"symbol": {"AAPL"}, "from": {day}, "to": {day}}},
		{"/stable/historical-chart/1min", url.Values{"symbol": {"AAPL"}, "from": {day}, "to": {day}}},
		{"/stable/delisted-companies", url.Values{"page": {"0"}, "limit": {"1"}}},
		{"/stable/earnings-calendar", url.Values{"from": {day}, "to": {day}}},
		{"/api/v3/profile/AAPL", nil},
	}
	var out []V3Row
	for _, p := range probes {
		st, h, n, err := c.Probe(ctx, p.path, p.q)
		row := V3Row{Path: p.path, Status: st, Bytes: n}
		if err != nil {
			row.Error = err.Error()
		}
		for _, k := range []string{"X-Rate-Limit-Limit", "X-Rate-Limit-Remaining", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"} {
			if v := http.Header(h).Get(k); v != "" {
				if row.RateHeader == nil {
					row.RateHeader = map[string]string{}
				}
				row.RateHeader[k] = v
			}
		}
		out = append(out, row)
	}
	return out
}

// JSON renders any spike output.
func JSON(v any) string {
	b, _ := json.MarshalIndent(v, "", "  ")
	return string(b)
}
