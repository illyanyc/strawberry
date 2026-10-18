package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	signalsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/signals/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
)

type tvExec struct {
	ordersv1.ExecutionServiceClient
	calls int
}

func (f *tvExec) ListStrategies(context.Context, *ordersv1.ListStrategiesRequest, ...grpc.CallOption) (*ordersv1.ListStrategiesResponse, error) {
	f.calls++
	return &ordersv1.ListStrategiesResponse{Strategies: []*ordersv1.Strategy{
		{StrategyId: "tv-breakout", Name: "TV breakout", Mode: ordersv1.StrategyMode_STRATEGY_MODE_PAPER, BrokerAccountId: "alpaca-paper"},
		{StrategyId: "orb-15m", Mode: ordersv1.StrategyMode_STRATEGY_MODE_PAPER},
	}}, nil
}

type tvMD struct{ mdv1.MarketDataServiceClient }

func (tvMD) ResolveSymbols(_ context.Context, r *mdv1.ResolveSymbolsRequest, _ ...grpc.CallOption) (*mdv1.ResolveSymbolsResponse, error) {
	out := &mdv1.ResolveSymbolsResponse{}
	for _, s := range r.GetSymbols() {
		if s == "AAPL" {
			out.Matches = append(out.Matches, &mdv1.SymbolMatch{Symbol: "AAPL", InstrumentId: "INST_AAPL"})
		} else {
			out.Unknown = append(out.Unknown, s)
		}
	}
	return out, nil
}

type fakeSignals struct {
	mu       sync.Mutex
	pub      []*signalsv1.Signal
	subjects []string
	ids      []string
	fail     bool
	recent   []*signalsv1.Signal
}

func (f *fakeSignals) Publish(_ context.Context, subject, msgID string, sig *signalsv1.Signal) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail {
		return errors.New("nats down")
	}
	f.pub, f.subjects, f.ids = append(f.pub, sig), append(f.subjects, subject), append(f.ids, msgID)
	return nil
}

func (f *fakeSignals) Recent(_ context.Context, id string, since time.Time, limit int) ([]*signalsv1.Signal, error) {
	out := []*signalsv1.Signal{}
	for _, s := range f.recent {
		if s.GetInstrumentId() == id && s.GetAsOf() >= since.UnixNano() && len(out) < limit {
			out = append(out, s)
		}
	}
	return out, nil
}

type fakeAccess struct{ ok bool }

func (f fakeAccess) Verify(_ context.Context, tok, aud string) error {
	if f.ok && tok == "good" && aud == "tv-aud" {
		return nil
	}
	return errors.New("rejected")
}

type tvRig struct {
	srv    *Server
	sig    *fakeSignals
	audits []audit.Event
	now    time.Time
	key    []byte
}

func newTVRig(t *testing.T, access AccessVerifier) *tvRig {
	t.Helper()
	cfg := testConfig("http://localhost:8080")
	cfg.TVWebhookAddr, cfg.TVMaxAge, cfg.CFAccessTVAud = ":8090", time.Minute, "tv-aud"
	cfg.TVAllowedIPs, cfg.TVTrustedProxies = TradingViewIPs, []string{"172.18.0.0/16"}
	rig := &tvRig{sig: &fakeSignals{}, now: time.Date(2026, 10, 19, 14, 30, 0, 0, time.UTC), key: []byte(strings.Repeat("k", 32))}
	srv, err := New(cfg, Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Signals: rig.sig,
		Trading: &TradingClients{Exec: &tvExec{}, MD: tvMD{}}, TVKey: rig.key, AccessVerifier: access})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = func() time.Time { return rig.now }
	srv.appendAudit = func(_ context.Context, e audit.Event) (int64, string, error) {
		rig.audits = append(rig.audits, e)
		return 1, "h", nil
	}
	rig.srv = srv
	return rig
}

func (rig *tvRig) post(name, body, peer, cfIP, jwt string) *httptest.ResponseRecorder {
	req := httptest.NewRequest("POST", "/webhooks/tradingview/"+name, strings.NewReader(body))
	req.RemoteAddr = peer + ":40000"
	if cfIP != "" {
		req.Header.Set("Cf-Connecting-Ip", cfIP)
	}
	if jwt != "" {
		req.Header.Set("Cf-Access-Jwt-Assertion", jwt)
	}
	rec := httptest.NewRecorder()
	rig.srv.WebhookHandler().ServeHTTP(rec, req)
	return rec
}

func (rig *tvRig) body(secret, ticker, action string, at time.Time) string {
	b, _ := json.Marshal(map[string]string{"secret": secret, "ticker": ticker, "action": action, "time": at.Format(time.RFC3339), "price": "187.20"})
	return string(b)
}

