package adapter_test

import (
	"context"
	"errors"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/contract"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/execstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/session"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/testkit"
)

func d(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: s} }

func testLog() *slog.Logger { return slog.New(slog.DiscardHandler) }

func baseReq() *brokerv1.BrokerOrderRequest {
	return &brokerv1.BrokerOrderRequest{ClientOrderId: contract.NewClientOrderID(), BrokerAccountId: "sim-paper",
		Instrument: &commonv1.Instrument{Symbol: "AAPL"}, Side: commonv1.Side_SIDE_BUY, Qty: d("10"),
		Type: ordersv1.OrderType_ORDER_TYPE_LIMIT, LimitPrice: d("100.00"), Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY}
}

func TestBR002CapabilityRejects(t *testing.T) {
	caps := sim.Capabilities() // no trailing stops, no OPG/CLS
	cases := map[string]func(r *brokerv1.BrokerOrderRequest){
		"unsupported tif":             func(r *brokerv1.BrokerOrderRequest) { r.Tif = ordersv1.TimeInForce_TIME_IN_FORCE_OPG },
		"unsupported type":            func(r *brokerv1.BrokerOrderRequest) { r.Type = ordersv1.OrderType_ORDER_TYPE_TRAILING_STOP },
		"no side":                     func(r *brokerv1.BrokerOrderRequest) { r.Side = commonv1.Side_SIDE_UNSPECIFIED },
		"zero qty":                    func(r *brokerv1.BrokerOrderRequest) { r.Qty = d("0") },
		"negative qty":                func(r *brokerv1.BrokerOrderRequest) { r.Qty = d("-1") },
		"malformed qty":               func(r *brokerv1.BrokerOrderRequest) { r.Qty = d("ten") },
		"limit without price":         func(r *brokerv1.BrokerOrderRequest) { r.LimitPrice = nil },
		"market with price":           func(r *brokerv1.BrokerOrderRequest) { r.Type = ordersv1.OrderType_ORDER_TYPE_MARKET },
		"stop without stop":           func(r *brokerv1.BrokerOrderRequest) { r.Type = ordersv1.OrderType_ORDER_TYPE_STOP },
		"stop limit without stop":     func(r *brokerv1.BrokerOrderRequest) { r.Type = ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT },
		"negative price":              func(r *brokerv1.BrokerOrderRequest) { r.LimitPrice = d("-5") },
		"fractional non-fractionable": func(r *brokerv1.BrokerOrderRequest) { r.Qty = d("1.5") },
		"extended hours market": func(r *brokerv1.BrokerOrderRequest) {
			r.ExtendedHours, r.Type, r.LimitPrice = true, ordersv1.OrderType_ORDER_TYPE_MARKET, nil
		},
		"extended hours ioc": func(r *brokerv1.BrokerOrderRequest) {
			r.ExtendedHours, r.Tif = true, ordersv1.TimeInForce_TIME_IN_FORCE_IOC
		},
		"bracket missing stop": func(r *brokerv1.BrokerOrderRequest) {
			r.Bracket = &brokerv1.BracketSpec{OrderClass: brokerv1.OrderClass_ORDER_CLASS_BRACKET, TakeProfitLimitPrice: d("110")}
		},
		"bracket legs inverted": func(r *brokerv1.BrokerOrderRequest) {
			r.Bracket = &brokerv1.BracketSpec{OrderClass: brokerv1.OrderClass_ORDER_CLASS_BRACKET, TakeProfitLimitPrice: d("90"), StopLossStopPrice: d("95")}
		},
		"bracket in extended hours": func(r *brokerv1.BrokerOrderRequest) {
			r.ExtendedHours = true
			r.Bracket = &brokerv1.BracketSpec{OrderClass: brokerv1.OrderClass_ORDER_CLASS_BRACKET, TakeProfitLimitPrice: d("110"), StopLossStopPrice: d("95")}
		},
		"no broker identifier": func(r *brokerv1.BrokerOrderRequest) {
			r.Instrument = &commonv1.Instrument{InstrumentId: "01J0000000000000000000000X"}
		},
	}
	for name, mut := range cases {
		r := baseReq()
		mut(r)
		rej := adapter.Validate(r, caps, false)
		if rej == nil || !rej.Reason.GetLocal() || rej.Reason.GetCode() != brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER {
			t.Errorf("%s: not rejected locally as INVALID_ORDER: %+v", name, rej)
		}
	}
	noShort := sim.Capabilities()
	noShort.SupportsShort = false
	r := baseReq()
	r.Side = commonv1.Side_SIDE_SELL_SHORT
	if adapter.Validate(r, noShort, false) == nil {
		t.Error("short accepted on an account without short support")
	}
	ok := baseReq()
	ok.Bracket = &brokerv1.BracketSpec{OrderClass: brokerv1.OrderClass_ORDER_CLASS_BRACKET, TakeProfitLimitPrice: d("110"), StopLossStopPrice: d("95")}
	if rej := adapter.Validate(ok, caps, false); rej != nil {
		t.Errorf("valid bracket rejected: %v", rej)
	}
	frac := baseReq()
	frac.Qty = d("0.5")
	if rej := adapter.Validate(frac, caps, true); rej != nil {
		t.Errorf("fractional DAY limit on a fractionable instrument rejected: %v", rej)
	}
}

