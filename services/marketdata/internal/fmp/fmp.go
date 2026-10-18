// Package fmp is the Financial Modeling Prep client (section 05, ADR-0022). It calls `/stable`
// endpoints only (accounts created after 2025-08-31 get 403 on legacy `/api/v3`; spike V3), with a
// per-minute token budget at 80% of the plan limit, a 30-day bandwidth budget, full-jitter retries
// that honor Retry-After, and a circuit breaker (MD-008). The API key travels as a query parameter,
// so URLs are never logged and errors are redacted (SE-012).
package fmp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/resilience"
)

// BaseURL of the FMP API.
const BaseURL = "https://financialmodelingprep.com"

// Client calls FMP.
type Client struct {
	HTTP    *http.Client
	Base    string
	Key     string
	Limiter *resilience.Limiter
	Breaker *resilience.Breaker
	// BandwidthBudget bytes per rolling 30 days (0 = unlimited); exceeding it opens the client.
	BandwidthBudget int64
	Now             func() time.Time
	// Backoff between retries of idempotent reads.
	Backoff resilience.Backoff

	bytes    atomic.Int64
	requests *prometheus.CounterVec
	bytesC   prometheus.Counter
}

// Options configure New.
type Options struct {
	RatePerMin      int   // budget, default 240 (80% of Starter 300/min)
	BandwidthBudget int64 // bytes, default 16 GB (80% of Starter 20 GB / 30 days)
	Registerer      prometheus.Registerer
}

// New returns a client. key may be empty (FMP disabled; every call fails with ErrDisabled).
func New(key string, o Options) *Client {
	if o.RatePerMin <= 0 {
		o.RatePerMin = 240
	}
	if o.BandwidthBudget == 0 {
		o.BandwidthBudget = 16 << 30
	}
	if key != "" {
		logging.RegisterSecret(key)
	}
	c := &Client{HTTP: &http.Client{Timeout: 30 * time.Second}, Base: BaseURL, Key: key,
		Limiter: resilience.NewLimiter(o.RatePerMin, time.Minute, 5), Breaker: resilience.NewBreaker(5, time.Minute),
		BandwidthBudget: o.BandwidthBudget, Now: time.Now, Backoff: resilience.Backoff{Base: 500 * time.Millisecond, Cap: 30 * time.Second},
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_fmp_requests_total", Help: "FMP requests by endpoint and outcome."}, []string{"endpoint", "outcome"}),
		bytesC:   prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_fmp_response_bytes_total", Help: "FMP response bytes (bandwidth budget)."}),
	}
	if o.Registerer != nil {
		o.Registerer.MustRegister(c.requests, c.bytesC)
	}
	return c
}

// Errors.
var (
	ErrDisabled  = errors.New("fmp: no API key configured")
	ErrLegacy    = errors.New("fmp: endpoint not available to this key (legacy or plan)")
	ErrBandwidth = errors.New("fmp: 30-day bandwidth budget exhausted")
)

// APIError is a non-success response.
type APIError struct {
	Status  int
	Message string
}

func (e *APIError) Error() string { return fmt.Sprintf("fmp: HTTP %d: %s", e.Status, e.Message) }

// BytesUsed returns the response bytes counted since start (persisted per day by the caller).
func (c *Client) BytesUsed() int64 { return c.bytes.Load() }

// get fetches path with params and decodes JSON into out (retrying idempotent reads).
func (c *Client) get(ctx context.Context, endpoint string, params url.Values, out any) error {
	if c.Key == "" {
		return ErrDisabled
	}
	if c.BandwidthBudget > 0 && c.bytes.Load() > c.BandwidthBudget {
		return ErrBandwidth
	}
	return resilience.Retry(ctx, c.Backoff, 5, func(ctx context.Context) error {
		if err := c.Breaker.Allow(); err != nil {
			return errors.Join(resilience.ErrPermanent, err)
		}
		err := c.once(ctx, endpoint, params, out)
		var ae *APIError
		switch {
		case err == nil:
			c.Breaker.Record(nil)
			c.requests.WithLabelValues(endpoint, "ok").Inc()
			return nil
		case errors.Is(err, ErrLegacy):
			c.requests.WithLabelValues(endpoint, "forbidden").Inc()
			return errors.Join(resilience.ErrPermanent, err)
		case errors.As(err, &ae) && ae.Status == http.StatusTooManyRequests:
			c.requests.WithLabelValues(endpoint, "rate_limited").Inc()
			return err
		case errors.As(err, &ae) && ae.Status < 500:
			c.Breaker.Record(nil)
			c.requests.WithLabelValues(endpoint, "client_error").Inc()
			return errors.Join(resilience.ErrPermanent, err)
		default:
			c.Breaker.Record(err)
			c.requests.WithLabelValues(endpoint, "error").Inc()
			return err
		}
	})
}

