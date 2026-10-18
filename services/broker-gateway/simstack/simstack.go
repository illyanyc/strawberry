// Package simstack runs broker-simulator accounts behind the real adapter engine and
// BrokerGatewayService, publishing their events through a callback with the same subjects and msg
// IDs as the Postgres outbox. It exists for the Phase 2 paper harness (tests/paper) and other
// cross-service tests; production uses cmd/broker-gateway.
package simstack

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/deadman"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/execstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/grpcsvc"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
)

// AccountConfig describes one simulated account.
type AccountConfig struct {
	ID   string
	Cash decimal.Decimal
	// IBKRLike makes the account look like the IBKR adapter's (ADR-0023): broker IBKR, no
	// bracket/OCO/OTO support, 44-character attribution limit, no fractional shares.
	IBKRLike bool
}

// Config for the stack.
type Config struct {
	Accounts []AccountConfig
	// Resolve maps symbols to instrument IDs for snapshots and manual orders.
	Resolve func(symbol string) string
	// Publish receives every bus message (subject, msg ID, protobuf payload) in order.
	Publish          func(subject, msgID string, payload []byte)
	Now              func() time.Time
	SnapshotInterval time.Duration
	Log              *slog.Logger
	// DeadmanTimeout enables the execution-heartbeat dead-man (0 = off).
	DeadmanTimeout time.Duration
}

// Stack is a running set of simulated accounts.
type Stack struct {
	Server  brokerv1.BrokerGatewayServiceServer
	sims    map[string]*sim.Sim
	accts   map[string]*adapter.Account
	Store   *execstore.Memory
	auditMu sync.Mutex
	audits  []audit.Event
	Deadman *deadman.Watch
	cancel  context.CancelFunc
}

type auditor struct{ s *Stack }

func (a auditor) Append(_ context.Context, e audit.Event) error {
	a.s.auditMu.Lock()
	a.s.audits = append(a.s.audits, e)
	a.s.auditMu.Unlock()
	return nil
}

// AuditActions returns the adapters' audit actions (BR-014).
func (s *Stack) AuditActions() []string {
	s.auditMu.Lock()
	defer s.auditMu.Unlock()
	out := make([]string, 0, len(s.audits))
	for _, e := range s.audits {
		out = append(out, e.Action)
	}
	return out
}

// IBKRCapabilities mirrors the IBKR adapter's declared capabilities (no broker-side brackets).
func IBKRCapabilities() *brokerv1.Capabilities {
	return &brokerv1.Capabilities{
		OrderTypes: []ordersv1.OrderType{ordersv1.OrderType_ORDER_TYPE_MARKET, ordersv1.OrderType_ORDER_TYPE_LIMIT, ordersv1.OrderType_ORDER_TYPE_STOP,
			ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT},
		Tifs:          []ordersv1.TimeInForce{ordersv1.TimeInForce_TIME_IN_FORCE_DAY, ordersv1.TimeInForce_TIME_IN_FORCE_GTC},
		SupportsShort: true, SupportsExtendedHours: true, MaxClientOrderIdLen: 44, IsMargin: true, SupportsReplace: true, DataFeeds: []string{"ibkr"},
	}
}

