// Package alpaca is the Alpaca adapter (section 04, ADR-0019): REST trading and data clients, the
// trade_updates stream, and the market-data stream, behind adapter.Broker and the market-data hub.
// Credentials are fetched from the credential store on demand, kept only in memory, and registered
// with the log redactor (SE-012, SE-031).
package alpaca

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
	"time"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
)

// Endpoints for one environment. Base URLs are derived from the environment only (BR-013).
type Endpoints struct {
	Trading string // REST trading API
	Data    string // REST market-data API
	TradeWS string // trade_updates stream
	DataWS  string // market-data stream base, feed appended (e.g. /v2/sip)
}

// EndpointsFor returns Alpaca's endpoints for env.
func EndpointsFor(env brokerv1.Environment) Endpoints {
	if env == brokerv1.Environment_ENVIRONMENT_LIVE {
		return Endpoints{Trading: "https://api.alpaca.markets", Data: "https://data.alpaca.markets",
			TradeWS: "wss://api.alpaca.markets/stream", DataWS: "wss://stream.data.alpaca.markets"}
	}
	return Endpoints{Trading: "https://paper-api.alpaca.markets", Data: "https://data.alpaca.markets",
		TradeWS: "wss://paper-api.alpaca.markets/stream", DataWS: "wss://stream.data.alpaca.markets"}
}

// Credentials are an Alpaca key pair.
type Credentials struct {
	KeyID  string
	Secret string
}

// CredentialFunc returns the credential for the account (from the encrypted store).
type CredentialFunc func(ctx context.Context) (Credentials, error)

// FieldsToCredentials converts stored secret fields (api_key_id, api_secret) and registers them with
// the log redactor.
func FieldsToCredentials(fields map[string]string) (Credentials, error) {
	c := Credentials{KeyID: fields["api_key_id"], Secret: fields["api_secret"]}
	if c.KeyID == "" || c.Secret == "" {
		return Credentials{}, fmt.Errorf("alpaca: credential needs api_key_id and api_secret: %w", adapter.ErrNoCredential)
	}
	logging.RegisterSecret(c.KeyID)
	logging.RegisterSecret(c.Secret)
	return c, nil
}

// Client is a rate-limited REST client for one credential.
type Client struct {
	HTTP      *http.Client
	Endpoints Endpoints
	Creds     CredentialFunc
	// Trading and Data are separate budgets so data jobs cannot starve orders (BR-016).
	Trading *resilience.Limiter
	Data    *resilience.Limiter
	Now     func() time.Time
}

// APIError is a non-2xx Alpaca response.
type APIError struct {
	Status  int
	Code    int64
	Message string
	Retry   time.Duration
}

func (e *APIError) Error() string {
	return fmt.Sprintf("alpaca: HTTP %d code %d: %s", e.Status, e.Code, e.Message)
}

// route records every endpoint the client can call; BR-020 forbids account-wide liquidation and
// cancel-all, and TestBR020NoAccountWideEndpoints checks this table.
type route struct {
	Method string
	Path   string
}

// Routes is the complete set of REST routes the adapter uses.
var Routes = []route{
	{http.MethodGet, "/v2/account"},
	{http.MethodGet, "/v2/positions"},
	{http.MethodPost, "/v2/orders"},
	{http.MethodGet, "/v2/orders"},
	{http.MethodGet, "/v2/orders:by_client_order_id"},
	{http.MethodGet, "/v2/orders/{id}"},
	{http.MethodDelete, "/v2/orders/{id}"},
	{http.MethodPatch, "/v2/orders/{id}"},
	{http.MethodGet, "/v2/account/activities/FILL"},
	{http.MethodGet, "/v2/assets"},
	{http.MethodGet, "/v2/assets/{id}"},
	{http.MethodGet, "/v2/stocks/bars"},
	{http.MethodGet, "/v1/corporate-actions"},
	{http.MethodGet, "/v1beta1/news"},
}

func allowed(method, path string) bool {
	for _, r := range Routes {
		if r.Method != method {
			continue
		}
		if r.Path == path {
			return true
		}
		if pre, ok := strings.CutSuffix(r.Path, "{id}"); ok && strings.HasPrefix(path, pre) && !strings.Contains(path[len(pre):], "/") && len(path) > len(pre) {
			return true
		}
	}
	return false
}

// do performs one request. base selects the trading or data API. prio is the limiter class.
func (c *Client) do(ctx context.Context, base, method, path string, q url.Values, body any, prio resilience.Priority, out any) error {
	if !allowed(method, path) {
		return fmt.Errorf("alpaca: route %s %s is not in the adapter's route table (BR-020)", method, path)
	}
	lim := c.Trading
	host := c.Endpoints.Trading
	if base == "data" {
		lim, host = c.Data, c.Endpoints.Data
	}
	if lim != nil {
		if err := lim.Wait(ctx, prio); err != nil {
			return errors.Join(adapter.ErrNotSent, err)
		}
	}
	creds, err := c.Creds(ctx)
	if err != nil {
		return errors.Join(adapter.ErrNotSent, err)
	}
	u := host + path
	if len(q) > 0 {
		u += "?" + q.Encode()
	}
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return errors.Join(adapter.ErrNotSent, err)
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, u, rd)
	if err != nil {
		return errors.Join(adapter.ErrNotSent, err)
	}
	req.Header.Set("APCA-API-KEY-ID", creds.KeyID)
	req.Header.Set("APCA-API-SECRET-KEY", creds.Secret)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("alpaca: %s %s: %s", method, path, logging.Redact(err.Error()))
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return fmt.Errorf("alpaca: read %s: %w", path, err)
	}
	if resp.StatusCode == http.StatusTooManyRequests && lim != nil {
		lim.Pause(resilience.RetryAfter(resp.Header, c.now()))
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		var e struct {
			Code    int64  `json:"code"`
			Message string `json:"message"`
		}
		_ = json.Unmarshal(raw, &e)
		if e.Message == "" {
			e.Message = strings.TrimSpace(string(raw))
			if len(e.Message) > 200 {
				e.Message = e.Message[:200]
			}
		}
		return &APIError{Status: resp.StatusCode, Code: e.Code, Message: logging.Redact(e.Message), Retry: resilience.RetryAfter(resp.Header, c.now())}
	}
	if out == nil || resp.StatusCode == http.StatusNoContent || len(raw) == 0 {
		return nil
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("alpaca: decode %s: %w", path, err)
	}
	return nil
}

func (c *Client) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now()
}

// retryable wraps a read-only call with backoff, honoring Retry-After (BR-016); never used for
// submits, cancels, or replaces.
func (c *Client) retryable(ctx context.Context, fn func(ctx context.Context) error) error {
	return resilience.Retry(ctx, resilience.Backoff{Base: 250 * time.Millisecond, Cap: 5 * time.Second}, 4, func(ctx context.Context) error {
		err := fn(ctx)
		var ae *APIError
		if errors.As(err, &ae) {
			switch {
			case ae.Status == http.StatusTooManyRequests:
				return &resilience.RetryAfterError{Wait: ae.Retry, Err: err}
			case ae.Status >= 500:
				return err
			default:
				return errors.Join(resilience.ErrPermanent, err)
			}
		}
		return err
	})
}