func (c *Client) once(ctx context.Context, endpoint string, params url.Values, out any) error {
	if err := c.Limiter.Wait(ctx, resilience.PriorityBulk); err != nil {
		return err
	}
	q := url.Values{}
	for k, v := range params {
		q[k] = v
	}
	q.Set("apikey", c.Key)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.Base+"/stable/"+endpoint+"?"+q.Encode(), nil)
	if err != nil {
		return err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		// net/url errors embed the URL (with the key): redact.
		return errors.New("fmp: " + logging.Redact(err.Error()))
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 256<<20))
	if err != nil {
		return err
	}
	c.bytes.Add(int64(len(raw)))
	c.bytesC.Add(float64(len(raw)))
	if resp.StatusCode == http.StatusTooManyRequests {
		wait := resilience.RetryAfter(resp.Header, c.Now())
		if resp.Header.Get("Retry-After") == "" {
			wait = 10 * time.Second
		}
		c.Limiter.Pause(wait)
		return &resilience.RetryAfterError{Wait: wait, Err: &APIError{Status: 429, Message: "rate limited"}}
	}
	msg := errorMessage(raw)
	if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusUnauthorized ||
		strings.Contains(strings.ToLower(msg), "legacy endpoint") || strings.Contains(strings.ToLower(msg), "special endpoint") {
		return fmt.Errorf("%w: HTTP %d %s", ErrLegacy, resp.StatusCode, logging.Redact(msg))
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &APIError{Status: resp.StatusCode, Message: logging.Redact(msg)}
	}
	if msg != "" {
		// FMP sometimes reports errors with HTTP 200.
		return &APIError{Status: 400, Message: logging.Redact(msg)}
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if err := d.Decode(out); err != nil {
		return fmt.Errorf("fmp: decode %s: %w", endpoint, err)
	}
	return nil
}

