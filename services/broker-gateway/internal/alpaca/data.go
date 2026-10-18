package alpaca

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/resilience"
)

// FeedParam converts a platform feed name ("alpaca.sip") to Alpaca's ("sip").
func FeedParam(feed string) (string, error) {
	switch feed {
	case "alpaca.sip", "sip":
		return "sip", nil
	case "alpaca.iex", "iex":
		return "iex", nil
	default:
		return "", fmt.Errorf("alpaca: unknown feed %q", feed)
	}
}

// Timeframes maps contract timeframes to Alpaca's parameter and bar duration.
var Timeframes = map[mdv1.Timeframe]struct {
	Param string
	Dur   time.Duration
}{
	mdv1.Timeframe_TIMEFRAME_1M:  {"1Min", time.Minute},
	mdv1.Timeframe_TIMEFRAME_5M:  {"5Min", 5 * time.Minute},
	mdv1.Timeframe_TIMEFRAME_15M: {"15Min", 15 * time.Minute},
	mdv1.Timeframe_TIMEFRAME_30M: {"30Min", 30 * time.Minute},
	mdv1.Timeframe_TIMEFRAME_1H:  {"1Hour", time.Hour},
	mdv1.Timeframe_TIMEFRAME_1D:  {"1Day", 24 * time.Hour},
}

type jsonBar struct {
	T  time.Time   `json:"t"`
	O  json.Number `json:"o"`
	H  json.Number `json:"h"`
	L  json.Number `json:"l"`
	C  json.Number `json:"c"`
	V  json.Number `json:"v"`
	N  int64       `json:"n"`
	VW json.Number `json:"vw"`
}

func num(n json.Number) *commonv1.Decimal {
	if n == "" {
		return nil
	}
	return &commonv1.Decimal{Value: normDec(n.String())}
}

// VendorBar converts an Alpaca bar to the contract shape: raw, symbol-keyed, instrument_id empty,
// ts_close = ts_open + duration (marketdata re-aligns daily bars to the session, MD-004).
func VendorBar(symbol string, tf mdv1.Timeframe, feed string, b jsonBar) *mdv1.Bar {
	return &mdv1.Bar{
		Symbol: symbol, Timeframe: tf, TsOpen: b.T.UnixNano(), TsClose: b.T.Add(Timeframes[tf].Dur).UnixNano(),
		Open: num(b.O), High: num(b.H), Low: num(b.L), Close: num(b.C), Volume: num(b.V), Vwap: num(b.VW), TradeCount: b.N,
		Feed: feed, Source: "alpaca", AdjustmentBasis: mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW,
	}
}

