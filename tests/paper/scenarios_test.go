package paper

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/shopspring/decimal"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

func dv(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: s} }

func dec(s string) decimal.Decimal { return decimal.RequireFromString(s) }

// intent builds a strategy intent. stop is the protective stop for opens ("" = none).
func (h *Harness) intent(strategy, acct, sym string, side commonv1.Side, qty string, kind ordersv1.IntentKind, horizon commonv1.Horizon, stop, tp string) *ordersv1.OrderIntent {
	it := &ordersv1.OrderIntent{IntentId: ulid.Make().String(), StrategyId: strategy, BrokerAccountId: acct, InstrumentId: h.Instr[sym], Side: side,
		Sizing: &ordersv1.OrderIntent_Quantity{Quantity: dv(qty)}, OrderType: ordersv1.OrderType_ORDER_TYPE_MARKET, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY,
		Mode: commonv1.Mode_MODE_PAPER, CorrelationId: ulid.Make().String(), CreatedAt: h.Clock.Now().UnixNano(), Horizon: horizon, Kind: kind,
		Source: ordersv1.IntentSource_INTENT_SOURCE_STRATEGY, DecisionTime: h.Clock.Now().UnixNano()}
	if stop != "" {
		tif := ordersv1.TimeInForce_TIME_IN_FORCE_DAY
		if horizon == commonv1.Horizon_HORIZON_SWING {
			tif = ordersv1.TimeInForce_TIME_IN_FORCE_GTC
		}
		it.Protective = &ordersv1.ProtectiveSpec{StopLossPrice: dv(stop), Tif: tif}
		if tp != "" {
			it.Protective.TakeProfitPrice = dv(tp)
		}
	}
	return it
}

func (h *Harness) submit(it *ordersv1.OrderIntent) *ordersv1.SubmitIntentResponse {
	h.T.Helper()
	r, err := h.Exec.Engine.Submit(h.Ctx, it, nil)
	if err != nil {
		h.T.Fatalf("submit: %v", err)
	}
	return r
}

func failed(r *ordersv1.SubmitIntentResponse) string {
	return r.GetDecision().GetFirstFailure()
}

func mustApprove(t *testing.T, r *ordersv1.SubmitIntentResponse) {
	t.Helper()
	if !r.GetDecision().GetApproved() {
		for _, c := range r.GetDecision().GetChecks() {
			if !c.GetPassed() {
				t.Logf("  %s: %s (value %s limit %s)", c.GetName(), c.GetDetail(), c.GetValue(), c.GetLimit())
			}
		}
		t.Fatalf("intent rejected by %s", failed(r))
	}
}

func mustReject(t *testing.T, r *ordersv1.SubmitIntentResponse, check string) {
	t.Helper()
	if r.GetDecision().GetApproved() {
		t.Fatalf("intent approved, want rejection by %s", check)
	}
	for _, c := range r.GetDecision().GetChecks() {
		if c.GetName() == check && !c.GetPassed() {
			return
		}
	}
	t.Fatalf("rejected by %s, want %s", failed(r), check)
}

// setup registers an intraday strategy at the Alpaca-shaped account and a swing strategy at the
// IBKR-shaped one, publishes fresh prices, and waits for the ledger to know both.
func setup(t *testing.T, o Options, allow []string) *Harness {
	h := New(t, o)
	h.Strategy("orb", "orbaaaa", commonv1.Horizon_HORIZON_INTRADAY, Alpaca, "12000", allow, true)
	h.Strategy("swing", "swingaa", commonv1.Horizon_HORIZON_SWING, IBKR, "40000", nil, false)
	h.Fresh(map[string]string{"AAPL": "100", "MSFT": "200", "NVDA": "50", "AMD": "80", "SPY": "400", "XLK": "150"})
	h.Quiesce()
	return h
}

