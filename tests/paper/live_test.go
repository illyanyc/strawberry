package paper

import (
	"strings"
	"testing"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// TestLiveGate is the section 15 order-path check on the paper harness (simulators only, GL-050, GL-052).
// A live-mode intent cannot be placed with the paper account's credential, and nothing except
// PromoteToLive can mark a strategy live.
func TestLiveGate(t *testing.T) {
	h := setup(t, Options{}, nil)
	ctx := h.Ctx
	if _, err := h.Exec.Engine.SetMode(ctx, "orb", ordersv1.StrategyMode_STRATEGY_MODE_LIVE, "config", "owner"); err == nil || !strings.Contains(err.Error(), "go-live") {
		t.Fatalf("config/API SetMode live: %v", err)
	}
	if _, err := h.Exec.Engine.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: "orb", LiveAccountId: "sim-live",
		CapitalCap: dv("1000"), IntendedAllocation: dv("10000"), Confirmation: "orb",
		Actor: &ordersv1.Actor{Name: "owner"}}); err == nil || !strings.Contains(err.Error(), "step-up") {
		t.Fatalf("promote without step-up: %v", err)
	}
	if _, err := h.Exec.Engine.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: "orb", LiveAccountId: "sim-live",
		CapitalCap: dv("1000"), IntendedAllocation: dv("10000"), Confirmation: "orb",
		Actor: &ordersv1.Actor{Name: "assistant", StepUpMethod: "passkey"}}); err == nil || !strings.Contains(err.Error(), "analyst") {
		t.Fatalf("analyst promote: %v", err)
	}
	it := h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "98", "105")
	it.Mode = commonv1.Mode_MODE_LIVE
	r := h.submit(it)
	if !r.GetDecision().GetApproved() {
		t.Fatalf("paper strategy on a paper account is a valid intent; the adapter must be what refuses the live mode, rejected by %s", failed(r))
	}
	h.Quiesce()
	if got := h.Owners(Alpaca, "AAPL"); got["orb"] != "" || got["broker"] != "" {
		t.Fatalf("live-mode intent reached the paper broker: %v", got)
	}
	rejected := false
	for _, o := range h.Exec.Engine.Orders(Alpaca, "orb", false, 0, 20) {
		if o.GetMode() == commonv1.Mode_MODE_LIVE && o.GetState() == ordersv1.OrderState_ORDER_STATE_REJECTED {
			rejected = true
		}
	}
	if !rejected {
		t.Fatal("paper adapter did not reject the live-mode order")
	}
	// Kill switch still freezes the paper strategy (same gate as live).
	if _, err := h.Exec.Engine.Trigger(ctx, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, "orb",
		ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FREEZE, "live-path drill", &ordersv1.Actor{Name: "owner"}); err != nil {
		t.Fatal(err)
	}
	mustReject(t, h.submit(h.intent("orb", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "98", "105")), "kill_switch")
}