func errorMessage(raw []byte) string {
	t := bytes.TrimSpace(raw)
	if len(t) == 0 || t[0] != '{' {
		return ""
	}
	var m map[string]any
	if json.Unmarshal(t, &m) != nil {
		return ""
	}
	for _, k := range []string{"Error Message", "error", "message"} {
		if v, ok := m[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

// EOD is one daily bar (unadjusted for splits when from NonSplitAdjustedEOD).
type EOD struct {
	Symbol string
	Date   string // YYYY-MM-DD (exchange-local)
	Open   json.Number
	High   json.Number
	Low    json.Number
	Close  json.Number
	Volume json.Number
}

type eodJSON struct {
	Symbol   string      `json:"symbol"`
	Date     string      `json:"date"`
	Open     json.Number `json:"open"`
	High     json.Number `json:"high"`
	Low      json.Number `json:"low"`
	Close    json.Number `json:"close"`
	AdjOpen  json.Number `json:"adjOpen"`
	AdjHigh  json.Number `json:"adjHigh"`
	AdjLow   json.Number `json:"adjLow"`
	AdjClose json.Number `json:"adjClose"`
	Volume   json.Number `json:"volume"`
}

func pick(a, b json.Number) json.Number {
	if a != "" {
		return a
	}
	return b
}

// NonSplitAdjustedEOD returns raw daily bars (GET /stable/historical-price-eod/non-split-adjusted),
// the reconciliation counterpart of Alpaca's raw daily bars (MD-010). The endpoint labels its
// fields adj*; they are not split-adjusted.
func (c *Client) NonSplitAdjustedEOD(ctx context.Context, symbol string, from, to time.Time) ([]EOD, error) {
	var rows []eodJSON
	err := c.get(ctx, "historical-price-eod/non-split-adjusted", url.Values{"symbol": {symbol},
		"from": {from.Format("2006-01-02")}, "to": {to.Format("2006-01-02")}}, &rows)
	if err != nil {
		return nil, err
	}
	out := make([]EOD, 0, len(rows))
	for _, r := range rows {
		out = append(out, EOD{Symbol: symbol, Date: r.Date, Open: pick(r.AdjOpen, r.Open), High: pick(r.AdjHigh, r.High),
			Low: pick(r.AdjLow, r.Low), Close: pick(r.AdjClose, r.Close), Volume: r.Volume})
	}
	return out, nil
}

// Delisted is one delisted company.
type Delisted struct {
	Symbol       string `json:"symbol"`
	CompanyName  string `json:"companyName"`
	Exchange     string `json:"exchange"`
	IPODate      string `json:"ipoDate"`
	DelistedDate string `json:"delistedDate"`
}

// DelistedCompanies pages through GET /stable/delisted-companies (survivorship-free universe, D20).
func (c *Client) DelistedCompanies(ctx context.Context, maxPages int) ([]Delisted, error) {
	var out []Delisted
	for page := 0; page < maxPages; page++ {
		var rows []Delisted
		if err := c.get(ctx, "delisted-companies", url.Values{"page": {fmt.Sprint(page)}, "limit": {"100"}}, &rows); err != nil {
			return out, err
		}
		out = append(out, rows...)
		if len(rows) < 100 {
			break
		}
	}
	return out, nil
}

// SymbolChange is a ticker change.
type SymbolChange struct {
	Date        string `json:"date"`
	CompanyName string `json:"companyName"`
	OldSymbol   string `json:"oldSymbol"`
	NewSymbol   string `json:"newSymbol"`
}

// SymbolChanges returns GET /stable/symbol-change.
func (c *Client) SymbolChanges(ctx context.Context, limit int) ([]SymbolChange, error) {
	var rows []SymbolChange
	err := c.get(ctx, "symbol-change", url.Values{"limit": {fmt.Sprint(limit)}}, &rows)
	return rows, err
}

// Split is a stock split (numerator:denominator, new:old).
type Split struct {
	Symbol      string      `json:"symbol"`
	Date        string      `json:"date"`
	Numerator   json.Number `json:"numerator"`
	Denominator json.Number `json:"denominator"`
}

// Splits returns GET /stable/splits for a symbol.
func (c *Client) Splits(ctx context.Context, symbol string) ([]Split, error) {
	var rows []Split
	err := c.get(ctx, "splits", url.Values{"symbol": {symbol}}, &rows)
	return rows, err
}

// Dividend is a cash dividend; Date is the ex-date.
type Dividend struct {
	Symbol          string      `json:"symbol"`
	Date            string      `json:"date"`
	RecordDate      string      `json:"recordDate"`
	PaymentDate     string      `json:"paymentDate"`
	DeclarationDate string      `json:"declarationDate"`
	Dividend        json.Number `json:"dividend"`
	AdjDividend     json.Number `json:"adjDividend"`
}

// Dividends returns GET /stable/dividends for a symbol.
func (c *Client) Dividends(ctx context.Context, symbol string) ([]Dividend, error) {
	var rows []Dividend
	err := c.get(ctx, "dividends", url.Values{"symbol": {symbol}}, &rows)
	return rows, err
}

// Probe calls an endpoint and returns the HTTP outcome without decoding (spike V3).
func (c *Client) Probe(ctx context.Context, path string, params url.Values) (int, http.Header, int, error) {
	if c.Key == "" {
		return 0, nil, 0, ErrDisabled
	}
	q := url.Values{}
	for k, v := range params {
		q[k] = v
	}
	q.Set("apikey", c.Key)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.Base+path+"?"+q.Encode(), nil)
	if err != nil {
		return 0, nil, 0, err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return 0, nil, 0, errors.New("fmp: " + logging.Redact(err.Error()))
	}
	defer func() { _ = resp.Body.Close() }()
	n, _ := io.Copy(io.Discard, resp.Body)
	return resp.StatusCode, resp.Header, int(n), nil
}
