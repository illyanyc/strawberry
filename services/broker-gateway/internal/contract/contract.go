// Package contract is the shared broker-adapter contract suite (rule BR-001). It drives any
// tb.broker.v1.BrokerGatewayService endpoint purely over gRPC, so the same cases run against the
// simulator, the Alpaca fake, Alpaca paper, and the Python IBKR adapter
// (TB_BROKER_CONTRACT_ADDR). Fault-injection cases run only when the target provides Hooks.
package contract

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
	"net"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/oklog/ulid/v2"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/grpcsvc"
)

// Hooks inject faults into a target (simulator and Alpaca fake only).
type Hooks interface {
	// AmbiguousNextSubmit makes the next submit reach the broker but fail at the caller (timeout).
	AmbiguousNextSubmit()
	// LostNextSubmit makes the next submit time out without reaching the broker.
	LostNextSubmit()
	// DropStream drops the order/trade stream.
	DropStream()
	// Fill makes the market trade through every resting order in symbol.
	Fill(symbol string)
	// InjectManualOrder places and fills an order outside the platform (no tb1- ID).
	InjectManualOrder(symbol string) string
	// ExecutionsPublished counts durable exec.<acct> publications of a canonical exec ID.
	ExecutionsPublished(execID string) int
	// ReconciliationRequests counts recon.request.<acct> publications.
	ReconciliationRequests() int
}

// Target is one endpoint and account under test.
type Target struct {
	Client  brokerv1.BrokerGatewayServiceClient
	Account string
	// Symbol is a liquid, tradable symbol; FarLimit a buy-limit price that will not fill.
	Symbol   string
	FarLimit string
	Hooks    Hooks
	// ReadyTimeout bounds waiting for the session to become READY.
	ReadyTimeout time.Duration
}

// NewClientOrderID returns a fresh attribution ID for strategy short ID "contrt".
func NewClientOrderID() string {
	return fmt.Sprintf("tb1-contrt-%s-1", ulid.MustNew(ulid.Now(), rand.Reader).String())
}

func nextAttempt(id string) string {
	i := strings.LastIndex(id, "-")
	n := 0
	_, _ = fmt.Sscanf(id[i+1:], "%d", &n)
	return fmt.Sprintf("%s-%d", id[:i], n+1)
}

func dec(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: s} }

func (tg Target) limitBuy(coid string) *brokerv1.BrokerOrderRequest {
	return &brokerv1.BrokerOrderRequest{ClientOrderId: coid, BrokerAccountId: tg.Account,
		Instrument: &commonv1.Instrument{Symbol: tg.Symbol}, Side: commonv1.Side_SIDE_BUY, Qty: dec("1"),
		Type: ordersv1.OrderType_ORDER_TYPE_LIMIT, LimitPrice: dec(tg.FarLimit), Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY,
		Mode: commonv1.Mode_MODE_PAPER}
}

func waitFor(t *testing.T, d time.Duration, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func (tg Target) state(t *testing.T) brokerv1.SessionState {
	t.Helper()
	r, err := tg.Client.GetSessionStatus(context.Background(), &brokerv1.GetSessionStatusRequest{BrokerAccountId: tg.Account})
	if err != nil {
		t.Fatalf("GetSessionStatus: %v", err)
	}
	return r.GetStatus().GetState()
}

func (tg Target) waitReady(t *testing.T) {
	t.Helper()
	d := tg.ReadyTimeout
	if d == 0 {
		d = 30 * time.Second
	}
	waitFor(t, d, "session READY", func() bool { return tg.state(t) == brokerv1.SessionState_SESSION_STATE_READY })
}

func (tg Target) order(t *testing.T, coid string) *brokerv1.BrokerOrder {
	t.Helper()
	r, err := tg.Client.GetOrder(context.Background(), &brokerv1.GetOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: coid})
	if err != nil || !r.GetFound() {
		t.Fatalf("GetOrder(%s): found=%v err=%v", coid, r.GetFound(), err)
	}
	return r.GetOrder()
}

func countByCOID(orders []*brokerv1.BrokerOrder, coid string) int {
	n := 0
	for _, o := range orders {
		if o.GetClientOrderId() == coid {
			n++
		}
	}
	return n
}

