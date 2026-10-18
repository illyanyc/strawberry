// Package paper is the Phase 2 paper-trading harness: broker simulators behind the real adapter
// engine (an Alpaca-shaped account through a broker-gateway endpoint and an IBKR-shaped account
// through a separate broker-gateway-ibkr endpoint), the ledger, and execution, all in-process over
// embedded NATS JetStream and real gRPC, on a shared virtual market clock. The gate tests in this
// module drive it (section 06/07 acceptance, Gate 2).
package paper

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"os"
	"sort"
	"sync"
	"testing"
	"time"

	natsserver "github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/shopspring/decimal"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/broker-gateway/simstack"
	execapp "github.com/illyanyc/strawberry/services/execution/app"
	ledgerapp "github.com/illyanyc/strawberry/services/ledger/app"
)

// Account IDs used by the harness (D30 routing: intraday → Alpaca-shaped, swing → IBKR-shaped).
const (
	Alpaca = "sim-paper"
	IBKR   = "ibkr-paper-bot"
)

// Clock is the shared virtual market clock: it runs at wall speed from the last Set, so snapshots
// and events keep distinct timestamps, and the harness jumps it between bars.
type Clock struct {
	mu    sync.Mutex
	base  time.Time
	setAt time.Time
}

// Now returns the virtual time.
func (c *Clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.base.Add(time.Since(c.setAt)).UTC()
}

// Set moves the clock.
func (c *Clock) Set(t time.Time) {
	c.mu.Lock()
	c.base, c.setAt = t, time.Now()
	c.mu.Unlock()
}

// Add advances the clock.
func (c *Clock) Add(d time.Duration) { c.Set(c.Now().Add(d)) }

// Options tune the harness.
type Options struct {
	Start         time.Time
	AlpacaCash    decimal.Decimal
	IBKRCash      decimal.Decimal
	Symbols       []string
	BrokerDeadman time.Duration
	ExecDeadman   time.Duration
	SessionAlert  time.Duration
	FlattenBefore time.Duration
	Policies      map[string]execapp.AccountPolicy
	ExecStore     *execapp.Memory
	LedgerStore   *ledgerapp.Memory
	// ExtraBrokers are additional adapter endpoints (e.g. the real IBKR adapter over its fake
	// gateway); ExtraSwing are their accounts routed for swing.
	ExtraBrokers []execapp.BrokerEndpoint
	ExtraSwing   []string
}

// Harness is a running paper stack.
type Harness struct {
	T          testing.TB
	Ctx        context.Context
	cancel     context.CancelFunc
	Clock      *Clock
	NS         *natsserver.Server
	NC         *nats.Conn
	JS         jetstream.JetStream
	URL        string
	Alpaca     *simstack.Stack
	IBKR       *simstack.Stack
	Ledger     *ledgerapp.App
	Exec       *execapp.App
	LedgerSt   *ledgerapp.Memory
	ExecSt     *execapp.Memory
	Instr      map[string]string
	Exec2Sym   map[string]string
	ExecClient ordersv1.ExecutionServiceClient
	LedgerCl   ledgerv1.LedgerServiceClient
	opts       Options
	brokerAddr map[string]string
	pubMu      sync.Mutex
	execCancel context.CancelFunc
	execDone   chan struct{}
}

// DefaultSymbols are the harness instruments.
var DefaultSymbols = []string{"AAPL", "MSFT", "NVDA", "AMD", "SPY", "XLK"}

// InstrumentID returns the synthetic instrument ID (a valid ULID shape) for a symbol.
func InstrumentID(sym string) string {
	id := fmt.Sprintf("01J%-23s", sym)
	b := []byte(id)
	for i := range b {
		if b[i] == ' ' {
			b[i] = '0'
		}
	}
	return string(b)
}

// Session returns the regular-session open of the n-th trading day starting 2026-10-12 (UTC).
func SessionOpen(n int) time.Time {
	day := time.Date(2026, 10, 12, 13, 30, 0, 0, time.UTC)
	for i := 0; i < n; {
		day = day.Add(24 * time.Hour)
		if day.Weekday() != time.Saturday && day.Weekday() != time.Sunday {
			i++
		}
	}
	return day
}

