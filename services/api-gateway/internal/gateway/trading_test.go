package gateway

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

type fakeExec struct {
	ordersv1.ExecutionServiceClient
	mu       sync.Mutex
	triggers []*ordersv1.TriggerKillSwitchRequest
	releases []*ordersv1.ReleaseKillSwitchRequest
	submits  []*ordersv1.SubmitIntentRequest
	modes    []*ordersv1.SetStrategyModeRequest
	limits   []*ordersv1.SetRiskLimitsRequest
}

func (f *fakeExec) GetTradingStatus(context.Context, *ordersv1.GetTradingStatusRequest, ...grpc.CallOption) (*ordersv1.GetTradingStatusResponse, error) {
	return &ordersv1.GetTradingStatusResponse{Status: &ordersv1.TradingStatus{GlobalState: "running",
		Accounts: []*ordersv1.AccountTradingStatus{{BrokerAccountId: "alpaca-paper", Environment: "paper", ReconciliationStatus: "RECONCILIATION_STATUS_OK"}}}}, nil
}

func (f *fakeExec) TriggerKillSwitch(_ context.Context, r *ordersv1.TriggerKillSwitchRequest, _ ...grpc.CallOption) (*ordersv1.TriggerKillSwitchResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.triggers = append(f.triggers, r)
	return &ordersv1.TriggerKillSwitchResponse{KillSwitch: &ordersv1.KillSwitch{EventId: "K1", Scope: r.GetScope(), Latched: true}}, nil
}

func (f *fakeExec) ReleaseKillSwitch(_ context.Context, r *ordersv1.ReleaseKillSwitchRequest, _ ...grpc.CallOption) (*ordersv1.ReleaseKillSwitchResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.releases = append(f.releases, r)
	return &ordersv1.ReleaseKillSwitchResponse{KillSwitch: &ordersv1.KillSwitch{EventId: r.GetEventId()}}, nil
}

func (f *fakeExec) SubmitIntent(_ context.Context, r *ordersv1.SubmitIntentRequest, _ ...grpc.CallOption) (*ordersv1.SubmitIntentResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.submits = append(f.submits, r)
	return &ordersv1.SubmitIntentResponse{Decision: &ordersv1.RiskDecision{IntentId: r.GetIntent().GetIntentId(), Approved: true}}, nil
}

func (f *fakeExec) SetStrategyMode(_ context.Context, r *ordersv1.SetStrategyModeRequest, _ ...grpc.CallOption) (*ordersv1.SetStrategyModeResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.modes = append(f.modes, r)
	return &ordersv1.SetStrategyModeResponse{Strategy: &ordersv1.Strategy{StrategyId: r.GetStrategyId(), Mode: r.GetMode()}}, nil
}

// SetRiskLimits mimics execution: max_order_qty above 100 is a raise and needs a passkey actor.
func (f *fakeExec) SetRiskLimits(_ context.Context, r *ordersv1.SetRiskLimitsRequest, _ ...grpc.CallOption) (*ordersv1.SetRiskLimitsResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.limits = append(f.limits, r)
	if decimalGreater(r.GetLimits().GetMaxOrderQty().GetValue(), "100") && r.GetActor().GetStepUpMethod() != "passkey" {
		return nil, status.Error(codes.PermissionDenied, "execution: desktop passkey step-up required")
	}
	return &ordersv1.SetRiskLimitsResponse{Limits: r.GetLimits()}, nil
}

func (f *fakeExec) ListFills(context.Context, *ordersv1.ListFillsRequest, ...grpc.CallOption) (*ordersv1.ListFillsResponse, error) {
	return &ordersv1.ListFillsResponse{Fills: []*ordersv1.Fill{{InstrumentId: "I1", BrokerExecId: "e1"}, {InstrumentId: "I2", BrokerExecId: "e2"}}}, nil
}