// Gate 2 same-ticker test (section 06/07 acceptance; VP-003, VP-004, VP-005, VP-006, RM-014): with
// a manual long in AAPL, a strategy allowlisted for AAPL buys and sells it; the manual quantity is
// unchanged at the broker and in the ledger; a sell beyond its own quantity and a short are rejected.
func TestGate2SameTickerManualHoldingUntouched(t *testing.T) {
	h := setup(t, Options{}, []string{InstrumentID("AAPL")})
	h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 100)
	h.Quiesce()
	if got := h.Owners(Alpaca, "AAPL"); got["manual"] != "100" || got["broker"] != "100" {
		t.Fatalf("manual buy not booked to manual: %v", got)
	}
	r := h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "50", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "98", "105"))
	mustApprove(t, r)
	h.Quiesce()
	if got := h.Owners(Alpaca, "AAPL"); got["manual"] != "100" || got["orb"] != "50" || got["broker"] != "150" {
		t.Fatalf("after strategy buy: %v", got)
	}
	if oo := h.OpenOrders(Alpaca); !slices.Contains(oo, "orbaaaa:AAPL:SIDE_SELL:ORDER_TYPE_STOP") {
		t.Fatalf("entry has no broker-side stop: %v", oo)
	}
	mustReject(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_SELL, "60", ordersv1.IntentKind_INTENT_KIND_CLOSE, commonv1.Horizon_HORIZON_INTRADAY, "", "")), "ownership")
	mustReject(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_SELL_SHORT, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "103", "")), "ownership")
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_SELL, "50", ordersv1.IntentKind_INTENT_KIND_CLOSE, commonv1.Horizon_HORIZON_INTRADAY, "", "")))
	h.Quiesce()
	if got := h.Owners(Alpaca, "AAPL"); got["manual"] != "100" || got["orb"] != "" || got["broker"] != "100" {
		t.Fatalf("after strategy sell: %v (manual must stay 100 at broker and ledger)", got)
	}
	if oo := h.OpenOrders(Alpaca); len(oo) != 0 {
		t.Fatalf("resting orders left after the exit: %v", oo)
	}
	if r := h.Recon(Alpaca); r.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK {
		t.Fatalf("recon %s %v", r.GetStatus(), r.GetDiffs())
	}
}

// Symbol segregation (RM-014): a strategy may not trade a manually held instrument unless
// allowlisted; self-cross (OE-018): opposite-side resting orders across owners are refused and the
// owner's own legs are canceled before its exit.
func TestGate2SymbolSegregationAndSelfCross(t *testing.T) {
	h := setup(t, Options{}, nil)
	h.ManualFill(Alpaca, "MSFT", commonv1.Side_SIDE_BUY, 10)
	h.Quiesce()
	mustReject(t, h.submit(h.intent("orb", Alpaca, "MSFT", commonv1.Side_SIDE_BUY, "5", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "195", "")), "symbol_segregation")

	h.Strategy("orb2", "orbbbbb", commonv1.Horizon_HORIZON_INTRADAY, Alpaca, "4000", nil, false)
	h.Quiesce()
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "NVDA", commonv1.Side_SIDE_BUY, "20", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "48", "55")))
	h.Quiesce()
	// orb's protective sell legs rest in NVDA: orb2's buy would cross them at the broker.
	mustReject(t, h.submit(h.intent("orb2", Alpaca, "NVDA", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "48", "")), "self_cross")
	// orb adding to the position would face its own opposite legs: refused too.
	mustReject(t, h.submit(h.intent("orb", Alpaca, "NVDA", commonv1.Side_SIDE_BUY, "5", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "48", "")), "self_cross")
	// orb's exit cancels its own legs first (cancel, then replace with the exit).
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "NVDA", commonv1.Side_SIDE_SELL, "20", ordersv1.IntentKind_INTENT_KIND_CLOSE, commonv1.Horizon_HORIZON_INTRADAY, "", "")))
	h.Quiesce()
	if oo := h.OpenOrders(Alpaca); len(oo) != 0 {
		t.Fatalf("open orders after exit: %v", oo)
	}
	mustApprove(t, h.submit(h.intent("orb2", Alpaca, "NVDA", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "48", "")))
}

