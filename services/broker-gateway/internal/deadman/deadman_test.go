package deadman_test

import (
	"context"
	"log/slog"
	"sync/atomic"
	"testing"
	"time"

	"github.com/shopspring/decimal"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/deadman"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/testkit"
)

// RM-017: when the execution heartbeat stops during the session, working platform orders are
// canceled; protective stops and manual orders stay.
func TestRM017DeadmanCancelsWorkingOrdersKeepsProtection(t *testing.T) {
	s, acct, _, _ := testkit.SimAccount(t, sim.Config{AccountID: "sim-paper"})
	testkit.Run(t, acct)
	testkit.WaitState(t, acct, brokerv1.SessionState_SESSION_STATE_READY)
	ctx := context.Background()
	inst := &commonv1.Instrument{InstrumentId: "01J0000000000000000000000X", Symbol: "X"}
	submit := func(coid string, typ ordersv1.OrderType, side commonv1.Side, px string) {
		t.Helper()
		req := &brokerv1.BrokerOrderRequest{ClientOrderId: coid, BrokerAccountId: "sim-paper", Instrument: inst, Side: side, Qty: adapter.D(decimal.NewFromInt(10)),
			Type: typ, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_GTC}
		if typ == ordersv1.OrderType_ORDER_TYPE_STOP {
			req.StopPrice = &commonv1.Decimal{Value: px}
		} else {
			req.LimitPrice = &commonv1.Decimal{Value: px}
		}
		r, err := acct.Submit(ctx, req, false, decimal.RequireFromString("0.01"))
		if err != nil || r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED {
			t.Fatalf("submit %s: %v %v", coid, r, err)
		}
	}
	submit("tb1-orbaaaa-01J00000000000000000000001-1", ordersv1.OrderType_ORDER_TYPE_LIMIT, commonv1.Side_SIDE_BUY, "90")
	submit("tb1-orbaaaa-01J00000000000000000000002-50", ordersv1.OrderType_ORDER_TYPE_STOP, commonv1.Side_SIDE_SELL, "80")
	submit("tb1-manual-01J00000000000000000000003-1", ordersv1.OrderType_ORDER_TYPE_LIMIT, commonv1.Side_SIDE_BUY, "91")
	var now atomic.Int64
	now.Store(time.Date(2026, 10, 14, 15, 0, 0, 0, time.UTC).UnixNano())
	alerts := 0
	w := &deadman.Watch{Accounts: []deadman.Account{acct}, Timeout: 30 * time.Second, Calendar: calendar.XNYS(),
		Now: func() time.Time { return time.Unix(0, now.Load()) }, Log: slog.New(slog.DiscardHandler),
		Alert: func(context.Context, *alertsv1.Alert) error { alerts++; return nil }}
	if n := w.Check(ctx); n != 0 {
		t.Fatal("must not fire before the first heartbeat")
	}
	w.Beat()
	now.Add(int64(20 * time.Second))
	if n := w.Check(ctx); n != 0 {
		t.Fatal("fired inside the timeout")
	}
	now.Add(int64(20 * time.Second))
	if n := w.Check(ctx); n != 1 || alerts != 1 {
		t.Fatalf("canceled %d orders, %d alerts; want 1 and 1", n, alerts)
	}
	if n := w.Check(ctx); n != 0 {
		t.Fatal("must fire once per loss")
	}
	open, _ := s.ListOpenOrders(ctx)
	if len(open) != 2 {
		t.Fatalf("open orders after dead-man = %d, want the stop and the manual order", len(open))
	}
}