// Run executes the suite.
func Run(t *testing.T, tg Target) {
	ctx := context.Background()
	t.Run("accounts and capabilities (BR-002)", func(t *testing.T) {
		r, err := tg.Client.ListAccounts(ctx, &brokerv1.ListAccountsRequest{})
		if err != nil {
			t.Fatal(err)
		}
		i := slices.IndexFunc(r.GetAccounts(), func(a *brokerv1.BrokerAccount) bool { return a.GetBrokerAccountId() == tg.Account })
		if i < 0 {
			t.Fatalf("account %s not listed", tg.Account)
		}
		caps := r.GetAccounts()[i].GetCapabilities()
		if caps.GetMaxClientOrderIdLen() < 40 || len(caps.GetOrderTypes()) == 0 || len(caps.GetTifs()) == 0 || caps.GetPdtApplies() {
			t.Fatalf("capabilities incomplete or PDT on (D30): %+v", caps)
		}
	})
	t.Run("session reaches READY (BR-010)", func(t *testing.T) { tg.waitReady(t) })
	t.Run("unknown account is NOT_FOUND", func(t *testing.T) {
		_, err := tg.Client.GetSessionStatus(ctx, &brokerv1.GetSessionStatusRequest{BrokerAccountId: "no-such-account"})
		if status.Code(err) != codes.NotFound {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("malformed attribution rejected locally (BR-003, OE-002)", func(t *testing.T) {
		for _, coid := range []string{"", "my-order-1", "tb1-x-01ARZ3NDEKTSV4RRFFQ69G5FAV-1", NewClientOrderID() + strings.Repeat("9", 200)} {
			r, err := tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(coid)})
			if err != nil {
				t.Fatalf("%q: %v", coid, err)
			}
			if r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED || r.GetReject().GetCode() != brokerv1.RejectCode_REJECT_CODE_INVALID_ATTRIBUTION || !r.GetReject().GetLocal() {
				t.Fatalf("%q: %+v", coid, r)
			}
		}
	})
	t.Run("resting order lifecycle, idempotent resubmit, replace, cancel (BR-007, OE-I1)", func(t *testing.T) {
		coid := NewClientOrderID()
		r, err := tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(coid)})
		if err != nil || r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED {
			t.Fatalf("submit: %+v %v", r, err)
		}
		bid := r.GetOrder().GetBrokerOrderId()
		if bid == "" || r.GetOrder().GetClientOrderId() != coid {
			t.Fatalf("ack missing IDs: %+v", r.GetOrder())
		}
		if got := tg.order(t, coid); got.GetBrokerOrderId() != bid {
			t.Fatalf("lookup by client_order_id returned %s", got.GetBrokerOrderId())
		}
		// Retrying the same submit must not create a second order.
		r2, err := tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(coid)})
		if err != nil {
			t.Fatal(err)
		}
		switch r2.GetOutcome() {
		case brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED:
			if r2.GetOrder().GetBrokerOrderId() != bid {
				t.Fatalf("resubmit created another order %s", r2.GetOrder().GetBrokerOrderId())
			}
		case brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED:
			if r2.GetReject().GetCode() != brokerv1.RejectCode_REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID {
				t.Fatalf("resubmit: %+v", r2)
			}
		default:
			t.Fatalf("resubmit outcome %v", r2.GetOutcome())
		}
		open, err := tg.Client.ListOpenOrders(ctx, &brokerv1.ListOpenOrdersRequest{BrokerAccountId: tg.Account})
		if err != nil || countByCOID(open.GetOrders(), coid) != 1 {
			t.Fatalf("open orders: %d %v", countByCOID(open.GetOrders(), coid), err)
		}
		live := coid
		accounts, _ := tg.Client.ListAccounts(ctx, &brokerv1.ListAccountsRequest{})
		supportsReplace := false
		for _, a := range accounts.GetAccounts() {
			if a.GetBrokerAccountId() == tg.Account {
				supportsReplace = a.GetCapabilities().GetSupportsReplace()
			}
		}
		if supportsReplace {
			bad, err := tg.Client.ReplaceOrder(ctx, &brokerv1.ReplaceOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: coid, NewClientOrderId: NewClientOrderID(), LimitPrice: dec(tg.FarLimit)})
			if err != nil || bad.GetOutcome() != brokerv1.ReplaceOutcome_REPLACE_OUTCOME_REJECTED || bad.GetReject().GetCode() != brokerv1.RejectCode_REJECT_CODE_INVALID_ATTRIBUTION {
				t.Fatalf("replace with a different intent must be rejected locally: %+v %v", bad, err)
			}
			next := nextAttempt(coid)
			rr, err := tg.Client.ReplaceOrder(ctx, &brokerv1.ReplaceOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: coid, NewClientOrderId: next, Qty: dec("2")})
			if err != nil || rr.GetOutcome() != brokerv1.ReplaceOutcome_REPLACE_OUTCOME_ACCEPTED {
				t.Fatalf("replace: %+v %v", rr, err)
			}
			live = next
			waitFor(t, 10*time.Second, "old order REPLACED", func() bool {
				return tg.order(t, coid).GetState() == ordersv1.OrderState_ORDER_STATE_REPLACED
			})
		}
		c, err := tg.Client.CancelOrder(ctx, &brokerv1.CancelOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: live})
		if err != nil || c.GetOutcome() != brokerv1.CancelOutcome_CANCEL_OUTCOME_REQUESTED {
			t.Fatalf("cancel: %+v %v", c, err)
		}
		waitFor(t, 10*time.Second, "order CANCELED", func() bool {
			return tg.order(t, live).GetState() == ordersv1.OrderState_ORDER_STATE_CANCELED
		})
		c2, err := tg.Client.CancelOrder(ctx, &brokerv1.CancelOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: live})
		if err != nil || c2.GetOutcome() != brokerv1.CancelOutcome_CANCEL_OUTCOME_ALREADY_FINAL {
			t.Fatalf("second cancel: %+v %v", c2, err)
		}
		all, err := tg.Client.ListOrders(ctx, &brokerv1.ListOrdersRequest{BrokerAccountId: tg.Account, Since: time.Now().Add(-time.Hour).UnixNano()})
		if err != nil || countByCOID(all.GetOrders(), coid) != 1 {
			t.Fatalf("ListOrders count for %s = %d (%v)", coid, countByCOID(all.GetOrders(), coid), err)
		}
		missing, err := tg.Client.GetOrder(ctx, &brokerv1.GetOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: NewClientOrderID()})
		if err != nil || missing.GetFound() {
			t.Fatalf("lookup of unknown order: %+v %v", missing, err)
		}
	})
	t.Run("account snapshot", func(t *testing.T) {
		r, err := tg.Client.GetAccountSnapshot(ctx, &brokerv1.GetAccountSnapshotRequest{BrokerAccountId: tg.Account})
		if err != nil || r.GetSnapshot().GetEquity().GetValue() == "" || r.GetSnapshot().GetBrokerAccountId() != tg.Account || r.GetSnapshot().GetAsOf() == 0 {
			t.Fatalf("snapshot: %+v %v", r, err)
		}
	})
	t.Run("instrument info", func(t *testing.T) {
		r, err := tg.Client.GetInstrumentInfo(ctx, &brokerv1.GetInstrumentInfoRequest{BrokerAccountId: tg.Account, Symbol: tg.Symbol})
		if err != nil || !r.GetInfo().GetTradable() || r.GetInfo().GetMinTick().GetValue() == "" {
			t.Fatalf("instrument info: %+v %v", r, err)
		}
	})
	if tg.Hooks == nil {
		t.Log("target has no fault hooks; fault-injection cases skipped")
		return
	}
	h := tg.Hooks
	t.Run("submit timeout recovers by lookup, no duplicate (BR-007, OE-003)", func(t *testing.T) {
		tg.waitReady(t)
		coid := NewClientOrderID()
		h.AmbiguousNextSubmit()
		r, err := tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(coid)})
		if err != nil || r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED || !r.GetRecoveredByLookup() {
			t.Fatalf("ambiguous submit: %+v %v", r, err)
		}
		all, _ := tg.Client.ListOrders(ctx, &brokerv1.ListOrdersRequest{BrokerAccountId: tg.Account, Since: time.Now().Add(-time.Hour).UnixNano()})
		if n := countByCOID(all.GetOrders(), coid); n != 1 {
			t.Fatalf("%d orders for %s after a timed-out submit", n, coid)
		}
		_, _ = tg.Client.CancelOrder(ctx, &brokerv1.CancelOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: coid})
	})
	t.Run("lost submit reports UNKNOWN, never resubmits (BR-007)", func(t *testing.T) {
		coid := NewClientOrderID()
		h.LostNextSubmit()
		r, err := tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(coid)})
		if err != nil || r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_UNKNOWN {
			t.Fatalf("lost submit: %+v %v", r, err)
		}
		g, _ := tg.Client.GetOrder(ctx, &brokerv1.GetOrderRequest{BrokerAccountId: tg.Account, ClientOrderId: coid})
		if g.GetFound() {
			t.Fatal("adapter resubmitted after an ambiguous failure")
		}
	})
	t.Run("disconnect: orders refused, missed fills published once, reconciliation requested (BR-004, BR-011)", func(t *testing.T) {
		tg.waitReady(t)
		coid := NewClientOrderID()
		r, err := tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(coid)})
		if err != nil || r.GetOutcome() != brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED {
			t.Fatalf("submit: %+v %v", r, err)
		}
		recon0 := h.ReconciliationRequests()
		h.DropStream()
		waitFor(t, 10*time.Second, "session leaves READY", func() bool { return tg.state(t) != brokerv1.SessionState_SESSION_STATE_READY })
		_, err = tg.Client.SubmitOrder(ctx, &brokerv1.SubmitOrderRequest{Order: tg.limitBuy(NewClientOrderID())})
		if status.Code(err) != codes.FailedPrecondition || !hasReason(err, "BROKER_UNAVAILABLE") {
			// The session may already be back; only assert when it is still down.
			if tg.state(t) != brokerv1.SessionState_SESSION_STATE_READY {
				t.Fatalf("submit while not ready: %v", err)
			}
		}
		h.Fill(tg.Symbol) // happens at the broker while (or just as) the stream is down
		tg.waitReady(t)
		execID := r.GetOrder().GetBrokerOrderId() + ":1"
		waitFor(t, 10*time.Second, "missed fill published", func() bool { return h.ExecutionsPublished(execID) >= 1 })
		h.DropStream()
		tg.waitReady(t)
		time.Sleep(200 * time.Millisecond)
		if n := h.ExecutionsPublished(execID); n != 1 {
			t.Fatalf("execution %s published %d times (want exactly once)", execID, n)
		}
		if h.ReconciliationRequests() <= recon0 {
			t.Fatal("no reconciliation request after reconnect")
		}
	})
	t.Run("manual order is visible without attribution (BR-019, VP-001)", func(t *testing.T) {
		tg.waitReady(t)
		sctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		stream, err := tg.Client.StreamExecutions(sctx, &brokerv1.StreamExecutionsRequest{BrokerAccountId: tg.Account})
		if err != nil {
			t.Fatal(err)
		}
		time.Sleep(100 * time.Millisecond)
		id := h.InjectManualOrder(tg.Symbol)
		for {
			m, err := stream.Recv()
			if err != nil {
				t.Fatalf("manual execution not streamed: %v", err)
			}
			if m.GetExecution().GetBrokerOrderId() == id {
				if strings.HasPrefix(m.GetExecution().GetClientOrderId(), "tb1-") {
					t.Fatalf("manual execution carries a platform ID: %s", m.GetExecution().GetClientOrderId())
				}
				break
			}
		}
		all, _ := tg.Client.ListOrders(ctx, &brokerv1.ListOrdersRequest{BrokerAccountId: tg.Account, Since: time.Now().Add(-time.Hour).UnixNano()})
		if !slices.ContainsFunc(all.GetOrders(), func(o *brokerv1.BrokerOrder) bool { return o.GetBrokerOrderId() == id }) {
			t.Fatal("manual order missing from ListOrders")
		}
	})
}

