package simcore

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"
	"time"

	"google.golang.org/protobuf/encoding/protodelim"

	backtestv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/backtest/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
)

const (
	acct  = "sim-paper"
	ibkr  = "ibkr-paper-bot"
	aapl  = "01JAAPL0000000000000000000"
	intnt = "01KBACKTEST000000000000001"
)

var open = time.Date(2026, 10, 13, 13, 30, 0, 0, time.UTC) // 09:30 ET

func d(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: s} }

func setup(t *testing.T, bracket bool, account string) *Core {
	t.Helper()
	reg, _ := json.Marshal(map[string]any{"strategies": []map[string]any{{
		"strategy_id": "orb-15m", "short_id": "orb15aa", "name": "ORB", "horizon": map[bool]string{true: "intraday", false: "swing"}[bracket],
		"broker_account_id": account, "mode": "paper", "allocation": "30000", "segregation_allowlist": []string{aapl}, "version": "1",
	}}})
	c := New(nil)
	hz := commonv1.Horizon_HORIZON_INTRADAY
	if !bracket {
		hz = commonv1.Horizon_HORIZON_SWING
	}
	r := c.Handle(context.Background(), &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Setup{Setup: &backtestv1.SimSetup{
		Start: open.Add(-10 * time.Minute).UnixNano(), RegistryJson: reg,
		Accounts: []*backtestv1.SimAccount{{BrokerAccountId: account, Cash: d("60000"), SupportsBracket: bracket, Segregation: bracket,
			OpeningPositions: []*ledgerv1.Position{{InstrumentId: aapl, Symbol: "AAPL", Qty: d("100"), AvgCost: d("100")}}}},
		Routing:     []*backtestv1.SimRoute{{Horizon: hz, BrokerAccountIds: []string{account}}},
		Instruments: []*backtestv1.SimInstrument{{InstrumentId: aapl, Symbol: "AAPL", Tradable: true}},
	}}})
	if r.GetError() != "" {
		t.Fatal(r.GetError())
	}
	return c
}

func bar(close string, at time.Time) []*backtestv1.SimBar {
	return []*backtestv1.SimBar{{InstrumentId: aapl, Close: d(close), TsClose: at.UnixNano(), IntervalNs: int64(15 * time.Minute)}}
}

func entry(account string, hz commonv1.Horizon, tif ordersv1.TimeInForce) *ordersv1.OrderIntent {
	return &ordersv1.OrderIntent{IntentId: intnt, StrategyId: "orb-15m", BrokerAccountId: account, InstrumentId: aapl, Side: commonv1.Side_SIDE_BUY,
		Sizing: &ordersv1.OrderIntent_Quantity{Quantity: d("100")}, OrderType: ordersv1.OrderType_ORDER_TYPE_MARKET, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY, Mode: commonv1.Mode_MODE_BACKTEST,
		CorrelationId: intnt, Horizon: hz, Kind: ordersv1.IntentKind_INTENT_KIND_OPEN, Source: ordersv1.IntentSource_INTENT_SOURCE_STRATEGY,
		ReferencePrice: d("101.3"), Protective: &ordersv1.ProtectiveSpec{StopLossPrice: d("98.95"), TakeProfitPrice: d("106"), Tif: tif}}
}

func do(t *testing.T, c *Core, req *backtestv1.SimRequest) *backtestv1.SimResponse {
	t.Helper()
	r := c.Handle(context.Background(), req)
	if r.GetError() != "" {
		t.Fatal(r.GetError())
	}
	return r
}

func position(r *backtestv1.SimResponse, owner string) string {
	for _, u := range r.GetLedgerUpdates() {
		for _, p := range u.GetPositions() {
			if p.GetOwner() == owner {
				return p.GetQty().GetValue() + "/" + p.GetRealizedPnl().GetValue()
			}
		}
	}
	return ""
}

