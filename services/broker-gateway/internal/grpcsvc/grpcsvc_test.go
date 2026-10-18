package grpcsvc_test

import (
	"context"
	"errors"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/contract"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/session"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/testkit"
)

func order(acct string) *brokerv1.SubmitOrderRequest {
	return &brokerv1.SubmitOrderRequest{Order: &brokerv1.BrokerOrderRequest{ClientOrderId: contract.NewClientOrderID(), BrokerAccountId: acct,
		Instrument: &commonv1.Instrument{Symbol: "AAPL"}, Side: commonv1.Side_SIDE_BUY, Qty: &commonv1.Decimal{Value: "1"},
		Type: ordersv1.OrderType_ORDER_TYPE_MARKET, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY}}
}

func TestBR004NotReadyIsFailedPrecondition(t *testing.T) {
	s, acct, _, _ := testkit.SimAccount(t, sim.Config{})
	s.SetConnectError(errors.New("broker down"))
	testkit.Run(t, acct)
	client := contract.InProcess(t, []*adapter.Account{acct}, "execution")
	_, err := client.SubmitOrder(context.Background(), order("sim-paper"))
	if status.Code(err) != codes.FailedPrecondition || !contract.HasReason(err, "BROKER_UNAVAILABLE") {
		t.Fatalf("want FAILED_PRECONDITION/BROKER_UNAVAILABLE, got %v", err)
	}
	if _, err := client.CancelOrder(context.Background(), &brokerv1.CancelOrderRequest{BrokerAccountId: "sim-paper", ClientOrderId: contract.NewClientOrderID()}); status.Code(err) != codes.FailedPrecondition {
		t.Fatalf("cancel while disconnected: %v", err)
	}
	if _, err := client.CancelOrder(context.Background(), &brokerv1.CancelOrderRequest{BrokerAccountId: "sim-paper"}); status.Code(err) != codes.InvalidArgument {
		t.Fatalf("cancel without an ID: %v", err)
	}
	s.SetConnectError(nil)
	testkit.WaitState(t, acct, session.Ready)
	r, err := client.SubmitOrder(context.Background(), order("sim-paper"))
	if err != nil || r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED {
		t.Fatalf("after reconnect: %+v %v", r, err)
	}
}

func TestSE040BrokerMethodAllowlist(t *testing.T) {
	_, acct, _, _ := testkit.SimAccount(t, sim.Config{})
	testkit.Run(t, acct)
	testkit.WaitState(t, acct, session.Ready)
	for _, caller := range []string{"ledger", "marketdata", "api-gateway"} {
		client := contract.InProcess(t, []*adapter.Account{acct}, caller)
		if _, err := client.SubmitOrder(context.Background(), order("sim-paper")); status.Code(err) != codes.PermissionDenied {
			t.Fatalf("%s submitted an order: %v", caller, err)
		}
	}
	ledger := contract.InProcess(t, []*adapter.Account{acct}, "ledger")
	if _, err := ledger.ListExecutions(context.Background(), &brokerv1.ListExecutionsRequest{BrokerAccountId: "sim-paper"}); err != nil {
		t.Fatalf("ledger must read executions: %v", err)
	}
}