const tvIP, cfPeer = "52.89.214.238", "172.18.0.9"

// SE-064, D70: only verified alerts become signals for their own tv-<name> strategy; every
// rejection path is covered, and authenticated outcomes are audited.
func TestSE064TradingViewWebhookRejectionPaths(t *testing.T) {
	rig := newTVRig(t, fakeAccess{ok: true})
	secret := TVSecret(rig.key, "tv-breakout")
	good := func() string { return rig.body(secret, "NASDAQ:AAPL", "buy", rig.now.Add(-2*time.Second)) }
	cases := []struct {
		name, path, body, peer, cfIP, jwt string
		code                              int
		audited                           bool
	}{
		{"direct peer outside the allowlist", "breakout", good(), "203.0.113.7", "", "good", 403, false},
		{"spoofed Cf-Connecting-Ip from an untrusted peer", "breakout", good(), "203.0.113.7", tvIP, "good", 403, false},
		{"trusted proxy without client header", "breakout", good(), cfPeer, "", "good", 403, false},
		{"trusted proxy, client not TradingView", "breakout", good(), cfPeer, "198.51.100.1", "good", 403, false},
		{"missing Access token", "breakout", good(), cfPeer, tvIP, "", 403, false},
		{"bad Access token", "breakout", good(), cfPeer, tvIP, "forged", 403, false},
		{"bad name", "Break.Out", good(), cfPeer, tvIP, "good", 404, false},
		{"malformed body", "breakout", "not json", cfPeer, tvIP, "good", 400, false},
		{"unknown field", "breakout", `{"secret":"x","ticker":"AAPL","action":"buy","time":"2026-10-19T14:30:00Z","qty":100}`, cfPeer, tvIP, "good", 400, false},
		{"wrong secret", "breakout", rig.body("nope", "AAPL", "buy", rig.now), cfPeer, tvIP, "good", 401, false},
		{"another strategy's secret", "breakout", rig.body(TVSecret(rig.key, "tv-other"), "AAPL", "buy", rig.now), cfPeer, tvIP, "good", 401, false},
		{"stale alert", "breakout", rig.body(secret, "AAPL", "buy", rig.now.Add(-2*time.Minute)), cfPeer, tvIP, "good", 422, true},
		{"future alert", "breakout", rig.body(secret, "AAPL", "buy", rig.now.Add(time.Minute)), cfPeer, tvIP, "good", 422, true},
		{"bad action", "breakout", rig.body(secret, "AAPL", "buy_more", rig.now), cfPeer, tvIP, "good", 400, true},
		{"unknown symbol", "breakout", rig.body(secret, "ZZZZ", "buy", rig.now), cfPeer, tvIP, "good", 422, true},
		{"unregistered strategy", "ghost", rig.body(TVSecret(rig.key, "tv-ghost"), "AAPL", "buy", rig.now), cfPeer, tvIP, "good", 404, true},
	}
	for _, c := range cases {
		before := len(rig.audits)
		rec := rig.post(c.path, c.body, c.peer, c.cfIP, c.jwt)
		if rec.Code != c.code {
			t.Errorf("%s: got %d %s, want %d", c.name, rec.Code, rec.Body, c.code)
		}
		if (len(rig.audits) > before) != c.audited {
			t.Errorf("%s: audited=%v, want %v", c.name, len(rig.audits) > before, c.audited)
		}
	}
	if len(rig.sig.pub) != 0 {
		t.Fatalf("no rejected request may publish a signal: %v", rig.sig.subjects)
	}

	rec := rig.post("breakout", good(), cfPeer, tvIP, "good")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"accepted"`) {
		t.Fatalf("valid alert: %d %s", rec.Code, rec.Body)
	}
	sg := rig.sig.pub[0]
	if rig.sig.subjects[0] != "sig.tv-breakout.INST_AAPL" || sg.GetStrategyId() != "tv-breakout" || sg.GetDirection() != signalsv1.Direction_DIRECTION_LONG ||
		sg.GetModelVersion() != "tradingview" || sg.GetAsOf() != rig.now.UnixNano() || rig.sig.ids[0] == "" {
		t.Fatalf("signal %v on %s", sg, rig.sig.subjects[0])
	}
	last := rig.audits[len(rig.audits)-1]
	if last.Actor != "webhook:tradingview" || last.Action != "signals.create" || last.Target != "tv-breakout" || last.Details["outcome"] != "accepted" {
		t.Fatalf("audit %+v", last)
	}
	if strings.Contains(fmt.Sprint(last.Details), secret) {
		t.Fatal("the secret must never be audited")
	}
	// Replay of the same alert: 200 duplicate, nothing published.
	if rec := rig.post("breakout", good(), cfPeer, tvIP, "good"); rec.Code != 200 || !strings.Contains(rec.Body.String(), "duplicate") || len(rig.sig.pub) != 1 {
		t.Fatalf("replay: %d %s pub=%d", rec.Code, rec.Body, len(rig.sig.pub))
	}
	// exit maps to FLAT; a bus failure is retriable (503) and not remembered as seen.
	rig.sig.fail = true
	exit := rig.body(secret, "AAPL", "exit", rig.now)
	if rec := rig.post("breakout", exit, cfPeer, tvIP, "good"); rec.Code != 503 {
		t.Fatalf("bus down: %d", rec.Code)
	}
	rig.sig.fail = false
	if rec := rig.post("breakout", exit, cfPeer, tvIP, "good"); rec.Code != 200 || rig.sig.pub[1].GetDirection() != signalsv1.Direction_DIRECTION_FLAT {
		t.Fatalf("retry after bus failure: %d %v", rec.Code, rig.sig.pub)
	}
}

func TestSE064WebhookOffByDefaultAndNeverOnTheUIListener(t *testing.T) {
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest("POST", "/webhooks/tradingview/breakout", strings.NewReader("{}"))
	rec := httptest.NewRecorder()
	srv.WebhookHandler().ServeHTTP(rec, req)
	if rec.Code != 404 {
		t.Fatalf("disabled intake: %d", rec.Code)
	}
	for _, r := range srv.Routes() {
		if strings.Contains(r.Pattern, "webhook") {
			t.Fatalf("webhook route on the UI listener: %s", r.Pattern)
		}
	}
	cfg := testConfig("http://localhost:8080")
	cfg.HTTPAddr, cfg.TVWebhookAddr = ":8080", ":8080"
	cfg.DB.User, cfg.Bus.User, cfg.LogLevel, cfg.TVMaxAge = "x", "y", "info", time.Minute
	if err := cfg.Validate(); err == nil || !strings.Contains(err.Error(), "TB_TV_WEBHOOK_ADDR") {
		t.Fatalf("shared listener must be refused: %v", err)
	}
}

func TestSE064SecretsAreStablePerStrategyAndDistinct(t *testing.T) {
	k := []byte(strings.Repeat("z", 32))
	a, b := TVSecret(k, "tv-a"), TVSecret(k, "tv-b")
	if len(a) != 32 || a == b || a != TVSecret(k, "tv-a") {
		t.Fatalf("secrets %q %q", a, b)
	}
}

// UI-049: signals for chart overlays come from the bus, filtered by instrument and time.
func TestUI049SignalsEndpoint(t *testing.T) {
	rig := newTVRig(t, nil)
	rig.sig.recent = []*signalsv1.Signal{
		{SignalId: "s1", StrategyId: "ml-lgbm", InstrumentId: "INST_AAPL", AsOf: rig.now.Add(-time.Hour).UnixNano(), Score: 0.42, Direction: signalsv1.Direction_DIRECTION_LONG},
		{SignalId: "s2", StrategyId: "ml-lgbm", InstrumentId: "INST_MSFT", AsOf: rig.now.Add(-time.Hour).UnixNano()},
		{SignalId: "s3", StrategyId: "ml-lgbm", InstrumentId: "INST_AAPL", AsOf: rig.now.Add(-48 * time.Hour).UnixNano()},
	}
	call := func(q string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("GET", "/api/v1/signals?"+q, nil)
		rec := httptest.NewRecorder()
		rig.srv.handleSignals(rec, req, &reqCtx{session: &Session{OwnerID: "owner"}})
		return rec
	}
	if rec := call("instrument_id=a.>"); rec.Code != 400 {
		t.Fatalf("bad id: %d", rec.Code)
	}
	rec := call("instrument_id=INST_AAPL")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"s1"`) || strings.Contains(rec.Body.String(), `"s2"`) || strings.Contains(rec.Body.String(), `"s3"`) {
		t.Fatalf("default 24h window: %d %s", rec.Code, rec.Body)
	}
	rec = call(fmt.Sprintf("instrument_id=INST_AAPL&since=%d", rig.now.Add(-72*time.Hour).UnixNano()))
	if !strings.Contains(rec.Body.String(), `"s3"`) || !strings.Contains(rec.Body.String(), `"score":0.42`) {
		t.Fatalf("since window: %s", rec.Body)
	}
}