// HistoricalBars fetches one page of raw bars (GET /v2/stocks/bars, adjustment=raw, DF-005).
func (c *Client) HistoricalBars(ctx context.Context, req *brokerv1.GetHistoricalBarsRequest) (*brokerv1.GetHistoricalBarsResponse, error) {
	tf, ok := Timeframes[req.GetTimeframe()]
	if !ok {
		return nil, fmt.Errorf("alpaca: unsupported timeframe %s", req.GetTimeframe())
	}
	feed, err := FeedParam(req.GetFeed())
	if err != nil {
		return nil, err
	}
	if len(req.GetSymbols()) == 0 || len(req.GetSymbols()) > 100 {
		return nil, fmt.Errorf("alpaca: 1..100 symbols per request")
	}
	limit := req.GetLimit()
	if limit == 0 || limit > 10000 {
		limit = 10000
	}
	q := url.Values{
		"symbols": {strings.Join(req.GetSymbols(), ",")}, "timeframe": {tf.Param}, "adjustment": {"raw"}, "feed": {feed},
		"start": {time.Unix(0, req.GetStart()).UTC().Format(time.RFC3339)}, "end": {time.Unix(0, req.GetEnd()).UTC().Format(time.RFC3339)},
		"limit": {strconv.Itoa(int(limit))}, "sort": {"asc"},
		// Identify each ticker's entity as of the window's last day, so a recycled ticker never returns
		// the later company's history and renamed tickers return their own past (spike V2, MD-001).
		"asof": {time.Unix(0, req.GetEnd()-1).In(nyLoc).Format("2006-01-02")},
	}
	if req.GetPageToken() != "" {
		q.Set("page_token", req.GetPageToken())
	}
	var raw json.RawMessage
	if err := c.retryable(ctx, func(ctx context.Context) error {
		return c.do(ctx, "data", http.MethodGet, "/v2/stocks/bars", q, nil, resilience.PriorityBulk, &raw)
	}); err != nil {
		return nil, err
	}
	var resp struct {
		Bars          map[string][]jsonBar `json:"bars"`
		NextPageToken *string              `json:"next_page_token"`
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if err := d.Decode(&resp); err != nil {
		return nil, fmt.Errorf("alpaca: decode bars: %w", err)
	}
	out := &brokerv1.GetHistoricalBarsResponse{}
	platformFeed := "alpaca." + feed
	for _, sym := range req.GetSymbols() {
		for _, b := range resp.Bars[sym] {
			if b.T.UnixNano() < req.GetStart() || b.T.UnixNano() >= req.GetEnd() {
				continue
			}
			out.Bars = append(out.Bars, VendorBar(sym, req.GetTimeframe(), platformFeed, b))
		}
	}
	if resp.NextPageToken != nil {
		out.NextPageToken = *resp.NextPageToken
	}
	return out, nil
}

// Assets lists US-equity assets (GET /v2/assets) for reference-data sync.
func (c *Client) Assets(ctx context.Context, includeInactive bool) (*brokerv1.ListAssetsResponse, error) {
	out := &brokerv1.ListAssetsResponse{AsOf: c.now().UnixNano()}
	statuses := []string{"active"}
	if includeInactive {
		statuses = append(statuses, "inactive")
	}
	for _, st := range statuses {
		var as []jsonAsset
		q := url.Values{"status": {st}, "asset_class": {"us_equity"}}
		if err := c.retryable(ctx, func(ctx context.Context) error {
			return c.do(ctx, "trading", http.MethodGet, "/v2/assets", q, nil, resilience.PriorityBulk, &as)
		}); err != nil {
			return nil, err
		}
		for _, a := range as {
			out.Assets = append(out.Assets, &brokerv1.Asset{Symbol: a.Symbol, BrokerAssetId: a.ID, Exchange: a.Exchange, AssetClass: a.Class,
				Name: a.Name, Status: a.Status, Tradable: a.Tradable, Shortable: a.Shortable, EasyToBorrow: a.EasyToBorrow,
				Fractionable: a.Fractionable, Marginable: a.Marginable})
		}
	}
	return out, nil
}

type jsonCA struct {
	ID            string      `json:"id"`
	Symbol        string      `json:"symbol"`
	OldSymbol     string      `json:"old_symbol"`
	NewSymbol     string      `json:"new_symbol"`
	NewRate       json.Number `json:"new_rate"`
	OldRate       json.Number `json:"old_rate"`
	Rate          json.Number `json:"rate"`
	ExDate        string      `json:"ex_date"`
	RecordDate    string      `json:"record_date"`
	PayableDate   string      `json:"payable_date"`
	ProcessDate   string      `json:"process_date"`
	EffectiveDate string      `json:"effective_date"`
}

var nyLoc = func() *time.Location {
	l, err := time.LoadLocation("America/New_York")
	if err != nil {
		return time.FixedZone("EST", -5*3600)
	}
	return l
}()

// dateNS converts a YYYY-MM-DD exchange-local date to UTC ns at 00:00 New York time (contract
// convention for ex_date).
func dateNS(s string) int64 {
	if s == "" {
		return 0
	}
	t, err := time.ParseInLocation("2006-01-02", s, nyLoc)
	if err != nil {
		return 0
	}
	return t.UnixNano()
}

// CorporateActions fetches actions between start and end (GET /v1/corporate-actions). available_at
// is left 0; marketdata sets it from declared_at / first fetch / ex-date (MD-011).
func (c *Client) CorporateActions(ctx context.Context, req *brokerv1.GetCorporateActionsRequest) (*brokerv1.GetCorporateActionsResponse, error) {
	out := &brokerv1.GetCorporateActionsResponse{}
	token := ""
	for page := 0; page < 1000; page++ {
		q := url.Values{"start": {time.Unix(0, req.GetStart()).In(nyLoc).Format("2006-01-02")}, "end": {time.Unix(0, req.GetEnd()).In(nyLoc).Format("2006-01-02")}, "limit": {"1000"}}
		if len(req.GetSymbols()) > 0 {
			q.Set("symbols", strings.Join(req.GetSymbols(), ","))
		}
		if token != "" {
			q.Set("page_token", token)
		}
		var raw json.RawMessage
		if err := c.retryable(ctx, func(ctx context.Context) error {
			return c.do(ctx, "data", http.MethodGet, "/v1/corporate-actions", q, nil, resilience.PriorityBulk, &raw)
		}); err != nil {
			return nil, err
		}
		var resp struct {
			CA            map[string][]jsonCA `json:"corporate_actions"`
			NextPageToken *string             `json:"next_page_token"`
		}
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		if err := d.Decode(&resp); err != nil {
			return nil, fmt.Errorf("alpaca: decode corporate actions: %w", err)
		}
		now := c.now().UnixNano()
		for kind, list := range resp.CA {
			for _, a := range list {
				ca := &mdv1.CorporateAction{Symbol: a.Symbol, SourceId: a.ID, Source: "alpaca", IngestedAt: now,
					ExDate: dateNS(a.ExDate), RecordDate: dateNS(a.RecordDate), PayableDate: dateNS(a.PayableDate)}
				switch kind {
				case "forward_splits", "reverse_splits", "unit_splits", "stock_splits":
					ca.Type = mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPLIT
					ca.Ratio = ratio(a.NewRate, a.OldRate)
				case "cash_dividends":
					ca.Type = mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DIVIDEND
					ca.Amount = num(a.Rate)
				case "name_changes":
					ca.Type = mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SYMBOL_CHANGE
					ca.Symbol, ca.NewSymbol = a.OldSymbol, a.NewSymbol
					ca.ExDate = dateNS(a.ProcessDate)
				case "worthless_removals":
					ca.Type = mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DELISTING
					ca.ExDate = dateNS(a.ProcessDate)
				case "cash_mergers", "stock_mergers", "stock_and_cash_mergers":
					ca.Type = mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_MERGER
					if ca.ExDate == 0 {
						ca.ExDate = dateNS(firstNonEmpty(a.EffectiveDate, a.ProcessDate))
					}
				case "spin_offs":
					ca.Type = mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPIN_OFF
					ca.Ratio = ratio(a.NewRate, a.OldRate)
				default:
					continue
				}
				out.Actions = append(out.Actions, ca)
			}
		}
		if resp.NextPageToken == nil || *resp.NextPageToken == "" {
			break
		}
		token = *resp.NextPageToken
	}
	return out, nil
}

type jsonNews struct {
	ID        json.Number `json:"id"`
	Headline  string      `json:"headline"`
	Author    string      `json:"author"`
	CreatedAt time.Time   `json:"created_at"`
	UpdatedAt time.Time   `json:"updated_at"`
	Summary   string      `json:"summary"`
	Content   string      `json:"content"`
	URL       string      `json:"url"`
	Symbols   []string    `json:"symbols"`
	Source    string      `json:"source"`
}

// News fetches one page of articles (GET /v1beta1/news, ascending by first publication). Text is
// passed through unchanged: context-data treats it as untrusted and sanitizes it (CX-011).
func (c *Client) News(ctx context.Context, req *brokerv1.GetNewsRequest) (*brokerv1.GetNewsResponse, error) {
	if req.GetEnd() <= req.GetStart() {
		return nil, fmt.Errorf("alpaca: news window end must be after start")
	}
	limit := req.GetLimit()
	if limit == 0 || limit > 50 {
		limit = 50
	}
	q := url.Values{
		"start": {time.Unix(0, req.GetStart()).UTC().Format(time.RFC3339)}, "end": {time.Unix(0, req.GetEnd()).UTC().Format(time.RFC3339)},
		"limit": {strconv.Itoa(int(limit))}, "sort": {"asc"}, "include_content": {strconv.FormatBool(req.GetIncludeContent())},
		"exclude_contentless": {"false"},
	}
	if len(req.GetSymbols()) > 0 {
		q.Set("symbols", strings.Join(req.GetSymbols(), ","))
	}
	if req.GetPageToken() != "" {
		q.Set("page_token", req.GetPageToken())
	}
	var raw json.RawMessage
	if err := c.retryable(ctx, func(ctx context.Context) error {
		return c.do(ctx, "data", http.MethodGet, "/v1beta1/news", q, nil, resilience.PriorityBulk, &raw)
	}); err != nil {
		return nil, err
	}
	var resp struct {
		News          []jsonNews `json:"news"`
		NextPageToken *string    `json:"next_page_token"`
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if err := d.Decode(&resp); err != nil {
		return nil, fmt.Errorf("alpaca: decode news: %w", err)
	}
	out := &brokerv1.GetNewsResponse{}
	for _, n := range resp.News {
		if n.ID.String() == "" || n.CreatedAt.IsZero() {
			continue
		}
		a := &brokerv1.NewsArticle{Id: n.ID.String(), Headline: n.Headline, Summary: n.Summary, Author: n.Author, Source: n.Source,
			Url: n.URL, Symbols: n.Symbols, CreatedAt: n.CreatedAt.UnixNano(), UpdatedAt: n.UpdatedAt.UnixNano()}
		if req.GetIncludeContent() {
			a.Content = n.Content
		}
		out.Articles = append(out.Articles, a)
	}
	if resp.NextPageToken != nil {
		out.NextPageToken = *resp.NextPageToken
	}
	return out, nil
}

func firstNonEmpty(xs ...string) string {
	for _, x := range xs {
		if x != "" {
			return x
		}
	}
	return ""
}

// ratio returns new/old as a decimal string (split 4:1 → "4").
func ratio(newRate, oldRate json.Number) *commonv1.Decimal {
	n, err1 := decimal.NewFromString(newRate.String())
	o, err2 := decimal.NewFromString(oldRate.String())
	if err1 != nil || err2 != nil || o.IsZero() {
		return nil
	}
	return &commonv1.Decimal{Value: n.DivRound(o, 10).String()}
}