func TestBR017TickRounding(t *testing.T) {
	r := baseReq()
	r.LimitPrice = d("100.129")
	if got := adapter.RoundPrices(r, decimal.Zero).GetLimitPrice().GetValue(); got != "100.12" {
		t.Errorf("buy limit must round down: %s", got)
	}
	r.Side = commonv1.Side_SIDE_SELL
	if got := adapter.RoundPrices(r, decimal.Zero).GetLimitPrice().GetValue(); got != "100.13" {
		t.Errorf("sell limit must round up: %s", got)
	}
	r = baseReq()
	r.Type, r.LimitPrice, r.StopPrice = ordersv1.OrderType_ORDER_TYPE_STOP, nil, d("50.001")
	if got := adapter.RoundPrices(r, decimal.Zero).GetStopPrice().GetValue(); got != "50.01" {
		t.Errorf("buy stop must round up: %s", got)
	}
	r.Side = commonv1.Side_SIDE_SELL
	if got := adapter.RoundPrices(r, decimal.Zero).GetStopPrice().GetValue(); got != "50" {
		t.Errorf("sell stop must round down: %s", got)
	}
	r = baseReq()
	r.LimitPrice = d("0.123456")
	if got := adapter.RoundPrices(r, decimal.Zero).GetLimitPrice().GetValue(); got != "0.1234" {
		t.Errorf("sub-dollar tick is 0.0001: %s", got)
	}
	r = baseReq()
	r.LimitPrice = d("100.07")
	if got := adapter.RoundPrices(r, decimal.RequireFromString("0.05")).GetLimitPrice().GetValue(); got != "100.05" {
		t.Errorf("instrument tick 0.05: %s", got)
	}
	r = baseReq()
	r.Bracket = &brokerv1.BracketSpec{OrderClass: brokerv1.OrderClass_ORDER_CLASS_BRACKET, TakeProfitLimitPrice: d("110.001"), StopLossStopPrice: d("95.009")}
	b := adapter.RoundPrices(r, decimal.Zero).GetBracket()
	if b.GetTakeProfitLimitPrice().GetValue() != "110.01" || b.GetStopLossStopPrice().GetValue() != "95" {
		t.Errorf("bracket legs (sell exits): tp=%s sl=%s", b.GetTakeProfitLimitPrice().GetValue(), b.GetStopLossStopPrice().GetValue())
	}
	if r.GetLimitPrice().GetValue() != "100.00" || r.GetBracket().GetTakeProfitLimitPrice().GetValue() != "110.001" {
		t.Error("RoundPrices mutated its input")
	}
}

func ready(t *testing.T, cfg sim.Config) (*sim.Sim, *adapter.Account, *execstore.Memory, *testkit.Audit) {
	t.Helper()
	s, acct, store, aud := testkit.SimAccount(t, cfg)
	testkit.Run(t, acct)
	testkit.WaitState(t, acct, session.Ready)
	return s, acct, store, aud
}