// Routing (RM-011, D30): intraday → Alpaca only, swing → IBKR only; a swing entry at the IBKR
// adapter (no bracket support) gets a separate GTC protective stop once filled (OE-014); the
// intraday-margin guard rejects beyond the cap (RM-012); an IMD halts the account (RM-013).
func TestGate2RoutingMarginGuardAndIMD(t *testing.T) {
	h := setup(t, Options{}, nil)
	mustReject(t, h.submit(h.intent("orb", IBKR, "AMD", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "78", "")), "routing")
	mustReject(t, h.submit(h.intent("swing", Alpaca, "AMD", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_SWING, "75", "")), "routing")
	// Swing at IBKR without a GTC stop is refused (RM-011).
	day := h.intent("swing", IBKR, "AMD", commonv1.Side_SIDE_BUY, "100", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_SWING, "75", "")
	day.Protective.Tif = ordersv1.TimeInForce_TIME_IN_FORCE_DAY
	mustReject(t, h.submit(day), "protective_order")
	r := h.submit(h.intent("swing", IBKR, "AMD", commonv1.Side_SIDE_BUY, "100", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_SWING, "75", ""))
	mustApprove(t, r)
	h.Quiesce()
	if co := r.GetOrders()[0].GetClientOrderId(); len(co) > 44 {
		t.Fatalf("attribution ID %q exceeds the IBKR orderRef limit", co)
	}
	h.WaitFor("GTC protective stop at IBKR", 5*time.Second, func() bool {
		return slices.Contains(h.OpenOrders(IBKR), "swingaa:AMD:SIDE_SELL:ORDER_TYPE_STOP")
	})
	var stop *ordersv1.Order
	for _, o := range h.Exec.Engine.Orders(IBKR, "swing", true, 0, 0) {
		if o.GetRole() == ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP {
			stop = o
		}
	}
	if stop == nil || stop.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_GTC || stop.GetQuantity().GetValue() != "100" {
		t.Fatalf("protective stop = %v", stop)
	}
	if got := h.Owners(IBKR, "AMD"); got["swing"] != "100" {
		t.Fatalf("IBKR ledger %v", got)
	}
	// Margin guard at IBKR: cap = min(Σ allocations 40k, equity 100k × 2 × 0.5 = 100k) → 40k;
	// 8k held, so a 33k buy is refused and a 30k buy passes.
	mustReject(t, h.submit(h.intent("swing", IBKR, "SPY", commonv1.Side_SIDE_BUY, "82", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_SWING, "390", "")), "margin_guard")
	// Margin guard at Alpaca (1x, no leverage): the whole account may not exceed equity.
	h.ManualFill(Alpaca, "SPY", commonv1.Side_SIDE_BUY, 45)
	h.Quiesce()
	mustReject(t, h.submit(h.intent("orb", Alpaca, "XLK", commonv1.Side_SIDE_BUY, "20", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "148", "")), "margin_guard")
	// IMD at IBKR halts the account: kill switch latched, intents refused, working orders canceled.
	h.IBKR.SetMargin(IBKR, false, true)
	h.WaitFor("IMD kill switch", 5*time.Second, func() bool {
		for _, k := range h.Exec.Engine.KillSwitches() {
			if k.GetTarget() == IBKR && k.GetEngagedBy() == "risk:margin" {
				return true
			}
		}
		return false
	})
	rr := h.submit(h.intent("swing", IBKR, "MSFT", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_SWING, "190", ""))
	if rr.GetDecision().GetApproved() {
		t.Fatal("intent approved during an IMD")
	}
	if !slices.Contains(h.OpenOrders(IBKR), "swingaa:AMD:SIDE_SELL:ORDER_TYPE_STOP") {
		t.Fatal("the IMD halt must keep the protective stop")
	}
}