// Start builds and runs the accounts until Close.
func Start(ctx context.Context, cfg Config) *Stack {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Log == nil {
		cfg.Log = slog.New(slog.DiscardHandler)
	}
	if cfg.SnapshotInterval == 0 {
		cfg.SnapshotInterval = 200 * time.Millisecond
	}
	ctx, cancel := context.WithCancel(ctx)
	st := &Stack{sims: map[string]*sim.Sim{}, accts: map[string]*adapter.Account{}, Store: execstore.NewMemory(), cancel: cancel}
	st.Store.OnPublish = func(m execstore.Message) {
		if cfg.Publish != nil {
			b, _ := proto.Marshal(m.Msg)
			cfg.Publish(m.Subject, m.MsgID, b)
		}
	}
	opts := adapter.DefaultOptions()
	opts.SnapshotInterval = cfg.SnapshotInterval
	opts.Backoff = resilience.Backoff{Base: 20 * time.Millisecond, Cap: 200 * time.Millisecond}
	opts.LookupAttempts = 3
	opts.LookupBackoff = resilience.Backoff{Base: 10 * time.Millisecond, Cap: 50 * time.Millisecond}
	opts.Now = cfg.Now
	var list []*adapter.Account
	for _, a := range cfg.Accounts {
		sc := sim.Config{AccountID: a.ID, StartingCash: a.Cash, Now: cfg.Now, Resolve: cfg.Resolve}
		if a.IBKRLike {
			sc.Broker, sc.Capabilities = commonv1.Broker_BROKER_IBKR, IBKRCapabilities()
		}
		s := sim.New(sc)
		acct := adapter.NewAccount(s, st.Store, auditor{st}, nil, cfg.Log, nil, opts)
		st.sims[a.ID], st.accts[a.ID] = s, acct
		list = append(list, acct)
		go acct.Run(ctx)
	}
	st.Server = grpcsvc.New(list)
	if cfg.DeadmanTimeout > 0 {
		var das []deadman.Account
		for _, a := range list {
			das = append(das, a)
		}
		st.Deadman = &deadman.Watch{Accounts: das, Timeout: cfg.DeadmanTimeout, Calendar: calendar.XNYS(), Now: cfg.Now, Log: cfg.Log,
			Alert: func(ctx context.Context, a *alertsv1.Alert) error { return st.Store.Alert(ctx, a) }}
	}
	return st
}

// Close stops every account.
func (s *Stack) Close() { s.cancel() }

// Ready reports whether every account's session is READY.
func (s *Stack) Ready() bool {
	for _, a := range s.accts {
		if a.Session().State() != brokerv1.SessionState_SESSION_STATE_READY {
			return false
		}
	}
	return true
}

// SetPrice moves the market for symbol in every simulated account (resting orders match).
func (s *Stack) SetPrice(symbol string, px decimal.Decimal) {
	for _, x := range s.sims {
		x.SetPrice(symbol, px)
	}
}

// InjectManualOrder places and fills an order "in the broker's own app" (owner manual, VP-001).
func (s *Stack) InjectManualOrder(acct, symbol string, side commonv1.Side, qty decimal.Decimal) string {
	return s.sims[acct].InjectManualOrder(symbol, side, qty)
}

// Disconnect drops the account's broker stream (the adapter reconnects and resyncs, OE-008).
func (s *Stack) Disconnect(acct string) { s.sims[acct].Disconnect() }

// SetConnectError makes reconnects fail (nil restores them).
func (s *Stack) SetConnectError(acct string, err error) { s.sims[acct].SetConnectError(err) }

// SetMargin sets broker-reported margin flags (IMD, margin call; RM-013).
func (s *Stack) SetMargin(acct string, marginCall, imd bool) { s.sims[acct].SetMargin(marginCall, imd) }

// FailNextSubmitAfterAccept makes the next submit reach the broker but time out (OE-003).
func (s *Stack) FailNextSubmitAfterAccept(acct string, n int) {
	s.sims[acct].FailNextSubmitAfterAccept(n)
}

// FailNextLookups makes the next n order lookups fail (with FailNextSubmitAfterAccept this yields
// SUBMIT_OUTCOME_UNKNOWN, OE-005).
func (s *Stack) FailNextLookups(acct string, n int) { s.sims[acct].FailNextLookups(n) }

// OpenOrders lists the simulator's open orders (test assertions).
func (s *Stack) OpenOrders(ctx context.Context, acct string) ([]*brokerv1.BrokerOrder, error) {
	return s.sims[acct].ListOpenOrders(ctx)
}

// Position returns the broker's net position in symbol.
func (s *Stack) Position(ctx context.Context, acct, symbol string) decimal.Decimal {
	snap, err := s.sims[acct].Snapshot(ctx)
	if err != nil {
		return decimal.Zero
	}
	for _, p := range snap.GetPositions() {
		if p.GetSymbol() == symbol {
			v, _ := decimal.NewFromString(p.GetQty().GetValue())
			return v
		}
	}
	return decimal.Zero
}

// Alerts returns alert subjects published by the adapters (dead-man, margin events).
func (s *Stack) Alerts() []*alertsv1.Alert {
	var out []*alertsv1.Alert
	for _, m := range s.Store.Messages("") {
		if a, ok := m.Msg.(*alertsv1.Alert); ok {
			out = append(out, a)
		}
	}
	return out
}
