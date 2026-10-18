package paper

import (
	"errors"
	"slices"
	"strings"
	"testing"
	"time"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

func (h *Harness) openIntent(strategy, acct, sym, qty, stop string) *ordersv1.OrderIntent {
	hz := commonv1.Horizon_HORIZON_INTRADAY
	if acct == IBKR {
		hz = commonv1.Horizon_HORIZON_SWING
	}
	return h.intent(strategy, acct, sym, commonv1.Side_SIDE_BUY, qty, ordersv1.IntentKind_INTENT_KIND_OPEN, hz, stop, "")
}

func (h *Harness) alertSubjects() []string {
	var out []string
	for _, m := range h.ExecSt.Messages("alert.critical") {
		out = append(out, string(m.Payload))
	}
	return out
}

func containsAlert(subjects []string, s string) bool {
	for _, x := range subjects {
		if strings.Contains(x, s) {
			return true
		}
	}
	return false
}

// Gate 2 disconnect tests (RM-004, RM-009, OE-008): stale data (> 2 bar intervals) blocks new
// intents; fresh data unblocks.
func TestGate2StaleDataBlocksIntents(t *testing.T) {
	h := setup(t, Options{}, nil)
	h.Clock.Add(3*time.Minute + time.Second)
	mustReject(t, h.submit(h.openIntent("orb", Alpaca, "AMD", "5", "78")), "data_fresh")
	h.Fresh(map[string]string{"AMD": "80"})
	h.Quiesce()
	mustApprove(t, h.submit(h.openIntent("orb", Alpaca, "AMD", "5", "78")))
}

// A lost broker stream (and IBKR status 1100, which the IBKR adapter reports the same way as a
// degraded session) halts new intents and pages; resumption only after the adapter resynced, a
// fresh snapshot arrived, and the ledger reconciled cleanly.
func TestGate2LostStreamHaltsPagesAndResumesAfterReconciliation(t *testing.T) {
	h := setup(t, Options{}, nil)
	for _, acct := range []string{Alpaca, IBKR} {
		st := h.Alpaca
		strategy, stop := "orb", "78"
		if acct == IBKR {
			st, strategy, stop = h.IBKR, "swing", "75"
		}
		st.SetConnectError(acct, errors.New("connection refused"))
		st.Disconnect(acct)
		h.WaitFor("session loss seen by execution", 5*time.Second, func() bool {
			for _, a := range h.Exec.Engine.Status().GetAccounts() {
				if a.GetBrokerAccountId() == acct && !a.GetAcceptingIntents() {
					return true
				}
			}
			return false
		})
		mustReject(t, h.submit(h.openIntent(strategy, acct, "AMD", "5", stop)), "session_ready")
		h.WaitFor("page for the lost session", 5*time.Second, func() bool { return containsAlert(h.alertSubjects(), "broker session not ready") })
		st.SetConnectError(acct, nil)
		h.WaitFor("resync and clean reconciliation", 15*time.Second, func() bool {
			for _, a := range h.Exec.Engine.Status().GetAccounts() {
				if a.GetBrokerAccountId() == acct {
					return a.GetAcceptingIntents()
				}
			}
			return false
		})
		h.Fresh(map[string]string{"AMD": "80"})
		mustApprove(t, h.submit(h.openIntent(strategy, acct, "AMD", "5", stop)))
		h.Quiesce()
	}
}

// Dead-man (RM-017): a lost strategy-runtime heartbeat latches a cancel-open kill switch for every
// runtime-hosted strategy; a lost execution heartbeat makes broker-gateway cancel working platform
// orders while keeping protective stops.
func TestGate2DeadmanRuntimeAndExecution(t *testing.T) {
	h := setup(t, Options{ExecDeadman: 600 * time.Millisecond, BrokerDeadman: 400 * time.Millisecond}, nil)
	beat := make(chan struct{})
	go func() {
		for {
			select {
			case <-beat:
				return
			case <-time.After(100 * time.Millisecond):
				h.Exec.Engine.OnRuntimeHeartbeat()
			}
		}
	}()
	time.Sleep(300 * time.Millisecond)
	// The runtime never sent a heartbeat during setup, so the dead-man may already have latched.
	for _, k := range h.Exec.Engine.KillSwitches() {
		if _, err := h.Exec.Engine.Release(h.Ctx, k.GetEventId(), "runtime started", &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"}); err != nil {
			t.Fatal(err)
		}
	}
	it := h.openIntent("orb", Alpaca, "AMD", "5", "70")
	it.OrderType, it.LimitPrice = ordersv1.OrderType_ORDER_TYPE_LIMIT, dv("79")
	mustApprove(t, h.submit(it))
	close(beat)
	h.WaitFor("runtime dead-man kill switch", 5*time.Second, func() bool {
		for _, k := range h.Exec.Engine.KillSwitches() {
			if k.GetTarget() == "orb" && k.GetEngagedBy() == "deadman:strategy-runtime" {
				return true
			}
		}
		return false
	})
	h.Quiesce()
	if oo := h.OpenOrders(Alpaca); len(oo) != 0 {
		t.Fatalf("working order survived the runtime dead-man: %v", oo)
	}
	beat2 := make(chan struct{})
	defer close(beat2)
	go func() {
		for {
			select {
			case <-beat2:
				return
			case <-time.After(100 * time.Millisecond):
				h.Exec.Engine.OnRuntimeHeartbeat()
			}
		}
	}()
	time.Sleep(200 * time.Millisecond)
	for _, k := range h.Exec.Engine.KillSwitches() {
		if _, err := h.Exec.Engine.Release(h.Ctx, k.GetEventId(), "runtime back", &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"}); err != nil {
			t.Fatal(err)
		}
	}
	// Execution heartbeat loss at the broker: a working entry is canceled, the protective stop of a
	// filled position stays.
	mustApprove(t, h.submit(h.openIntent("orb", Alpaca, "NVDA", "10", "45")))
	work := h.openIntent("orb", Alpaca, "XLK", "2", "140")
	work.OrderType, work.LimitPrice = ordersv1.OrderType_ORDER_TYPE_LIMIT, dv("149")
	mustApprove(t, h.submit(work))
	h.Quiesce()
	h.Alpaca.Deadman.Beat()
	h.stopExec()
	time.Sleep(500 * time.Millisecond)
	if n := h.Alpaca.Deadman.Check(h.Ctx); n != 1 {
		t.Fatalf("broker dead-man canceled %d orders, want the 1 working entry", n)
	}
	oo := h.OpenOrders(Alpaca)
	if !slices.Contains(oo, "orbaaaa:NVDA:SIDE_SELL:ORDER_TYPE_STOP") || slices.Contains(oo, "orbaaaa:XLK:SIDE_BUY:ORDER_TYPE_LIMIT") {
		t.Fatalf("after the execution dead-man: %v", oo)
	}
	found := false
	for _, a := range h.Alpaca.Alerts() {
		found = found || a.GetSubject() == "execution heartbeat lost"
	}
	if !found {
		t.Fatal("execution dead-man did not page")
	}
}

// Restart during open orders (section 07 acceptance, OE-020): the new process stays frozen until
// open orders are reconciled with the broker, creates no duplicates, and keeps tracking the order.
func TestGate2RestartRecoversWithoutDuplicates(t *testing.T) {
	h := setup(t, Options{}, nil)
	it := h.openIntent("orb", Alpaca, "AMD", "5", "70")
	it.OrderType, it.LimitPrice = ordersv1.OrderType_ORDER_TYPE_LIMIT, dv("79")
	mustApprove(t, h.submit(it))
	h.Quiesce()
	before := h.OpenOrders(Alpaca)
	h.RestartExecution()
	if r := h.submit(h.openIntent("orb", Alpaca, "NVDA", "5", "45")); r.GetDecision().GetApproved() {
		t.Fatal("approved during startup recovery")
	}
	h.WaitFor("recovery", 10*time.Second, func() bool { return !h.Exec.Engine.Recovering() })
	if dup := h.submit(it); !dup.GetDuplicate() {
		t.Fatal("replayed intent was not deduplicated after restart")
	}
	h.Quiesce()
	if after := h.OpenOrders(Alpaca); !slices.Equal(after, before) {
		t.Fatalf("orders before %v, after %v", before, after)
	}
	h.SetPrice("AMD", "78.5")
	h.WaitFor("fill tracked after restart", 5*time.Second, func() bool {
		for _, o := range h.Exec.Engine.Orders(Alpaca, "orb", false, 0, 0) {
			if o.GetIntentId() == it.GetIntentId() && o.GetRole() == ordersv1.OrderRole_ORDER_ROLE_ENTRY && o.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
				return true
			}
		}
		return false
	})
}