// Kill switches (RM-006, RM-007): every scope latches and freezes; cancel-open cancels
// strategy-owned working orders within 2 s and never manual orders; flatten closes strategy-owned
// quantity only; release needs a desktop passkey step-up.
func TestGate2KillSwitchesLatchFreezeCancelFlatten(t *testing.T) {
	h := setup(t, Options{AlpacaCash: dec("200000")}, []string{InstrumentID("AAPL")})
	ctx := context.Background()
	h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 100)
	h.Quiesce()
	for scope, target := range map[ordersv1.KillSwitchScope]string{
		ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_INSTRUMENT:     h.Instr["NVDA"],
		ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY:       "orb",
		ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT: Alpaca,
		ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL:         "",
	} {
		k, err := h.Exec.Engine.Trigger(ctx, scope, target, ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FREEZE, "drill", &ordersv1.Actor{Name: "owner"})
		if err != nil {
			t.Fatal(err)
		}
		mustReject(t, h.submit(h.intent("orb", Alpaca, "NVDA", commonv1.Side_SIDE_BUY, "1", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "48", "")), "kill_switch")
		if _, err := h.ExecClient.ReleaseKillSwitch(ctx, &ordersv1.ReleaseKillSwitchRequest{EventId: k.GetKillSwitch().GetEventId(), Actor: &ordersv1.Actor{Name: "owner"}}); status.Code(err) != codes.PermissionDenied {
			t.Fatalf("release without step-up: %v", err)
		}
		if _, err := h.ExecClient.ReleaseKillSwitch(ctx, &ordersv1.ReleaseKillSwitchRequest{EventId: k.GetKillSwitch().GetEventId(), Actor: &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey", Mobile: true}}); status.Code(err) != codes.PermissionDenied {
			t.Fatalf("release from mobile: %v", err)
		}
		if _, err := h.ExecClient.ReleaseKillSwitch(ctx, &ordersv1.ReleaseKillSwitchRequest{EventId: k.GetKillSwitch().GetEventId(), Actor: &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"}}); err != nil {
			t.Fatalf("release with desktop step-up: %v", err)
		}
	}
	// 20 working strategy orders (unmarketable limits) + a strategy position in AAPL.
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "30", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "97", "")))
	h.Quiesce()
	h.Strategy("drill", "drillaa", commonv1.Horizon_HORIZON_INTRADAY, Alpaca, "100000", nil, false)
	h.Quiesce()
	for i := 0; i < 20; i++ {
		it := h.intent("drill", Alpaca, []string{"NVDA", "XLK"}[i%2], commonv1.Side_SIDE_BUY, fmt.Sprint(1+i), ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "", "")
		it.OrderType, it.LimitPrice = ordersv1.OrderType_ORDER_TYPE_LIMIT, dv(map[bool]string{true: "49.50", false: "148"}[i%2 == 0])
		if i%2 == 0 {
			it.Protective = &ordersv1.ProtectiveSpec{StopLossPrice: dv("45"), Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY}
		} else {
			it.Protective = &ordersv1.ProtectiveSpec{StopLossPrice: dv("140"), Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY}
		}
		it.Kind = ordersv1.IntentKind_INTENT_KIND_OPEN
		mustApprove(t, h.submit(it))
	}
	h.Quiesce()
	resp, err := h.Exec.Engine.Trigger(ctx, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL, "", ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN, "drill", &ordersv1.Actor{Name: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	if resp.GetOrdersCanceled() != 20 || time.Duration(resp.GetKillSwitch().GetCancelCompletedNs()) > 2*time.Second {
		t.Fatalf("global cancel: %d orders in %s, want 20 within 2s", resp.GetOrdersCanceled(), time.Duration(resp.GetKillSwitch().GetCancelCompletedNs()))
	}
	h.Quiesce()
	oo := h.OpenOrders(Alpaca)
	for _, o := range oo {
		if !strings.HasPrefix(o, "manual:") && !strings.HasSuffix(o, "ORDER_TYPE_STOP") {
			t.Fatalf("working strategy order survived the global cancel: %v", oo)
		}
	}
	if !slices.Contains(oo, "orbaaaa:AAPL:SIDE_SELL:ORDER_TYPE_STOP") {
		t.Fatalf("cancel-open must keep broker-side protection: %v", oo)
	}
	if _, err := h.Exec.Engine.Release(ctx, resp.GetKillSwitch().GetEventId(), "drill done", &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"}); err != nil {
		t.Fatal(err)
	}
	// Flatten the strategy: only its 30 AAPL leave; manual's 100 stay.
	fr, err := h.Exec.Engine.Trigger(ctx, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, "orb", ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED, "drill", &ordersv1.Actor{Name: "owner"})
	if err != nil || fr.GetFlattenIntents() != 1 {
		t.Fatalf("flatten: %v %v", fr, err)
	}
	h.Quiesce()
	if got := h.Owners(Alpaca, "AAPL"); got["manual"] != "100" || got["orb"] != "" || got["broker"] != "100" {
		t.Fatalf("after flatten %v", got)
	}
	if acts := h.ExecSt.AuditActions(); !slices.Contains(acts, "execution.killswitch.engage") || !slices.Contains(acts, "execution.killswitch.release") {
		t.Fatalf("kill switch actions not audited: %v", acts)
	}
}

// Risk decisions are audited with every check value (RM-002).
func TestGate2EveryDecisionAudited(t *testing.T) {
	h := setup(t, Options{}, nil)
	before := len(h.ExecSt.AuditActions())
	mustReject(t, h.submit(h.intent("orb", Alpaca, "AMD", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "", "")), "protective_order")
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "AMD", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "78", "")))
	n := 0
	for _, a := range h.ExecSt.AuditSnapshot()[before:] {
		if a.Action == "execution.risk.decision" {
			n++
			if _, ok := a.Details["checks"]; !ok {
				t.Fatal("decision audit lacks check values")
			}
		}
	}
	if n != 2 {
		t.Fatalf("decision audits = %d, want 2", n)
	}
}