func (h *Harness) publish(subject, msgID string, payload []byte) {
	h.pubMu.Lock()
	defer h.pubMu.Unlock()
	m := nats.NewMsg(subject)
	m.Data = payload
	if _, err := h.JS.PublishMsg(h.Ctx, m, jetstream.WithMsgID(msgID)); err != nil && h.Ctx.Err() == nil {
		h.T.Logf("publish %s: %v", subject, err)
	}
}

func (h *Harness) outboxPublish(m outbox.Message) { h.publish(m.Subject, m.MsgID, m.Payload) }

func serve(t testing.TB, register func(*grpc.Server)) (string, func()) {
	t.Helper()
	lis, err := (&net.ListenConfig{}).Listen(context.Background(), "tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	gs := grpc.NewServer()
	register(gs)
	go func() { _ = gs.Serve(lis) }()
	return lis.Addr().String(), gs.Stop
}

func dial(t testing.TB, addr string) *grpc.ClientConn {
	t.Helper()
	c, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// New starts the stack and waits until both accounts are READY and execution finished startup
// recovery.
func New(t testing.TB, o Options) *Harness {
	t.Helper()
	if o.Start.IsZero() {
		o.Start = SessionOpen(0).Add(30 * time.Minute)
	}
	if o.AlpacaCash.IsZero() {
		o.AlpacaCash = decimal.NewFromInt(20000)
	}
	if o.IBKRCash.IsZero() {
		o.IBKRCash = decimal.NewFromInt(100000)
	}
	if len(o.Symbols) == 0 {
		o.Symbols = DefaultSymbols
	}
	if o.SessionAlert == 0 {
		o.SessionAlert = 300 * time.Millisecond
	}
	ctx, cancel := context.WithCancel(context.Background())
	h := &Harness{T: t, Ctx: ctx, cancel: cancel, Clock: &Clock{}, Instr: map[string]string{}, Exec2Sym: map[string]string{}, opts: o, brokerAddr: map[string]string{}}
	h.Clock.Set(o.Start)
	for _, s := range o.Symbols {
		h.Instr[s] = InstrumentID(s)
		h.Exec2Sym[InstrumentID(s)] = s
	}
	ns, err := natsserver.NewServer(&natsserver.Options{Host: "127.0.0.1", Port: -1, JetStream: true, StoreDir: t.TempDir(), NoLog: true, NoSigs: true})
	if err != nil {
		t.Fatal(err)
	}
	go ns.Start()
	if !ns.ReadyForConnections(10 * time.Second) {
		t.Fatal("nats not ready")
	}
	h.NS, h.URL = ns, ns.ClientURL()
	if h.NC, err = nats.Connect(h.URL); err != nil {
		t.Fatal(err)
	}
	if h.JS, err = jetstream.New(h.NC); err != nil {
		t.Fatal(err)
	}
	if err := bus.EnsureStreams(ctx, h.NC); err != nil {
		t.Fatal(err)
	}
	resolve := func(sym string) string { return h.Instr[sym] }
	log := slog.New(slog.DiscardHandler)
	if os.Getenv("TB_PAPER_DEBUG") != "" {
		log = slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelDebug}))
	}
	h.Alpaca = simstack.Start(ctx, simstack.Config{Accounts: []simstack.AccountConfig{{ID: Alpaca, Cash: o.AlpacaCash}}, Resolve: resolve,
		Publish: h.publish, Now: h.Clock.Now, DeadmanTimeout: o.BrokerDeadman, Log: log})
	h.IBKR = simstack.Start(ctx, simstack.Config{Accounts: []simstack.AccountConfig{{ID: IBKR, Cash: o.IBKRCash, IBKRLike: true}}, Resolve: resolve,
		Publish: h.publish, Now: h.Clock.Now, Log: log})
	var stop []func()
	addr, s1 := serve(t, func(gs *grpc.Server) { brokerv1.RegisterBrokerGatewayServiceServer(gs, h.Alpaca.Server) })
	h.brokerAddr["broker-gateway"] = addr
	addr2, s2 := serve(t, func(gs *grpc.Server) { brokerv1.RegisterBrokerGatewayServiceServer(gs, h.IBKR.Server) })
	h.brokerAddr["broker-gateway-ibkr"] = addr2
	stop = append(stop, s1, s2)

	h.LedgerSt = o.LedgerStore
	if h.LedgerSt == nil {
		h.LedgerSt = ledgerapp.NewMemoryStore(h.outboxPublish)
	} else {
		h.LedgerSt.Publish = h.outboxPublish
	}
	lc := ledgerapp.DefaultConfig()
	lc.Now = h.Clock.Now
	la, err := ledgerapp.New(ctx, ledgerapp.Deps{Config: lc, Store: h.LedgerSt, Resolver: ledgerapp.StaticResolver(h.Instr), JS: h.JS, Log: log, TickEvery: 50 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	h.Ledger = la
	go la.Run(ctx)
	laddr, s3 := serve(t, la.Register)
	stop = append(stop, s3)
	h.LedgerCl = ledgerv1.NewLedgerServiceClient(dial(t, laddr))

	h.ExecSt = o.ExecStore
	if h.ExecSt == nil {
		h.ExecSt = execapp.NewMemoryStore(h.outboxPublish)
	} else {
		h.ExecSt.Publish = h.outboxPublish
	}
	h.startExec()
	t.Cleanup(func() {
		h.stopExec()
		cancel()
		for _, s := range stop {
			s()
		}
		h.Alpaca.Close()
		h.IBKR.Close()
		h.NC.Close()
		ns.Shutdown()
	})
	h.WaitFor("brokers ready", 10*time.Second, func() bool { return h.Alpaca.Ready() && h.IBKR.Ready() })
	h.WaitFor("execution recovery", 15*time.Second, func() bool { return !h.Exec.Engine.Recovering() })
	h.Quiesce()
	return h
}

// ExecConfig is the execution configuration the harness uses.
func (h *Harness) ExecConfig() execapp.Config {
	ec := execapp.DefaultConfig()
	ec.Now, ec.Wall = h.Clock.Now, time.Now
	ec.Routing = map[commonv1.Horizon][]string{commonv1.Horizon_HORIZON_INTRADAY: {Alpaca}, commonv1.Horizon_HORIZON_SWING: append([]string{IBKR}, h.opts.ExtraSwing...)}
	ec.Policies = map[string]execapp.AccountPolicy{
		Alpaca: {MarginMultiple: decimal.NewFromInt(1), MarginUtilCap: decimal.NewFromInt(1), PDTGuard: "off", PDTMax: 3, Segregation: true},
		IBKR:   {MarginMultiple: decimal.NewFromInt(2), MarginUtilCap: decimal.RequireFromString("0.5"), PDTGuard: "off", PDTMax: 3, Segregation: false},
	}
	for k, v := range h.opts.Policies {
		ec.Policies[k] = v
	}
	ec.SessionAlertAfter = h.opts.SessionAlert
	ec.CancelWait = 3 * time.Second
	ec.UnknownGiveUp = 5 * time.Second
	if h.opts.ExecDeadman > 0 {
		ec.DeadmanTimeout = h.opts.ExecDeadman
	} else {
		ec.DeadmanTimeout = time.Hour
	}
	if h.opts.FlattenBefore > 0 {
		ec.FlattenBeforeClose = h.opts.FlattenBefore
	}
	return ec
}

func (h *Harness) startExec() {
	t := h.T
	brokers := &execapp.Brokers{Endpoints: []execapp.BrokerEndpoint{
		{Audience: "broker-gateway", Client: brokerv1.NewBrokerGatewayServiceClient(dial(t, h.brokerAddr["broker-gateway"]))},
		{Audience: "broker-gateway-ibkr", Client: brokerv1.NewBrokerGatewayServiceClient(dial(t, h.brokerAddr["broker-gateway-ibkr"]))},
	}}
	brokers.Endpoints = append(brokers.Endpoints, h.opts.ExtraBrokers...)
	instr := execapp.StaticInstruments{}
	for sym, id := range h.Instr {
		instr[id] = &commonv1.Instrument{InstrumentId: id, Symbol: sym, AssetClass: commonv1.AssetClass_ASSET_CLASS_US_EQUITY, Currency: "USD"}
	}
	ctx, cancel := context.WithCancel(h.Ctx)
	ea, err := execapp.New(ctx, execapp.Deps{Config: h.ExecConfig(), Store: h.ExecSt, Brokers: brokers, Ledger: &execapp.Ledger{Client: h.LedgerCl},
		Instruments: instr, NC: h.NC, JS: h.JS, TickEvery: 50 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	h.Exec, h.execCancel, h.execDone = ea, cancel, make(chan struct{})
	go func() { ea.Run(ctx); close(h.execDone) }()
	addr, stop := serve(t, ea.Register)
	go func() { <-ctx.Done(); stop() }()
	h.ExecClient = ordersv1.NewExecutionServiceClient(dial(t, addr))
}

func (h *Harness) stopExec() {
	if h.execCancel != nil {
		h.execCancel()
		<-h.execDone
		h.execCancel = nil
	}
}

// RestartExecution stops execution and starts a fresh process on the same store (OE-020).
func (h *Harness) RestartExecution() {
	h.stopExec()
	h.startExec()
}

// WaitFor polls cond until it holds or fails the test.
func (h *Harness) WaitFor(what string, d time.Duration, cond func() bool) {
	h.T.Helper()
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	h.T.Fatalf("timed out waiting for %s", what)
}

// Quiesce waits until every durable consumer has drained, the order stream has been quiet for a
// few polls, and the ledger has reconciled both accounts against a snapshot taken after the call
// started, so ledger and OMS state reflect everything the brokers did.
func (h *Harness) Quiesce() {
	h.T.Helper()
	t0 := h.Clock.Now().UnixNano()
	stable := 0
	var lastSeq uint64
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		pending := uint64(0)
		var seq uint64
		for _, stream := range []string{"ORDERS", "LEDGER", "REF"} {
			s, err := h.JS.Stream(h.Ctx, stream)
			if err != nil {
				continue
			}
			if info, err := s.Info(h.Ctx); err == nil && stream == "ORDERS" {
				seq = info.State.LastSeq
			}
			lister := s.ListConsumers(h.Ctx)
			for ci := range lister.Info() {
				pending += ci.NumPending + uint64(ci.NumAckPending)
			}
		}
		fresh := true
		for _, acct := range []string{Alpaca, IBKR} {
			if r := h.Ledger.Engine.LastRecon(acct); r == nil || r.GetSnapshotAsOf() < t0 {
				fresh = false
			}
		}
		if pending == 0 && seq == lastSeq && fresh {
			stable++
			if stable >= 3 {
				return
			}
		} else {
			stable = 0
		}
		lastSeq = seq
		time.Sleep(100 * time.Millisecond)
	}
	h.T.Fatal("bus did not quiesce")
}

// SetPrice moves both simulated markets.
func (h *Harness) SetPrice(sym string, px string) {
	p := decimal.RequireFromString(px)
	h.Alpaca.SetPrice(sym, p)
	h.IBKR.SetPrice(sym, p)
}

// PublishBar publishes a final bar for sym closing at the current clock (available 60 s after the
// close, MD-002) and sets the simulated price to its close.
func (h *Harness) PublishBar(sym string, tf mdv1.Timeframe, width time.Duration, o, hi, lo, c string) {
	close := h.Clock.Now()
	b := &mdv1.Bar{InstrumentId: h.Instr[sym], Timeframe: tf, TsOpen: close.Add(-width).UnixNano(), TsClose: close.UnixNano(),
		Open: &commonv1.Decimal{Value: o}, High: &commonv1.Decimal{Value: hi}, Low: &commonv1.Decimal{Value: lo}, Close: &commonv1.Decimal{Value: c},
		Volume: &commonv1.Decimal{Value: "100000"}, Feed: "sim", IsFinal: true, AvailableAt: close.Add(time.Minute).UnixNano(), Source: "sim"}
	payload, _ := proto.Marshal(b)
	h.publish(fmt.Sprintf("md.bar.%s.%s", tfName(tf), h.Instr[sym]), fmt.Sprintf("bar:%s:%d", h.Instr[sym], b.GetTsOpen()), payload)
	h.SetPrice(sym, c)
}

func tfName(tf mdv1.Timeframe) string {
	switch tf {
	case mdv1.Timeframe_TIMEFRAME_1M:
		return "1m"
	case mdv1.Timeframe_TIMEFRAME_5M:
		return "5m"
	case mdv1.Timeframe_TIMEFRAME_15M:
		return "15m"
	case mdv1.Timeframe_TIMEFRAME_1D:
		return "1d"
	}
	return "x"
}

// Fresh publishes a final 1-minute bar at px for every symbol (prices fresh at the current clock).
func (h *Harness) Fresh(px map[string]string) {
	for _, sym := range h.opts.Symbols {
		p := px[sym]
		if p == "" {
			p = "100"
		}
		h.PublishBar(sym, mdv1.Timeframe_TIMEFRAME_1M, time.Minute, p, p, p, p)
	}
	h.WaitFor("bars visible to execution", 5*time.Second, func() bool {
		for _, sym := range h.opts.Symbols {
			if p := px[sym]; p != "" && h.Exec.Engine.LastPrice(h.Instr[sym]).String() != decimal.RequireFromString(p).String() {
				return false
			}
		}
		return true
	})
}

// Strategy registers a strategy through the registry seed path (paper mode, allocation).
func (h *Harness) Strategy(id, short string, horizon commonv1.Horizon, acct string, alloc string, allow []string, runtime bool) {
	h.T.Helper()
	hz := "intraday"
	if horizon == commonv1.Horizon_HORIZON_SWING {
		hz = "swing"
	}
	reg := &execapp.Registry{Strategies: []execapp.RegistryEntry{{StrategyID: id, ShortID: short, Name: id, Horizon: hz, Account: acct, Mode: "paper",
		Allocation: alloc, Allowlist: allow, Version: "1"}}}
	if runtime {
		reg.Strategies[0].Runtime = []byte(`{"class":"test"}`)
	}
	if err := execapp.Seed(h.Ctx, h.Exec.Engine, reg); err != nil {
		h.T.Fatal(err)
	}
}

// ManualFill places and fills an order in the broker's own app (owner manual).
func (h *Harness) ManualFill(acct, sym string, side commonv1.Side, qty int64) {
	st := h.Alpaca
	if acct == IBKR {
		st = h.IBKR
	}
	st.InjectManualOrder(acct, sym, side, decimal.NewFromInt(qty))
}

// Owners returns the ledger's owner split for an instrument in an account.
func (h *Harness) Owners(acct, sym string) map[string]string {
	v, err := h.LedgerCl.GetAccountView(h.Ctx, &ledgerv1.GetAccountViewRequest{BrokerAccountId: acct})
	if err != nil {
		h.T.Fatal(err)
	}
	out := map[string]string{}
	for _, ib := range v.GetView().GetInstruments() {
		if ib.GetInstrumentId() == h.Instr[sym] {
			for _, o := range ib.GetOwners() {
				out[o.GetOwner()] = o.GetQty().GetValue()
			}
			out["broker"] = ib.GetBrokerQty().GetValue()
		}
	}
	return out
}

// Recon runs an on-demand reconciliation.
func (h *Harness) Recon(acct string) *ledgerv1.ReconciliationResult {
	r, err := h.LedgerCl.Reconcile(h.Ctx, &ledgerv1.ReconcileRequest{BrokerAccountId: acct, Trigger: "on_demand"})
	if err != nil {
		h.T.Fatal(err)
	}
	return r.GetResult()
}

// OpenOrders summarizes open broker orders as "owner:side:type" (owner from the attribution).
func (h *Harness) OpenOrders(acct string) []string {
	st := h.Alpaca
	if acct == IBKR {
		st = h.IBKR
	}
	open, _ := st.OpenOrders(h.Ctx, acct)
	var out []string
	var walk func(o *brokerv1.BrokerOrder)
	walk = func(o *brokerv1.BrokerOrder) {
		switch o.GetState() {
		case ordersv1.OrderState_ORDER_STATE_FILLED, ordersv1.OrderState_ORDER_STATE_CANCELED, ordersv1.OrderState_ORDER_STATE_REJECTED,
			ordersv1.OrderState_ORDER_STATE_EXPIRED, ordersv1.OrderState_ORDER_STATE_REPLACED:
		default:
			out = append(out, fmt.Sprintf("%s:%s:%s:%s", OwnerOf(o.GetClientOrderId()), o.GetInstrument().GetSymbol(), o.GetSide(), o.GetType()))
		}
		for _, l := range o.GetLegs() {
			walk(l)
		}
	}
	for _, o := range open {
		walk(o)
	}
	sort.Strings(out)
	return out
}

// OwnerOf returns the attribution short ID ("manual" for non-platform orders).
func OwnerOf(coid string) string {
	if len(coid) > 4 && coid[:4] == "tb1-" {
		for i := 4; i < len(coid); i++ {
			if coid[i] == '-' {
				return coid[4:i]
			}
		}
	}
	return "manual"
}