func marketReq(qty string) *brokerv1.BrokerOrderRequest {
	r := baseReq()
	r.Type, r.LimitPrice, r.Qty = ordersv1.OrderType_ORDER_TYPE_MARKET, nil, d(qty)
	return r
}

func TestBR008DuplicateExecutionsPublishedOnce(t *testing.T) {
	_, acct, store, _ := ready(t, sim.Config{DuplicateExecs: true, PartialFills: 3})
	execs, cancel := acct.Fanout().SubscribeExecutions()
	defer cancel()
	resp, err := acct.Submit(context.Background(), marketReq("9"), false, decimal.Zero)
	if err != nil || resp.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED {
		t.Fatalf("%+v %v", resp, err)
	}
	time.Sleep(200 * time.Millisecond)
	ids := map[string]int{}
	for _, m := range store.Messages(execstore.ExecSubject("sim-paper")) {
		ids[m.MsgID]++
	}
	if len(ids) != 3 {
		t.Fatalf("want 3 distinct executions, got %v", ids)
	}
	for id, n := range ids {
		if n != 1 {
			t.Fatalf("%s published %d times", id, n)
		}
	}
	streamed := 0
	for len(execs) > 0 {
		<-execs
		streamed++
	}
	if streamed != 3 {
		t.Fatalf("StreamExecutions emitted %d (BR-008 dedupe)", streamed)
	}
}

func TestBR009OutOfOrderFills(t *testing.T) {
	_, acct, store, _ := ready(t, sim.Config{OutOfOrder: true, PartialFills: 3})
	resp, _ := acct.Submit(context.Background(), marketReq("9"), false, decimal.Zero)
	time.Sleep(200 * time.Millisecond)
	var last decimal.Decimal
	for _, m := range store.Messages(execstore.OrderSubject("sim-paper")) {
		u := m.Msg.(*brokerv1.BrokerOrderUpdate)
		if u.GetOrder().GetBrokerOrderId() != resp.GetOrder().GetBrokerOrderId() {
			continue
		}
		f, _ := decimal.NewFromString(u.GetOrder().GetFilledQty().GetValue())
		if f.LessThan(last) {
			t.Fatalf("filled qty moved backwards: %s after %s", f, last)
		}
		last = f
	}
	if !last.Equal(decimal.NewFromInt(9)) {
		t.Fatalf("final filled qty %s", last)
	}
	if n := len(store.Messages(execstore.ExecSubject("sim-paper"))); n != 3 {
		t.Fatalf("executions %d", n)
	}
}

func TestBR014AuditOnSubmitCancelReplaceReject(t *testing.T) {
	_, acct, _, aud := ready(t, sim.Config{RejectSymbols: []string{"HALT"}})
	ctx := context.Background()
	r := baseReq()
	r.LimitPrice = d("1.00")
	if resp, _ := acct.Submit(ctx, r, false, decimal.Zero); resp.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED {
		t.Fatal(resp)
	}
	next := r.GetClientOrderId()[:strings.LastIndex(r.GetClientOrderId(), "-")] + "-2"
	rr, _ := acct.Replace(ctx, &brokerv1.ReplaceOrderRequest{ClientOrderId: r.GetClientOrderId(), NewClientOrderId: next, LimitPrice: d("1.01")}, decimal.Zero)
	if rr.GetOutcome() != brokerv1.ReplaceOutcome_REPLACE_OUTCOME_ACCEPTED {
		t.Fatal(rr)
	}
	if resp, _ := acct.Cancel(ctx, adapter.OrderRef{ClientOrderID: next}); resp.GetOutcome() != brokerv1.CancelOutcome_CANCEL_OUTCOME_REQUESTED {
		t.Fatal(resp)
	}
	h := baseReq()
	h.Instrument.Symbol = "HALT"
	if resp, _ := acct.Submit(ctx, h, false, decimal.Zero); resp.GetReject().GetCode() != brokerv1.RejectCode_REJECT_CODE_NOT_TRADABLE {
		t.Fatal(resp)
	}
	bad := baseReq()
	bad.ClientOrderId = "manual-app-order"
	_, _ = acct.Submit(ctx, bad, false, decimal.Zero)
	acts := aud.Actions()
	for _, want := range []string{"broker.order.submit", "broker.order.submit.result", "broker.order.replace", "broker.order.cancel", "broker.order.reject"} {
		if !slices.Contains(acts, want) {
			t.Fatalf("missing audit %q in %v", want, acts)
		}
	}
	for _, e := range aud.Events {
		if e.Details["broker_account_id"] != "sim-paper" {
			t.Fatalf("audit without account: %+v", e)
		}
	}
	// SE-021: when the audit log is unavailable the order is not sent.
	aud.Fail = errors.New("audit down")
	resp, _ := acct.Submit(ctx, baseReq(), false, decimal.Zero)
	if resp.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED || !resp.GetReject().GetLocal() {
		t.Fatalf("order sent without audit: %+v", resp)
	}
}