// UI-047 / SE-065: the tearsheet may be framed by this origin only on the embed variant; the SPA
// CSP gains exactly one frame source when the TradingView widget is enabled.
func TestUI047TearsheetEmbedAndSE065WidgetCSP(t *testing.T) {
	if !strings.Contains(tearsheetEmbedCSP, "frame-ancestors 'self'") || !strings.Contains(tearsheetEmbedCSP, "sandbox") || !strings.Contains(tearsheetEmbedCSP, "default-src 'none'") {
		t.Fatalf("embed CSP %q", tearsheetEmbedCSP)
	}
	if strings.Replace(cspWidgetPolicy, "frame-src 'self' https://s.tradingview.com; ", "", 1) != cspPolicy {
		t.Fatalf("widget CSP must differ from the strict CSP by the frame-src only:\n%s\n%s", cspWidgetPolicy, cspPolicy)
	}
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	h := srv.Handler(nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/healthz", nil))
	if got := rec.Header().Get("Content-Security-Policy"); got != cspPolicy {
		t.Fatalf("default CSP %q", got)
	}
	srv.flags.mu.Lock()
	srv.flags.widget, srv.flags.at = true, srv.now()
	srv.flags.mu.Unlock()
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/charts", nil))
	if got := rec.Header().Get("Content-Security-Policy"); got != cspWidgetPolicy {
		t.Fatalf("widget CSP on the SPA document: %q", got)
	}
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/api/v1/settings", nil))
	if got := rec.Header().Get("Content-Security-Policy"); got != cspPolicy {
		t.Fatalf("API responses keep the strict CSP: %q", got)
	}
}

// SE-064: requests for the Cloudflare Access UI hostname need a valid Access token; other hostnames
// (Tailscale, localhost) are unaffected.
func TestSE064AccessGateOnTheUIHostname(t *testing.T) {
	cfg := testConfig("https://ui.example.com")
	cfg.CFAccessUIAud, cfg.CFAccessUIHosts = "ui-aud", []string{"ui.example.com"}
	srv, err := New(cfg, Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)), AccessVerifier: uiAccess{}})
	if err != nil {
		t.Fatal(err)
	}
	h := srv.Handler(nil)
	do := func(host, tok, path string) int {
		req := httptest.NewRequest("GET", path, nil)
		req.Host = host
		if tok != "" {
			req.Header.Set("Cf-Access-Jwt-Assertion", tok)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		return rec.Code
	}
	if c := do("ui.example.com", "", "/auth/state"); c != http.StatusForbidden {
		t.Fatalf("no token: %d", c)
	}
	if c := do("ui.example.com:443", "bad", "/auth/state"); c != http.StatusForbidden {
		t.Fatalf("bad token: %d", c)
	}
	if c := do("ui.example.com", "", "/healthz"); c != 200 {
		t.Fatalf("healthz: %d", c)
	}
	if c := do("ui.example.com", "ok", "/healthz"); c != 200 {
		t.Fatalf("with token: %d", c)
	}
	if c := do("mini.tailnet.ts.net", "", "/healthz"); c != 200 {
		t.Fatalf("tailnet host: %d", c)
	}
}