type fakeLedger struct {
	ledgerv1.LedgerServiceClient
	mu       sync.Mutex
	alloc    string
	sets     []*ledgerv1.SetAllocationRequest
	resolved []*ledgerv1.ResolveBreakRequest
}

func (f *fakeLedger) GetStrategyPortfolio(_ context.Context, r *ledgerv1.GetStrategyPortfolioRequest, _ ...grpc.CallOption) (*ledgerv1.GetStrategyPortfolioResponse, error) {
	return &ledgerv1.GetStrategyPortfolioResponse{BrokerAccountId: "alpaca-paper",
		Portfolio: &ledgerv1.OwnerView{Owner: r.GetStrategyId(), Allocation: &commonv1.Decimal{Value: f.alloc}}}, nil
}

func (f *fakeLedger) SetAllocation(_ context.Context, r *ledgerv1.SetAllocationRequest, _ ...grpc.CallOption) (*ledgerv1.SetAllocationResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sets = append(f.sets, r)
	return &ledgerv1.SetAllocationResponse{Allocation: r.GetAmount()}, nil
}

func (f *fakeLedger) ResolveBreak(_ context.Context, r *ledgerv1.ResolveBreakRequest, _ ...grpc.CallOption) (*ledgerv1.ResolveBreakResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.resolved = append(f.resolved, r)
	return &ledgerv1.ResolveBreakResponse{}, nil
}

type fakeMD struct{ mdv1.MarketDataServiceClient }

func (fakeMD) GetBars(_ context.Context, r *mdv1.GetBarsRequest, _ ...grpc.CallOption) (*mdv1.GetBarsResponse, error) {
	return &mdv1.GetBarsResponse{Bars: []*mdv1.Bar{{InstrumentId: r.GetInstrumentId(), TsOpen: 1}}, Source: "cache"}, nil
}

type tradingRig struct {
	srv    *Server
	exec   *fakeExec
	ledger *fakeLedger
	now    time.Time
}

func newTradingRig(t *testing.T, env string) *tradingRig {
	t.Helper()
	cfg := testConfig("http://localhost:8080")
	cfg.Environment = env
	rig := &tradingRig{exec: &fakeExec{}, ledger: &fakeLedger{alloc: "1000"}, now: time.Date(2026, 10, 19, 15, 0, 0, 0, time.UTC)}
	srv, err := New(cfg, Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Trading: &TradingClients{Exec: rig.exec, Ledger: rig.ledger, MD: fakeMD{}}})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = func() time.Time { return rig.now }
	rig.srv = srv
	return rig
}

const desktopUA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 Safari/605.1.15"
const phoneUA = "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148"

// call invokes a handler directly with a session; stepUp is "" or a fresh step-up method.
func (rig *tradingRig) call(h func(http.ResponseWriter, *http.Request, *reqCtx), method, target, body, ua, stepUp string, pathValues ...string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, target, strings.NewReader(body))
	req.Header.Set("User-Agent", ua)
	for i := 0; i+1 < len(pathValues); i += 2 {
		req.SetPathValue(pathValues[i], pathValues[i+1])
	}
	sess := &Session{OwnerID: "owner", IdleExpires: rig.now.Add(time.Hour)}
	if stepUp != "" {
		until := rig.now.Add(time.Minute)
		sess.StepUpMethod, sess.StepUpUntil = stepUp, &until
	}
	rec := httptest.NewRecorder()
	h(rec, req, &reqCtx{session: sess, ip: "127.0.0.1", ua: ua})
	return rec
}

func errCode(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var m map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &m)
	s, _ := m["error"].(string)
	return s
}