func TestBT003LiveGateApprovesAndBuildsTheBracket(t *testing.T) {
	c := setup(t, true, acct)
	now := open.Add(16 * time.Minute)
	r := do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Submit{Submit: &backtestv1.SimSubmit{Now: now.UnixNano(),
		Intent: entry(acct, commonv1.Horizon_HORIZON_INTRADAY, ordersv1.TimeInForce_TIME_IN_FORCE_DAY), Bars: bar("101.3", open.Add(15*time.Minute))}}})
	if !r.GetDecision().GetApproved() {
		t.Fatalf("rejected: %s %v", r.GetDecision().GetFirstFailure(), r.GetDecision().GetChecks())
	}
	if len(r.GetDecision().GetChecks()) < 30 {
		t.Fatalf("expected every gate check plus capital, got %d", len(r.GetDecision().GetChecks()))
	}
	if len(r.GetOrders()) != 1 {
		t.Fatalf("want the entry with its legs, got %d orders", len(r.GetOrders()))
	}
	o := r.GetOrders()[0]
	// OE-013: marketable limit at last + 10 bps, rounded up to the cent.
	if o.GetType() != ordersv1.OrderType_ORDER_TYPE_LIMIT || o.GetLimitPrice().GetValue() != "101.41" || len(o.GetLegs()) != 2 {
		t.Fatalf("entry %v", o)
	}
	id, err := attribution.Parse(o.GetClientOrderId(), 0)
	if err != nil || id.StrategyShortID != "orb15aa" || id.IntentID != intnt {
		t.Fatalf("attribution %q: %v", o.GetClientOrderId(), err)
	}
	for _, l := range o.GetLegs() {
		if l.GetBrokerStatus() != "held" {
			t.Fatalf("legs are held until the parent fills: %v", l)
		}
	}

	// Entry fills: the strategy owns 100, manual keeps 100, the legs go live.
	r = do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Fill{Fill: &backtestv1.SimFill{Now: now.Add(time.Second).UnixNano(),
		BrokerAccountId: acct, BrokerOrderId: o.GetBrokerOrderId(), Qty: d("100"), Price: d("101.35"), Fees: d("0.01")}}})
	if got := position(r, "orb-15m"); got != "100/0" {
		t.Fatalf("orb position %q", got)
	}
	var stop, tp *brokerv1.BrokerOrder
	for _, x := range r.GetOrders() {
		switch {
		case x.GetParentBrokerOrderId() != "" && x.GetType() == ordersv1.OrderType_ORDER_TYPE_STOP:
			stop = x
		case x.GetParentBrokerOrderId() != "" && x.GetType() == ordersv1.OrderType_ORDER_TYPE_LIMIT:
			tp = x
		}
	}
	if stop == nil || tp == nil || stop.GetBrokerStatus() != "new" {
		t.Fatalf("legs not released: %v", r.GetOrders())
	}

	// The stop fills; the broker cancels the take-profit (OCO); FIFO P&L includes both fees.
	r = do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Fill{Fill: &backtestv1.SimFill{Now: now.Add(30 * time.Minute).UnixNano(),
		BrokerAccountId: acct, BrokerOrderId: stop.GetBrokerOrderId(), Qty: d("100"), Price: d("98.95"), Fees: d("0.03")}}})
	if got := position(r, "orb-15m"); got != "0/-240.04" {
		t.Fatalf("orb after stop %q", got)
	}
	canceled := false
	for _, x := range r.GetOrders() {
		if x.GetBrokerOrderId() == tp.GetBrokerOrderId() && x.GetState() == ordersv1.OrderState_ORDER_STATE_CANCELED {
			canceled = true
		}
	}
	if !canceled {
		t.Fatal("take-profit not canceled by the OCO")
	}
	q := do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Query{Query: &backtestv1.SimQuery{Owner: "manual", BrokerAccountId: acct}}})
	if len(q.GetPortfolio().GetPositions()) != 1 || q.GetPortfolio().GetPositions()[0].GetQty().GetValue() != "100" {
		t.Fatalf("manual book changed: %v", q.GetPortfolio())
	}
}

func TestBT003AllocationIsNotDayPnL(t *testing.T) {
	c := setup(t, true, acct)
	q := do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Query{Query: &backtestv1.SimQuery{Owner: "orb-15m", BrokerAccountId: acct}}})
	if q.GetPortfolio().GetDayPnl().GetValue() != "0" || q.GetPortfolio().GetEquity().GetValue() != "30000" || q.GetPortfolio().GetHighWaterMark().GetValue() != "30000" {
		t.Fatalf("RM-005: the initial allocation must not count as day P&L: %v", q.GetPortfolio())
	}
}

func TestBT007SellBeyondOwnQuantityRejectedManualUntouched(t *testing.T) {
	c := setup(t, true, acct)
	now := open.Add(16 * time.Minute)
	it := entry(acct, commonv1.Horizon_HORIZON_INTRADAY, ordersv1.TimeInForce_TIME_IN_FORCE_DAY)
	it.Side, it.Kind, it.Protective, it.Sizing = commonv1.Side_SIDE_SELL, ordersv1.IntentKind_INTENT_KIND_CLOSE, nil, &ordersv1.OrderIntent_Quantity{Quantity: d("50")}
	r := do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Submit{Submit: &backtestv1.SimSubmit{Now: now.UnixNano(), Intent: it,
		Bars: bar("101.3", open.Add(15*time.Minute))}}})
	if r.GetDecision().GetApproved() || r.GetDecision().GetFirstFailure() != "ownership" {
		t.Fatalf("a strategy sell of manual shares must fail ownership (VP-003): %v", r.GetDecision())
	}
	if len(r.GetOrders()) != 0 {
		t.Fatal("no order may reach the broker")
	}
}