// Unknown submit outcome (OE-003, OE-005): the order is never resubmitted, the strategy is halted for
// the instrument, and the reconciler resolves it by client_order_id.
func TestGate2UnknownSubmitHaltsUntilResolved(t *testing.T) {
	h := setup(t, Options{}, nil)
	h.Alpaca.FailNextSubmitAfterAccept(Alpaca, 1)
	h.Alpaca.FailNextLookups(Alpaca, 3)
	r := h.submit(h.openIntent("orb", Alpaca, "AMD", "5", "78"))
	if len(r.GetOrders()) != 1 || r.GetOrders()[0].GetState() != ordersv1.OrderState_ORDER_STATE_UNKNOWN {
		t.Fatalf("orders = %v, want one UNKNOWN", r.GetOrders())
	}
	mustReject(t, h.submit(h.openIntent("orb", Alpaca, "AMD", "6", "78")), "halt")
	h.WaitFor("unknown order resolved", 10*time.Second, func() bool {
		for _, o := range h.Exec.Engine.Orders(Alpaca, "orb", false, 0, 0) {
			if o.GetOrderId() == r.GetOrders()[0].GetOrderId() && o.GetState() != ordersv1.OrderState_ORDER_STATE_UNKNOWN {
				return true
			}
		}
		return false
	})
	h.Quiesce()
	h.WaitFor("halt cleared", 5*time.Second, func() bool { return len(h.Exec.Engine.Status().GetActiveHalts()) == 0 })
	mustApprove(t, h.submit(h.openIntent("orb", Alpaca, "NVDA", "6", "48")))
	n := 0
	for _, a := range h.Alpaca.AuditActions() {
		if a == "broker.order.submit" {
			n++
		}
	}
	if n != 2 {
		t.Fatalf("broker submits = %d, want 2 (the unknown one was never resubmitted)", n)
	}
}