type uiAccess struct{}

func (uiAccess) Verify(_ context.Context, tok, aud string) error {
	if tok == "ok" && aud == "ui-aud" {
		return nil
	}
	return errors.New("rejected")
}

type freshMD struct{ mdv1.MarketDataServiceClient }

func (freshMD) IsFresh(_ context.Context, r *mdv1.IsFreshRequest, _ ...grpc.CallOption) (*mdv1.IsFreshResponse, error) {
	if r.GetTimeframe() == mdv1.Timeframe_TIMEFRAME_1D {
		return nil, errors.New("down")
	}
	return &mdv1.IsFreshResponse{Fresh: r.GetTimeframe() == mdv1.Timeframe_TIMEFRAME_15M, LastBarClose: 42, Reason: "ok"}, nil
}

// UI-051: freshness per timeframe; a failing timeframe reports its error, never "fresh".
func TestUI051FreshnessPerTimeframe(t *testing.T) {
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Trading: &TradingClients{MD: freshMD{}}})
	if err != nil {
		t.Fatal(err)
	}
	call := func(id string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("GET", "/", nil)
		req.SetPathValue("id", id)
		rec := httptest.NewRecorder()
		srv.handleFreshness(rec, req, &reqCtx{session: &Session{OwnerID: "owner"}})
		return rec
	}
	if rec := call("a.>"); rec.Code != 400 {
		t.Fatalf("bad id: %d", rec.Code)
	}
	var out struct {
		Timeframes []struct {
			Timeframe string `json:"timeframe"`
			Fresh     bool   `json:"fresh"`
			Error     string `json:"error"`
		} `json:"timeframes"`
	}
	rec := call("INST_AAPL")
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil || len(out.Timeframes) != 6 {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	if !out.Timeframes[2].Fresh || out.Timeframes[0].Fresh || out.Timeframes[5].Fresh || out.Timeframes[5].Error != "marketdata_unavailable" {
		t.Fatalf("rows %+v", out.Timeframes)
	}
}