func TestBT003PartialGlobalLimitsMergeOverDefaults(t *testing.T) {
	reg, _ := json.Marshal(map[string]any{"strategies": []map[string]any{{"strategy_id": "orb-15m", "short_id": "orb15aa", "horizon": "intraday",
		"broker_account_id": acct, "mode": "paper", "allocation": "30000"}}})
	c := New(nil)
	r := c.Handle(context.Background(), &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Setup{Setup: &backtestv1.SimSetup{
		Start: open.Add(-10 * time.Minute).UnixNano(), RegistryJson: reg, GlobalLimits: &ordersv1.RiskLimits{MaxPositions: 50},
		Accounts:    []*backtestv1.SimAccount{{BrokerAccountId: acct, Cash: d("60000"), SupportsBracket: true}},
		Routing:     []*backtestv1.SimRoute{{Horizon: commonv1.Horizon_HORIZON_INTRADAY, BrokerAccountIds: []string{acct}}},
		Instruments: []*backtestv1.SimInstrument{{InstrumentId: aapl, Symbol: "AAPL", Tradable: true}},
	}}})
	if r.GetError() != "" {
		t.Fatal(r.GetError())
	}
	now := open.Add(16 * time.Minute)
	r = do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Submit{Submit: &backtestv1.SimSubmit{Now: now.UnixNano(),
		Intent: entry(acct, commonv1.Horizon_HORIZON_INTRADAY, ordersv1.TimeInForce_TIME_IN_FORCE_DAY), Bars: bar("101.3", open.Add(15*time.Minute))}}})
	if !r.GetDecision().GetApproved() {
		t.Fatalf("an override of one limit must keep the other defaults (price collar): %s", r.GetDecision().GetFirstFailure())
	}
}

func TestBT004StaleBarFailsClosed(t *testing.T) {
	c := setup(t, true, acct)
	now := open.Add(3 * time.Hour)
	r := do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Submit{Submit: &backtestv1.SimSubmit{Now: now.UnixNano(),
		Intent: entry(acct, commonv1.Horizon_HORIZON_INTRADAY, ordersv1.TimeInForce_TIME_IN_FORCE_DAY), Bars: bar("101.3", open.Add(15*time.Minute))}}})
	if r.GetDecision().GetApproved() || r.GetDecision().GetFirstFailure() != "data_fresh" {
		t.Fatalf("stale data must fail closed (RM-004): %v", r.GetDecision().GetFirstFailure())
	}
}

func TestBT003IBKRStyleStopAfterEntryFill(t *testing.T) {
	c := setup(t, false, ibkr)
	now := open.Add(16 * time.Minute)
	r := do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Submit{Submit: &backtestv1.SimSubmit{Now: now.UnixNano(),
		Intent: entry(ibkr, commonv1.Horizon_HORIZON_SWING, ordersv1.TimeInForce_TIME_IN_FORCE_GTC), Bars: bar("101.3", open.Add(15*time.Minute))}}})
	if !r.GetDecision().GetApproved() || len(r.GetOrders()) != 1 || len(r.GetOrders()[0].GetLegs()) != 0 {
		t.Fatalf("entry without bracket expected: %v %v", r.GetDecision().GetFirstFailure(), r.GetOrders())
	}
	r = do(t, c, &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Fill{Fill: &backtestv1.SimFill{Now: now.Add(time.Second).UnixNano(),
		BrokerAccountId: ibkr, BrokerOrderId: r.GetOrders()[0].GetBrokerOrderId(), Qty: d("100"), Price: d("101.35")}}})
	var stop *brokerv1.BrokerOrder
	for _, x := range r.GetOrders() {
		if x.GetType() == ordersv1.OrderType_ORDER_TYPE_STOP {
			stop = x
		}
	}
	if stop == nil || stop.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_GTC || stop.GetQty().GetValue() != "100" {
		t.Fatalf("OE-014: a GTC stop must follow the entry fill: %v", r.GetOrders())
	}
	if id, _ := attribution.Parse(stop.GetClientOrderId(), 0); id.Attempt != 50 {
		t.Fatalf("protective stop attempt %d", id.Attempt)
	}
}

func TestBT003ClockNeverMovesBackwards(t *testing.T) {
	c := setup(t, true, acct)
	r := c.Handle(context.Background(), &backtestv1.SimRequest{Body: &backtestv1.SimRequest_Advance{Advance: &backtestv1.SimAdvance{Now: open.Add(-time.Hour).UnixNano()}}})
	if r.GetError() == "" {
		t.Fatal("a backwards clock must fail")
	}
}

func TestBT003StdioTransport(t *testing.T) {
	var in, out bytes.Buffer
	for _, req := range []*backtestv1.SimRequest{
		{Body: &backtestv1.SimRequest_Query{Query: &backtestv1.SimQuery{Owner: "x"}}},
		{Body: &backtestv1.SimRequest_Advance{Advance: &backtestv1.SimAdvance{Now: 1}}},
	} {
		if _, err := protodelim.MarshalTo(&in, req); err != nil {
			t.Fatal(err)
		}
	}
	if err := Serve(context.Background(), New(nil), &in, &out); err != nil {
		t.Fatal(err)
	}
	n := 0
	for out.Len() > 0 {
		resp := &backtestv1.SimResponse{}
		if err := protodelim.UnmarshalFrom(&out, resp); err != nil {
			t.Fatal(err)
		}
		if resp.GetError() == "" {
			t.Fatal("requests before setup must fail closed")
		}
		n++
	}
	if n != 2 {
		t.Fatalf("one response per request, got %d", n)
	}
}
