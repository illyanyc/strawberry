package fmp

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/resilience"
)

const fixtures = "../../../../tests/fixtures/fmp/"

func server(t *testing.T, h http.HandlerFunc) *Client {
	t.Helper()
	s := httptest.NewServer(h)
	t.Cleanup(s.Close)
	c := New("test-fmp-key-0123456789abcdef", Options{RatePerMin: 6000})
	c.Base = s.URL
	c.Backoff = resilience.Backoff{Base: time.Millisecond, Cap: 5 * time.Millisecond}
	return c
}

func TestMD010NonSplitAdjustedEODAndStableOnly(t *testing.T) {
	raw, _ := os.ReadFile(fixtures + "eod_non_split_adjusted_aapl.json")
	var path string
	c := server(t, func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		if r.URL.Query().Get("apikey") == "" {
			w.WriteHeader(401)
			return
		}
		_, _ = w.Write(raw)
	})
	rows, err := c.NonSplitAdjustedEOD(context.Background(), "AAPL", time.Date(2026, 3, 1, 0, 0, 0, 0, time.UTC), time.Date(2026, 3, 4, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if path != "/stable/historical-price-eod/non-split-adjusted" {
		t.Fatalf("must call /stable only, called %s", path)
	}
	if len(rows) != 2 || rows[0].Close.String() != "241.95" || rows[1].Volume.String() != "48765432" {
		t.Fatalf("rows %+v", rows)
	}
}

func TestMD008RetryAfterBreakerAndLegacy(t *testing.T) {
	var calls atomic.Int32
	c := server(t, func(w http.ResponseWriter, r *http.Request) {
		switch calls.Add(1) {
		case 1:
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(429)
		default:
			_, _ = w.Write([]byte(`[{"symbol":"TWTR","companyName":"Twitter, Inc.","exchange":"NYSE","ipoDate":"2013-11-07","delistedDate":"2022-11-08"}]`))
		}
	})
	rows, err := c.DelistedCompanies(context.Background(), 3)
	if err != nil || len(rows) != 1 || calls.Load() != 2 {
		t.Fatalf("429 must be retried: rows=%d calls=%d err=%v", len(rows), calls.Load(), err)
	}
	legacy := server(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(403)
		_, _ = w.Write([]byte(`{"Error Message":"Legacy Endpoint : Due to Legacy endpoints being no longer supported..."}`))
	})
	if _, err := legacy.Splits(context.Background(), "NVDA"); !errors.Is(err, ErrLegacy) {
		t.Fatalf("legacy/forbidden must be permanent ErrLegacy: %v", err)
	}
	var n atomic.Int32
	down := server(t, func(w http.ResponseWriter, _ *http.Request) { n.Add(1); w.WriteHeader(503) })
	down.Breaker = resilience.NewBreaker(2, time.Hour)
	_, err = down.Splits(context.Background(), "NVDA")
	if err == nil || down.Breaker.State() != resilience.BreakerOpen {
		t.Fatalf("breaker must open after repeated 5xx: %v %s", err, down.Breaker.State())
	}
	before := n.Load()
	if _, err := down.Splits(context.Background(), "NVDA"); !errors.Is(err, resilience.ErrOpen) || n.Load() != before {
		t.Fatal("open breaker must short-circuit without calling FMP")
	}
	if _, err := New("", Options{}).Splits(context.Background(), "X"); !errors.Is(err, ErrDisabled) {
		t.Fatal("no key must disable the client")
	}
}

func TestSE012APIKeyNeverInErrorsOrLogs(t *testing.T) {
	c := New("leak-canary-fmp-key-9f8e7d6c", Options{})
	c.Base = "http://127.0.0.1:1" // connection refused: the net/url error embeds the full URL
	c.Backoff = resilience.Backoff{Base: time.Millisecond, Cap: 5 * time.Millisecond}
	c.Breaker = resilience.NewBreaker(100, time.Hour)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_, err := c.Splits(ctx, "AAPL")
	if err == nil || strings.Contains(err.Error(), "leak-canary-fmp-key-9f8e7d6c") {
		t.Fatalf("key leaked in error: %v", err)
	}
	var buf bytes.Buffer
	slog.New(logging.New("t", "info", &buf).Handler()).Error("x", "err", err)
	if strings.Contains(buf.String(), "leak-canary-fmp-key-9f8e7d6c") {
		t.Fatal("key leaked in log")
	}
}

func TestFMPFixturesDecode(t *testing.T) {
	for file, fn := range map[string]func(c *Client) (int, error){
		"splits_nvda.json":    func(c *Client) (int, error) { r, err := c.Splits(context.Background(), "NVDA"); return len(r), err },
		"dividends_aapl.json": func(c *Client) (int, error) { r, err := c.Dividends(context.Background(), "AAPL"); return len(r), err },
		"symbol_change.json":  func(c *Client) (int, error) { r, err := c.SymbolChanges(context.Background(), 100); return len(r), err },
		"delisted_companies.json": func(c *Client) (int, error) {
			r, err := c.DelistedCompanies(context.Background(), 1)
			return len(r), err
		},
	} {
		raw, _ := os.ReadFile(fixtures + file)
		c := server(t, func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(raw) })
		n, err := fn(c)
		if err != nil || n == 0 {
			t.Fatalf("%s: n=%d err=%v", file, n, err)
		}
	}
	if c := server(t, func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`[]`)) }); c.BytesUsed() != 0 {
		t.Fatal("bytes before any call")
	}
}