func TestBR021MarginEventDegrades(t *testing.T) {
	s, acct, store, _ := ready(t, sim.Config{})
	s.SetMargin(true, false)
	testkit.WaitState(t, acct, session.Degraded)
	var ue *adapter.UnavailableError
	if _, err := acct.Submit(context.Background(), baseReq(), false, decimal.Zero); !errors.As(err, &ue) {
		t.Fatalf("want UnavailableError during a margin call, got %v", err)
	}
	if len(store.Messages("alert.critical")) == 0 {
		t.Fatal("no critical alert for the margin call")
	}
	if reasons := acct.Session().Status().GetDegradedReasons(); len(reasons) == 0 || !strings.Contains(reasons[0], "margin call") {
		t.Fatalf("reasons %v", reasons)
	}
	recon := len(store.Messages(execstore.ReconSubject("sim-paper")))
	s.SetMargin(false, false)
	testkit.WaitState(t, acct, session.Ready)
	if len(store.Messages(execstore.ReconSubject("sim-paper"))) <= recon {
		t.Fatal("returning to READY after a margin event must request reconciliation")
	}
}

type gate struct{ ok bool }

func (g gate) Allowed(context.Context, string) (bool, string) {
	if g.ok {
		return true, ""
	}
	return false, "live gate closed"
}

func TestBR013LiveAdapterStaysStopped(t *testing.T) {
	for _, g := range []adapter.LiveGate{nil, gate{false}} {
		s := sim.New(sim.Config{AccountID: "sim-live", Environment: brokerv1.Environment_ENVIRONMENT_LIVE})
		acct := adapter.NewAccount(s, execstore.NewMemory(), &testkit.Audit{}, g, testLog(), nil, testkit.FastOptions())
		testkit.Run(t, acct)
		testkit.WaitState(t, acct, session.Stopped)
		time.Sleep(50 * time.Millisecond)
		if acct.Session().State() != session.Stopped || s.Streaming() {
			t.Fatal("live adapter started without both gates")
		}
		if _, err := acct.Submit(context.Background(), baseReq(), false, decimal.Zero); err == nil {
			t.Fatal("stopped live account accepted an order")
		}
	}
	s := sim.New(sim.Config{AccountID: "sim-live", Environment: brokerv1.Environment_ENVIRONMENT_LIVE})
	acct := adapter.NewAccount(s, execstore.NewMemory(), &testkit.Audit{}, gate{true}, testLog(), nil, testkit.FastOptions())
	testkit.Run(t, acct)
	testkit.WaitState(t, acct, session.Ready)
}

func TestBR011ResyncRecoversAfterStoreFailure(t *testing.T) {
	_, acct, store, _ := ready(t, sim.Config{})
	store.FailNext = errors.New("postgres blip")
	resp, _ := acct.Submit(context.Background(), marketReq("1"), false, decimal.Zero)
	id := resp.GetOrder().GetBrokerOrderId() + ":1"
	deadline := time.Now().Add(3 * time.Second)
	for testkit.CountExec(store, "sim-paper", id) == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if n := testkit.CountExec(store, "sim-paper", id); n != 1 {
		t.Fatalf("execution lost or duplicated after a store failure: %d", n)
	}
}
