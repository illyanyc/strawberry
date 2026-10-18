package paper

import (
	"context"
	"slices"
	"strings"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	execapp "github.com/illyanyc/strawberry/services/execution/app"
)

func (h *Harness) mode(id string) ordersv1.StrategyMode {
	for _, s := range h.Exec.Engine.Strategies() {
		if s.GetStrategyId() == id {
			return s.GetMode()
		}
	}
	return ordersv1.StrategyMode_STRATEGY_MODE_UNSPECIFIED
}

func (h *Harness) workingLimit(strategy, sym, limit, stop string) {
	h.T.Helper()
	it := h.intent(strategy, Alpaca, sym, commonv1.Side_SIDE_BUY, "5", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, stop, "")
	it.OrderType, it.LimitPrice = ordersv1.OrderType_ORDER_TYPE_LIMIT, dv(limit)
	mustApprove(h.T.(*testing.T), h.submit(it))
}

// RM-018, SM-006, MN-035: automatic demotion of a paper strategy halts it, cancels its working
// entry orders through the broker, keeps the broker-side protective stop on its open position, keeps
// its own quantity (demotion is not a flatten), never touches the manual holding in the same
// symbol, and is audited and alerted. Repeating it changes nothing.
func TestRM018DemotePaperStrategyToHaltedReducesRiskOnly(t *testing.T) {
	h := setup(t, Options{AlpacaCash: dec("200000")}, []string{InstrumentID("AAPL")})
	ctx := context.Background()
	h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 100)
	h.Quiesce()
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "30", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "97", "")))
	h.Quiesce()
	h.workingLimit("orb", "NVDA", "49.50", "45")
	h.Quiesce()

	r, err := h.ExecClient.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "orb", Reason: "divergence breach: match rate 0.55 < 0.80",
		EvidenceId: "dv-test-1", Actor: &ordersv1.Actor{Name: "service:monitor"}})
	if err != nil {
		t.Fatal(err)
	}
	if !r.GetChanged() || r.GetFrom() != ordersv1.StrategyMode_STRATEGY_MODE_PAPER || r.GetStrategy().GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_HALTED {
		t.Fatalf("demote: %v", r)
	}
	if r.GetOrdersCanceled() != 1 || r.GetKillSwitchEventId() != "" {
		t.Fatalf("paper demotion should cancel the one working entry and not latch a kill switch: %v", r)
	}
	h.Quiesce()
	oo := h.OpenOrders(Alpaca)
	if !slices.Contains(oo, "orbaaaa:AAPL:SIDE_SELL:ORDER_TYPE_STOP") {
		t.Fatalf("protective stop must stay on the open position: %v", oo)
	}
	for _, o := range oo {
		if strings.HasPrefix(o, "orbaaaa:NVDA") {
			t.Fatalf("working entry survived the demotion: %v", oo)
		}
	}
	if got := h.Owners(Alpaca, "AAPL"); got["manual"] != "100" || got["orb"] != "30" {
		t.Fatalf("demotion must not flatten or touch manual: %v", got)
	}
	mustReject(t, h.submit(h.intent("orb", Alpaca, "MSFT", commonv1.Side_SIDE_BUY, "1", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "190", "")), "mode")
	if !containsAlert(h.alertSubjects(), "strategy demoted") {
		t.Fatalf("no critical demotion alert: %v", h.alertSubjects())
	}
	var found bool
	for _, a := range h.ExecSt.AuditSnapshot() {
		if a.Action == "execution.strategy.demote" && a.Target == "orb" && a.Details["evidence_id"] == "dv-test-1" && a.Details["to"] == "STRATEGY_MODE_HALTED" {
			found = true
		}
	}
	if !found {
		t.Fatal("demotion not audited with its evidence")
	}
	again, err := h.ExecClient.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "orb", Reason: "repeat", Actor: &ordersv1.Actor{Name: "service:monitor"}})
	if err != nil || again.GetChanged() || again.GetStrategy().GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_HALTED {
		t.Fatalf("second demotion must be a no-op: %v %v", again, err)
	}
}

// RM-018, RM-006: a strategy in live mode (as the section 15 flow would persist it) is demoted to
// paper through a latched strategy kill switch: its working orders are canceled, its protection
// stays, new intents are blocked until the owner releases the latch, and manual is untouched.
func TestRM018DemoteLiveStrategyToPaperThroughLatchedKill(t *testing.T) {
	h := setup(t, Options{AlpacaCash: dec("200000")}, []string{InstrumentID("AAPL")})
	ctx := context.Background()
	h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 100)
	h.Quiesce()
	mustApprove(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "20", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "97", "")))
	h.Quiesce()
	h.workingLimit("orb", "NVDA", "49.50", "45")
	h.Quiesce()
	var live *ordersv1.Strategy
	for _, s := range h.Exec.Engine.Strategies() {
		if s.GetStrategyId() == "orb" {
			live = proto.Clone(s).(*ordersv1.Strategy)
		}
	}
	live.Mode, live.ModeReason, live.Limits = ordersv1.StrategyMode_STRATEGY_MODE_LIVE, "simulated live account (section 15 promotion)", nil
	if err := h.ExecSt.Commit(ctx, &execapp.Tx{Strategies: []*ordersv1.Strategy{live}}); err != nil {
		t.Fatal(err)
	}
	h.RestartExecution()
	h.WaitFor("execution recovery", 15e9, func() bool { return !h.Exec.Engine.Recovering() })
	h.Fresh(map[string]string{"AAPL": "100", "MSFT": "200", "NVDA": "50", "AMD": "80", "SPY": "400", "XLK": "150"})
	h.Quiesce()
	if h.mode("orb") != ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
		t.Fatalf("setup: orb not live: %s", h.mode("orb"))
	}

	r, err := h.ExecClient.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "orb", Reason: "drawdown 4.1% > 1.5x backtest p95 2.6%",
		EvidenceId: "perf-test-1", Actor: &ordersv1.Actor{Name: "service:monitor"}})
	if err != nil {
		t.Fatal(err)
	}
	if !r.GetChanged() || r.GetFrom() != ordersv1.StrategyMode_STRATEGY_MODE_LIVE || r.GetStrategy().GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_PAPER || r.GetKillSwitchEventId() == "" {
		t.Fatalf("live demotion: %v", r)
	}
	h.Quiesce()
	oo := h.OpenOrders(Alpaca)
	if !slices.Contains(oo, "orbaaaa:AAPL:SIDE_SELL:ORDER_TYPE_STOP") || slices.ContainsFunc(oo, func(o string) bool { return strings.HasPrefix(o, "orbaaaa:NVDA") }) {
		t.Fatalf("live demotion must cancel working entries and keep protection: %v", oo)
	}
	if got := h.Owners(Alpaca, "AAPL"); got["manual"] != "100" || got["orb"] != "20" {
		t.Fatalf("live demotion touched positions: %v", got)
	}
	mustReject(t, h.submit(h.intent("orb", Alpaca, "MSFT", commonv1.Side_SIDE_BUY, "1", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "190", "")), "kill_switch")
	acts := h.ExecSt.AuditActions()
	if !slices.Contains(acts, "execution.strategy.demote") || !slices.Contains(acts, "execution.killswitch.engage") {
		t.Fatalf("live demotion not audited: %v", acts)
	}
	if _, err := h.ExecClient.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "manual", Reason: "x"}); status.Code(err) != codes.InvalidArgument {
		t.Fatalf("manual must never be demoted: %v", err)
	}
	if _, err := h.ExecClient.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "orb"}); status.Code(err) != codes.InvalidArgument {
		t.Fatalf("a reason is required: %v", err)
	}
}