func hasReason(err error, reason string) bool {
	st, ok := status.FromError(err)
	if !ok {
		return false
	}
	for _, d := range st.Details() {
		if ei, ok := d.(*errdetails.ErrorInfo); ok && ei.GetReason() == reason {
			return true
		}
	}
	return false
}

// HasReason is exported for other tests.
func HasReason(err error, reason string) bool { return hasReason(err, reason) }

// InProcess starts a gRPC server for accounts on an in-memory listener, with the production
// service-token interceptors, and returns a client signed as caller.
func InProcess(t *testing.T, accounts []*adapter.Account, caller string) brokerv1.BrokerGatewayServiceClient {
	t.Helper()
	seed, pub, err := svcauth.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	signer, err := svcauth.NewSigner(caller, seed)
	if err != nil {
		t.Fatal(err)
	}
	v := svcauth.NewVerifier("broker-gateway", map[string]ed25519.PublicKey{caller: pub})
	lis := bufconn.Listen(1 << 20)
	gs := grpc.NewServer(grpc.UnaryInterceptor(v.UnaryServerInterceptor(grpcsvc.AllowedCallers)),
		grpc.StreamInterceptor(v.StreamServerInterceptor(grpcsvc.AllowedCallers)))
	brokerv1.RegisterBrokerGatewayServiceServer(gs, grpcsvc.New(accounts))
	go func() { _ = gs.Serve(lis) }()
	t.Cleanup(gs.Stop)
	conn, err := grpc.NewClient("passthrough:///bufnet",
		grpc.WithContextDialer(func(ctx context.Context, _ string) (net.Conn, error) { return lis.DialContext(ctx) }),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(signer.PerRPC("broker-gateway")))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return brokerv1.NewBrokerGatewayServiceClient(conn)
}