// UI-006, RM-007: engaging needs no step-up (one confirmation, desktop or phone); release needs a
// fresh passkey step-up from a desktop client, and the forwarded actor carries both facts.
func TestRM007KillSwitchEngageAndDesktopPasskeyRelease(t *testing.T) {
	rig := newTradingRig(t, "test")
	s := rig.srv
	if rec := rig.call(s.handleKillEngage, "POST", "/api/v1/kill-switches", `{"scope":"global","reason":"drill"}`, phoneUA, ""); rec.Code != 200 {
		t.Fatalf("engage from phone: %d %s", rec.Code, rec.Body)
	}
	if got := rig.exec.triggers[0]; got.GetScope() != ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL ||
		got.GetAction() != ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN || got.GetActor().GetName() != "owner" || !got.GetActor().GetMobile() {
		t.Fatalf("trigger forwarded as %v", got)
	}
	if rec := rig.call(s.handleKillEngage, "POST", "/", `{"scope":"strategy","target":"a.>"}`, desktopUA, ""); rec.Code != 400 {
		t.Fatalf("bad target must be rejected: %d", rec.Code)
	}
	rel := func(ua, stepUp string) *httptest.ResponseRecorder {
		return rig.call(s.handleKillRelease, "POST", "/", `{"reason":"drill over"}`, ua, stepUp, "id", "K1")
	}
	if rec := rel(desktopUA, ""); rec.Code != 403 || errCode(t, rec) != "step_up_required" || !strings.Contains(rec.Body.String(), `"passkey"`) {
		t.Fatalf("release without step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := rel(desktopUA, "totp"); rec.Code != 403 || errCode(t, rec) != "step_up_required" {
		t.Fatalf("release after TOTP step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := rel(phoneUA, "passkey"); rec.Code != 403 || errCode(t, rec) != "desktop_required" {
		t.Fatalf("release from phone: %d %s", rec.Code, rec.Body)
	}
	if len(rig.exec.releases) != 0 {
		t.Fatal("no release may reach execution before the checks pass")
	}
	if rec := rel(desktopUA, "passkey"); rec.Code != 200 {
		t.Fatalf("desktop passkey release: %d %s", rec.Code, rec.Body)
	}
	if a := rig.exec.releases[0].GetActor(); a.GetStepUpMethod() != "passkey" || a.GetMobile() {
		t.Fatalf("release actor %v", a)
	}
}

// RM-007, VP-009: allocation increases need a passkey step-up; decreases do not.
func TestRM007AllocationIncreaseNeedsPasskey(t *testing.T) {
	rig := newTradingRig(t, "test")
	put := func(amount, stepUp string) *httptest.ResponseRecorder {
		return rig.call(rig.srv.handleStrategyAllocation, "PUT", "/", `{"amount":"`+amount+`"}`, desktopUA, stepUp, "id", "orb-15m")
	}
	if rec := put("500", ""); rec.Code != 200 {
		t.Fatalf("decrease: %d %s", rec.Code, rec.Body)
	}
	if rec := put("1000.01", ""); rec.Code != 403 || errCode(t, rec) != "step_up_required" {
		t.Fatalf("increase without step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := put("-5", ""); rec.Code != 400 {
		t.Fatalf("negative amount: %d", rec.Code)
	}
	if rec := put("2000", "passkey"); rec.Code != 200 {
		t.Fatalf("increase with passkey: %d %s", rec.Code, rec.Body)
	}
	if n := len(rig.ledger.sets); n != 2 || rig.ledger.sets[1].GetBrokerAccountId() != "alpaca-paper" || rig.ledger.sets[1].GetActor() != "owner" {
		t.Fatalf("SetAllocation calls %v", rig.ledger.sets)
	}
}

// RM-007: when execution reports a loosened limit without a passkey actor, the gateway answers with
// the policy's require_step_up for risk.limits.raise; after the step-up the same request passes.
func TestRM007RiskLimitRaiseMapsToStepUp(t *testing.T) {
	rig := newTradingRig(t, "test")
	put := func(qty, stepUp string) *httptest.ResponseRecorder {
		return rig.call(rig.srv.handleRiskLimitsPut, "PUT", "/", `{"maxOrderQty":{"value":"`+qty+`"}}`, desktopUA, stepUp, "scope", "strategy:orb-15m")
	}
	if rec := put("50", ""); rec.Code != 200 {
		t.Fatalf("lower: %d %s", rec.Code, rec.Body)
	}
	if rec := put("500", ""); rec.Code != 403 || errCode(t, rec) != "step_up_required" {
		t.Fatalf("raise without step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := put("500", "passkey"); rec.Code != 200 {
		t.Fatalf("raise with passkey: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(rig.srv.handleRiskLimitsPut, "PUT", "/", `{}`, desktopUA, "", "scope", "strategy:x.>"); rec.Code != 400 {
		t.Fatalf("bad scope: %d", rec.Code)
	}
}

// UI-015, SE-003: the ticket becomes a "manual" UI intent with a ULID; paper accounts need no
// step-up, unknown accounts are treated as live (passkey), and phones are refused unless enabled.
func TestUI015ManualOrderTicket(t *testing.T) {
	rig := newTradingRig(t, "test")
	ticket := func(acct string) string {
		return `{"broker_account_id":"` + acct + `","instrument_id":"I1","side":"buy","quantity":"10","order_type":"limit","limit_price":"101.5","tif":"day"}`
	}
	if rec := rig.call(rig.srv.handleOrderSubmit, "POST", "/", ticket("alpaca-paper"), desktopUA, ""); rec.Code != 200 {
		t.Fatalf("paper manual order: %d %s", rec.Code, rec.Body)
	}
	it := rig.exec.submits[0].GetIntent()
	if it.GetStrategyId() != "manual" || it.GetSource() != ordersv1.IntentSource_INTENT_SOURCE_UI || it.GetMode() != commonv1.Mode_MODE_PAPER ||
		it.GetQuantity().GetValue() != "10" || it.GetLimitPrice().GetValue() != "101.5" || it.GetKind() != ordersv1.IntentKind_INTENT_KIND_OPEN {
		t.Fatalf("intent %v", it)
	}
	if _, err := ulid.ParseStrict(it.GetIntentId()); err != nil {
		t.Fatalf("intent id %q is not a ULID", it.GetIntentId())
	}
	if rec := rig.call(rig.srv.handleOrderSubmit, "POST", "/", ticket("unknown-acct"), desktopUA, ""); rec.Code != 403 || errCode(t, rec) != "step_up_required" {
		t.Fatalf("unknown account must need the live step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(rig.srv.handleOrderSubmit, "POST", "/", ticket("alpaca-paper"), phoneUA, ""); rec.Code != 403 || errCode(t, rec) != "mobile_order_entry_disabled" {
		t.Fatalf("phone order: %d %s", rec.Code, rec.Body)
	}
	bad := []string{
		`{"broker_account_id":"alpaca-paper","instrument_id":"I1","side":"buy","quantity":"0","order_type":"market","tif":"day"}`,
		`{"broker_account_id":"alpaca-paper","instrument_id":"I1","side":"buy","quantity":"1e3","order_type":"market","tif":"day"}`,
		`{"broker_account_id":"alpaca-paper","instrument_id":"I1","side":"buy","quantity":"5","order_type":"limit","tif":"day"}`,
		`{"broker_account_id":"alpaca-paper","instrument_id":"I1","side":"hold","quantity":"5","order_type":"market","tif":"day"}`,
	}
	for _, b := range bad {
		if rec := rig.call(rig.srv.handleOrderSubmit, "POST", "/", b, desktopUA, ""); rec.Code != 400 {
			t.Errorf("%s: %d", b, rec.Code)
		}
	}
	if len(rig.exec.submits) != 1 {
		t.Fatalf("rejected tickets reached execution: %d", len(rig.exec.submits))
	}
}

// UI-014, SM-004: the UI sets disabled|shadow|paper only; live is refused before execution.
func TestUI014StrategyModeNeverLive(t *testing.T) {
	rig := newTradingRig(t, "test")
	if rec := rig.call(rig.srv.handleStrategyMode, "PUT", "/", `{"mode":"live"}`, desktopUA, "passkey", "id", "orb-15m"); rec.Code != 400 {
		t.Fatalf("live: %d", rec.Code)
	}
	if rec := rig.call(rig.srv.handleStrategyMode, "PUT", "/", `{"mode":"shadow","reason":"watch"}`, desktopUA, "", "id", "orb-15m"); rec.Code != 200 {
		t.Fatalf("shadow: %d %s", rec.Code, rec.Body)
	}
	if len(rig.exec.modes) != 1 || rig.exec.modes[0].GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_SHADOW {
		t.Fatalf("modes %v", rig.exec.modes)
	}
}

// VP-019, RM-007: break resolution needs a passkey step-up and a known resolution.
func TestVP019BreakResolutionNeedsPasskey(t *testing.T) {
	rig := newTradingRig(t, "test")
	res := func(body, stepUp string) *httptest.ResponseRecorder {
		return rig.call(rig.srv.handleResolveBreak, "POST", "/", body, desktopUA, stepUp, "account", "alpaca-paper", "id", "B1")
	}
	if rec := res(`{"resolution":"accept"}`, ""); rec.Code != 403 || errCode(t, rec) != "step_up_required" {
		t.Fatalf("no step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := res(`{"resolution":"delete_everything"}`, "passkey"); rec.Code != 400 {
		t.Fatalf("unknown resolution: %d", rec.Code)
	}
	if rec := res(`{"resolution":"transfer_to_manual","from_owner":"orb-15m","qty":"20"}`, "passkey"); rec.Code != 200 {
		t.Fatalf("transfer: %d %s", rec.Code, rec.Body)
	}
	if r := rig.ledger.resolved[0]; r.GetQty().GetValue() != "20" || r.GetFromOwner() != "orb-15m" || r.GetActor() != "owner" {
		t.Fatalf("forwarded %v", r)
	}
}

// Identifiers that reach bus subjects or gRPC are validated (no subject wildcards); fills are
// filtered to the requested instrument; responses use the canonical protojson names.
func TestTradingInputValidationAndJSON(t *testing.T) {
	rig := newTradingRig(t, "test")
	for _, q := range []string{"instrument_id=a.>&timeframe=15m", "instrument_id=I1&timeframe=2m", "timeframe=15m"} {
		if rec := rig.call(rig.srv.handleBars, "GET", "/api/v1/bars?"+q, "", desktopUA, ""); rec.Code != 400 {
			t.Errorf("bars %s: %d", q, rec.Code)
		}
		if rec := rig.call(rig.srv.handleMarketStream, "GET", "/api/v1/stream/market?"+q, "", desktopUA, ""); rec.Code != 400 {
			t.Errorf("stream %s: %d", q, rec.Code)
		}
	}
	rec := rig.call(rig.srv.handleBars, "GET", "/api/v1/bars?instrument_id=I1&timeframe=15m", "", desktopUA, "")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"instrumentId":"I1"`) || !strings.Contains(rec.Body.String(), `"source":"cache"`) {
		t.Fatalf("bars: %d %s", rec.Code, rec.Body)
	}
	rec = rig.call(rig.srv.handleFills, "GET", "/api/v1/fills?instrument_id=I2", "", desktopUA, "")
	if rec.Code != 200 || strings.Contains(rec.Body.String(), `"e1"`) || !strings.Contains(rec.Body.String(), `"e2"`) {
		t.Fatalf("fills filter: %s", rec.Body)
	}
	empty := newTradingRig(t, "test")
	empty.srv.trading = nil
	if rec := empty.call(empty.srv.handleStrategies, "GET", "/", "", desktopUA, ""); rec.Code != 503 {
		t.Fatalf("missing execution: %d", rec.Code)
	}
	if _, err := empty.srv.tradingStatusJSON(context.Background()); err == nil {
		t.Fatal("status must report execution as unavailable")
	}
}

func TestDecimalGreater(t *testing.T) {
	cases := []struct {
		a, b string
		want bool
	}{
		{"10", "9", true}, {"9", "10", false}, {"1000.01", "1000", true}, {"1000.00", "1000", false},
		{"0010", "10", false}, {"0.5", "0.49", true}, {"1", "", true}, {"0", "", false}, {"5", "garbage", true},
	}
	for _, c := range cases {
		if got := decimalGreater(c.a, c.b); got != c.want {
			t.Errorf("decimalGreater(%q,%q)=%v", c.a, c.b, got)
		}
	}
}

type fakeBus struct {
	mu   sync.Mutex
	subs map[string]func([]byte)
}

func (b *fakeBus) Subscribe(subject string, fn func([]byte)) (func(), error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.subs[subject] = fn
	return func() {
		b.mu.Lock()
		delete(b.subs, subject)
		b.mu.Unlock()
	}, nil
}

func (b *fakeBus) publish(subject string, m proto.Message) {
	data, _ := proto.Marshal(m)
	b.mu.Lock()
	fn := b.subs[subject]
	b.mu.Unlock()
	if fn != nil {
		fn(data)
	}
}

func (b *fakeBus) count() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.subs)
}

// UI-012: the chart stream sends final bars, coalesces partial bars (latest wins), forwards only
// executions in the charted instrument, and unsubscribes when the client goes away.
func TestUI012MarketStream(t *testing.T) {
	rig := newTradingRig(t, "dev")
	bus := &fakeBus{subs: map[string]func([]byte){}}
	rig.srv.market = bus
	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest("GET", "/api/v1/stream/market?instrument_id=I1&timeframe=15m", nil).WithContext(ctx)
	rec := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		defer close(done)
		rig.srv.handleMarketStream(rec, req, &reqCtx{session: &Session{OwnerID: "owner"}})
	}()
	deadline := time.Now().Add(2 * time.Second)
	for bus.count() < 4 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	bus.publish("md.bar.15m.I1", &mdv1.Bar{InstrumentId: "I1", TsOpen: 100, IsFinal: true})
	for i := range 20 {
		bus.publish("md.bar.partial.15m.I1", &mdv1.Bar{InstrumentId: "I1", TsOpen: 200, Close: &commonv1.Decimal{Value: "1" + strings.Repeat("0", i%3)}})
	}
	bus.publish("md.bar.partial.15m.I1", &mdv1.Bar{InstrumentId: "I1", TsOpen: 200, Close: &commonv1.Decimal{Value: "123.45"}})
	bus.publish("exec.>", &ordersv1.ExecutionReport{InstrumentId: "I2", BrokerExecId: "other"})
	bus.publish("exec.>", &ordersv1.ExecutionReport{InstrumentId: "I1", BrokerExecId: "mine"})
	time.Sleep(2 * partialEvery)
	cancel()
	<-done
	body := rec.Body.String()
	if !strings.Contains(body, "event: ready") || !strings.Contains(body, "event: bar") || !strings.Contains(body, `"tsOpen":"100"`) {
		t.Fatalf("missing ready/bar events:\n%s", body)
	}
	// 21 partials published within one flush window arrive as one or two events, the last being the latest.
	parts := strings.Split(body, "event: partial")
	if n := len(parts) - 1; n < 1 || n > 2 || !strings.Contains(parts[len(parts)-1], `"123.45"`) {
		t.Fatalf("partials not coalesced to the latest (%d):\n%s", n, body)
	}
	if !strings.Contains(body, `"mine"`) || strings.Contains(body, `"other"`) {
		t.Fatalf("fill filter:\n%s", body)
	}
	if bus.count() != 0 {
		t.Fatal("subscriptions leaked after disconnect")
	}
}
