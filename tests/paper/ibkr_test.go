package paper

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/shopspring/decimal"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	execapp "github.com/illyanyc/strawberry/services/execution/app"
)

// bridgeIBKR relays the IBKR adapter's snapshots, order updates, and executions onto the bus with
// the subjects and msg IDs its Postgres outbox relay uses in production (ADR-0023), so ledger and
// execution see the account exactly as they would behind broker-gateway-ibkr.
func bridgeIBKR(ctx context.Context, h *Harness, c brokerv1.BrokerGatewayServiceClient, opt grpc.CallOption, acct string) {
	go func() {
		for ctx.Err() == nil {
			if r, err := c.GetAccountSnapshot(ctx, &brokerv1.GetAccountSnapshotRequest{BrokerAccountId: acct}, opt); err == nil {
				s := r.GetSnapshot()
				s.AsOf = h.Clock.Now().UnixNano()
				b, _ := proto.Marshal(s)
				h.publish("acct.snapshot."+acct, fmt.Sprintf("acct:%s:%d", acct, s.GetAsOf()), b)
			}
			time.Sleep(200 * time.Millisecond)
		}
	}()
	go func() {
		st, err := c.StreamOrderUpdates(ctx, &brokerv1.StreamOrderUpdatesRequest{BrokerAccountId: acct}, opt)
		if err != nil {
			return
		}
		for {
			u, err := st.Recv()
			if err != nil {
				return
			}
			b, _ := proto.Marshal(u.GetUpdate())
			o := u.GetUpdate().GetOrder()
			h.publish("ord.update."+acct, fmt.Sprintf("ord:%s:%s|%s|%s", acct, o.GetBrokerOrderId(), o.GetBrokerStatus(), o.GetFilledQty().GetValue()), b)
		}
	}()
	go func() {
		st, err := c.StreamExecutions(ctx, &brokerv1.StreamExecutionsRequest{BrokerAccountId: acct}, opt)
		if err != nil {
			return
		}
		for {
			x, err := st.Recv()
			if err != nil {
				return
			}
			b, _ := proto.Marshal(x.GetExecution())
			h.publish("exec."+acct, "exec:"+acct+":"+x.GetExecution().GetBrokerExecId(), b)
		}
	}()
}

// TestGate2SwingRoutesThroughIBKRAdapter (D30, RM-011, OE-014): a swing intent is routed to the
// account served by the real Python IBKR adapter (broker-gateway-ibkr over its fake gateway, run by
// `make paper-ibkr`), passes the gate, is sent with an orderRef within the adapter's 44-character
// limit and no bracket (the adapter has none), fills, and gets a separate GTC protective stop that
// rests at IBKR; the ledger books the position to the swing strategy and reconciles.
func TestGate2SwingRoutesThroughIBKRAdapter(t *testing.T) {
	addr, seedFile := os.Getenv("TB_IBKR_ADAPTER_ADDR"), os.Getenv("TB_IBKR_SEED_FILE")
	if addr == "" || seedFile == "" {
		t.Skip("TB_IBKR_ADAPTER_ADDR / TB_IBKR_SEED_FILE not set: run `make paper-ibkr`")
	}
	raw, err := os.ReadFile(seedFile) //nolint:gosec // test-provided path
	if err != nil {
		t.Fatal(err)
	}
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	signer, err := svcauth.NewSigner("execution", seed)
	if err != nil {
		t.Fatal(err)
	}
	const acct = "ibkr-paper-fake"
	client := brokerv1.NewBrokerGatewayServiceClient(dial(t, addr))
	opt := signer.PerRPC("broker-gateway-ibkr")
	h := New(t, Options{ExtraBrokers: []execapp.BrokerEndpoint{{Audience: "broker-gateway-ibkr", Client: client, Opts: []grpc.CallOption{opt}}}, ExtraSwing: []string{acct},
		Policies: map[string]execapp.AccountPolicy{acct: {MarginMultiple: decimal.NewFromInt(2), MarginUtilCap: decimal.RequireFromString("0.5"), PDTGuard: "off", PDTMax: 3}}})
	bridgeIBKR(h.Ctx, h, client, opt, acct)
	h.Fresh(map[string]string{"SPY": "100"})
	h.WaitFor("IBKR account accepting intents", 20*time.Second, func() bool {
		for _, a := range h.Exec.Engine.Status().GetAccounts() {
			if a.GetBrokerAccountId() == acct {
				return a.GetAcceptingIntents()
			}
		}
		return false
	})
	h.Strategy("swingib", "swingib", commonv1.Horizon_HORIZON_SWING, acct, "40000", nil, false)
	h.Strategy("orbib", "orbibaa", commonv1.Horizon_HORIZON_INTRADAY, acct, "1000", nil, false)
	mustReject(t, h.submit(h.intent("orbib", acct, "SPY", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "95", "")), "routing")
	r := h.submit(h.intent("swingib", acct, "SPY", commonv1.Side_SIDE_BUY, "10", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_SWING, "95", ""))
	mustApprove(t, r)
	entry := r.GetOrders()[0]
	if len(entry.GetClientOrderId()) > 44 || entry.GetState() == ordersv1.OrderState_ORDER_STATE_REJECTED || entry.GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN {
		t.Fatalf("entry at the IBKR adapter: %v", entry)
	}
	h.WaitFor("GTC protective stop resting at the IBKR adapter", 20*time.Second, func() bool {
		open, err := client.ListOpenOrders(h.Ctx, &brokerv1.ListOpenOrdersRequest{BrokerAccountId: acct}, opt)
		if err != nil {
			return false
		}
		for _, o := range open.GetOrders() {
			if o.GetType() == ordersv1.OrderType_ORDER_TYPE_STOP && o.GetTif() == ordersv1.TimeInForce_TIME_IN_FORCE_GTC &&
				strings.HasSuffix(o.GetClientOrderId(), "-50") && strings.HasPrefix(o.GetClientOrderId(), "tb1-swingib-") {
				return true
			}
		}
		return false
	})
	h.Quiesce()
	if got := h.Owners(acct, "SPY"); got["swingib"] != "10" || got["broker"] != "10" {
		t.Fatalf("ledger at the IBKR account: %v", got)
	}
	var roles []string
	for _, o := range h.Exec.Engine.Orders(acct, "swingib", false, 0, 0) {
		roles = append(roles, o.GetRole().String()+":"+o.GetState().String())
	}
	if !slices.Contains(roles, "ORDER_ROLE_PROTECTIVE_STOP:ORDER_STATE_ACCEPTED") && !slices.Contains(roles, "ORDER_ROLE_PROTECTIVE_STOP:ORDER_STATE_SUBMITTED") {
		t.Fatalf("OMS roles at the IBKR account: %v", roles)
	}
	t.Logf("IBKR adapter orders: %v; ledger %v", roles, h.Owners(acct, "SPY"))
}
