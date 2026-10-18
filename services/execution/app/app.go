// Package app wires the execution service (section 07): the OMS engine, its bus inputs, the
// heartbeat, the strategy registry seed, and the gRPC server. cmd/execution uses it in production;
// the Phase 2 paper harness (tests/paper) runs it in-process.
package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/shopspring/decimal"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/execution/internal/oms"
	"github.com/illyanyc/strawberry/services/execution/internal/server"
)

// Re-exports for wiring outside the module.
type (
	Config            = oms.Config
	AccountPolicy     = oms.AccountPolicy
	Store             = oms.Store
	Memory            = oms.Memory
	Engine            = oms.Engine
	StaticInstruments = oms.StaticInstruments
	// Tx is one store commit; tests use it to seed persisted state (for example a strategy left in
	// live mode by the section 15 go-live flow) before a restart.
	Tx = oms.Tx
)

// DefaultConfig returns the documented defaults.
func DefaultConfig() Config { return oms.DefaultConfig() }

// NewMemoryStore returns an in-memory store; publish receives committed outbox messages.
func NewMemoryStore(publish func(outbox.Message)) *Memory {
	m := oms.NewMemory()
	m.Publish = publish
	return m
}

// NewPostgresStore returns the production store.
func NewPostgresStore(pool *pgxpool.Pool, relay *outbox.Relay) Store {
	return &oms.Postgres{Pool: pool, Relay: relay}
}

// AllowedCallers is the gRPC allowlist (SE-040).
var AllowedCallers = server.AllowedCallers

// ParseRouting parses "intraday=a|b;swing=c" (RM-011).
func ParseRouting(s string) (map[commonv1.Horizon][]string, error) {
	out := map[commonv1.Horizon][]string{}
	for _, part := range strings.Split(s, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		h, accts, ok := strings.Cut(part, "=")
		if !ok {
			return nil, fmt.Errorf("routing %q: want horizon=acct|acct", part)
		}
		hz, err := ParseHorizon(h)
		if err != nil {
			return nil, err
		}
		for _, a := range strings.Split(accts, "|") {
			if a = strings.TrimSpace(a); a != "" {
				out[hz] = append(out[hz], a)
			}
		}
	}
	return out, nil
}

// ParseHorizon parses intraday|swing.
func ParseHorizon(s string) (commonv1.Horizon, error) {
	switch strings.TrimSpace(strings.ToLower(s)) {
	case "intraday":
		return commonv1.Horizon_HORIZON_INTRADAY, nil
	case "swing":
		return commonv1.Horizon_HORIZON_SWING, nil
	}
	return 0, fmt.Errorf("horizon %q: want intraday|swing", s)
}

// ParsePolicies parses "acct:mult=1,cap=1,pdt=off,seg=true;acct2:..." (RM-008, RM-012, RM-014).
func ParsePolicies(s string, def AccountPolicy) (map[string]AccountPolicy, error) {
	out := map[string]AccountPolicy{}
	for _, part := range strings.Split(s, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		acct, kv, ok := strings.Cut(part, ":")
		if !ok {
			return nil, fmt.Errorf("policy %q: want acct:key=value pairs", part)
		}
		p := def
		for _, f := range strings.Split(kv, ",") {
			k, v, _ := strings.Cut(strings.TrimSpace(f), "=")
			var err error
			switch k {
			case "mult":
				p.MarginMultiple, err = decimal.NewFromString(v)
			case "cap":
				p.MarginUtilCap, err = decimal.NewFromString(v)
			case "pdt":
				if v != "off" && v != "legacy" {
					err = fmt.Errorf("pdt must be off|legacy")
				}
				p.PDTGuard = v
			case "seg":
				p.Segregation = v == "true"
			default:
				err = fmt.Errorf("unknown key %q", k)
			}
			if err != nil {
				return nil, fmt.Errorf("policy %s: %w", acct, err)
			}
		}
		out[strings.TrimSpace(acct)] = p
	}
	return out, nil
}

// RegistryEntry is one strategy in the registry seed file (shared with strategy-runtime).
type RegistryEntry struct {
	StrategyID string            `json:"strategy_id"`
	ShortID    string            `json:"short_id"`
	Name       string            `json:"name"`
	Horizon    string            `json:"horizon"`
	Account    string            `json:"broker_account_id"`
	Mode       string            `json:"mode"`
	Allocation string            `json:"allocation"`
	AllowShort bool              `json:"allow_short"`
	Allowlist  []string          `json:"segregation_allowlist"`
	Version    string            `json:"version"`
	CodeCommit string            `json:"code_commit"`
	ParamsHash string            `json:"params_hash"`
	Runtime    json.RawMessage   `json:"runtime"`
	Limits     map[string]string `json:"limits"`
}

// Registry is the seed file shape.
type Registry struct {
	Strategies []RegistryEntry `json:"strategies"`
}

// LoadRegistry reads a registry seed file ("" = none).
func LoadRegistry(path string) (*Registry, error) {
	if path == "" {
		return &Registry{}, nil
	}
	b, err := os.ReadFile(path) //nolint:gosec // operator-provided config path
	if err != nil {
		return nil, err
	}
	var r Registry
	if err := json.Unmarshal(b, &r); err != nil {
		return nil, fmt.Errorf("registry %s: %w", path, err)
	}
	return &r, nil
}

// Seed registers every entry (SM-001: new entries start disabled, shadow, or paper; an existing
// entry keeps its mode). The initial allocation is applied only on first registration and only to
// paper accounts; changes after that are owner actions with step-up (RM-007).
func Seed(ctx context.Context, e *Engine, r *Registry) error {
	for _, x := range r.Strategies {
		h, err := ParseHorizon(x.Horizon)
		if err != nil {
			return err
		}
		mode := ordersv1.StrategyMode_STRATEGY_MODE_DISABLED
		switch x.Mode {
		case "paper":
			mode = ordersv1.StrategyMode_STRATEGY_MODE_PAPER
		case "shadow":
			mode = ordersv1.StrategyMode_STRATEGY_MODE_SHADOW
		}
		alloc := decimal.Zero
		if x.Allocation != "" {
			if alloc, err = decimal.NewFromString(x.Allocation); err != nil {
				return fmt.Errorf("strategy %s allocation: %w", x.StrategyID, err)
			}
		}
		st := &ordersv1.Strategy{StrategyId: x.StrategyID, ShortId: x.ShortID, Name: x.Name, Horizon: h, Mode: mode, BrokerAccountId: x.Account,
			Version: x.Version, CodeCommit: x.CodeCommit, ParamsHash: x.ParamsHash, AllowShort: x.AllowShort, SegregationAllowlist: x.Allowlist, RuntimeHosted: len(x.Runtime) > 0}
		if _, err := e.RegisterStrategy(ctx, st, alloc, "config:registry"); err != nil {
			return fmt.Errorf("strategy %s: %w", x.StrategyID, err)
		}
		if len(x.Limits) > 0 {
			l := &ordersv1.RiskLimits{}
			for k, v := range x.Limits {
				if err := setLimit(l, k, v); err != nil {
					return fmt.Errorf("strategy %s limit %s: %w", x.StrategyID, k, err)
				}
			}
			cur, _ := e.Limits("strategy:" + x.StrategyID)
			if proto.Size(cur) == 0 {
				if _, err := e.SetLimits(ctx, "strategy:"+x.StrategyID, l, &ordersv1.Actor{Name: "config:registry", StepUpMethod: "passkey"}); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func setLimit(l *ordersv1.RiskLimits, k, v string) error {
	dv := &commonv1.Decimal{Value: v}
	if _, err := decimal.NewFromString(v); err != nil {
		return err
	}
	iv := func() int32 { n, _ := decimal.NewFromString(v); return int32(n.IntPart()) }
	switch k {
	case "max_order_notional":
		l.MaxOrderNotional = dv
	case "max_order_qty":
		l.MaxOrderQty = dv
	case "max_position_notional":
		l.MaxPositionNotional = dv
	case "max_position_pct_allocation":
		l.MaxPositionPctAllocation = dv
	case "max_gross_exposure":
		l.MaxGrossExposure = dv
	case "max_daily_loss":
		l.MaxDailyLoss = dv
	case "max_drawdown":
		l.MaxDrawdown = dv
	case "max_positions":
		l.MaxPositions = iv()
	case "max_orders_per_minute":
		l.MaxOrdersPerMinute = iv()
	case "max_net_exposure":
		l.MaxNetExposure = dv
	case "max_concentration_pct":
		l.MaxConcentrationPct = dv
	case "price_collar_pct":
		l.PriceCollarPct = dv
	case "fat_finger_multiple":
		l.FatFingerMultiple = dv
	case "max_cancels_per_minute":
		l.MaxCancelsPerMinute = iv()
	case "max_consecutive_losses":
		l.MaxConsecutiveLosses = iv()
	case "duplicate_window_seconds":
		l.DuplicateWindowSeconds = iv()
	case "max_open_orders":
		l.MaxOpenOrders = iv()
	default:
		return fmt.Errorf("unknown limit")
	}
	return nil
}

// Deps are the app's collaborators.
type Deps struct {
	Config      Config
	Store       Store
	Brokers     oms.Brokers
	Ledger      oms.Ledger
	Instruments oms.Instruments
	NC          *nats.Conn
	JS          jetstream.JetStream
	Log         *slog.Logger
	Registry    prometheus.Registerer
	// TickEvery drives lifecycle work (default 1 s).
	TickEvery     time.Duration
	DurablePrefix string
}

// App is a running execution service.
type App struct {
	Engine *Engine
	deps   Deps
}

// New loads the OMS.
func New(ctx context.Context, d Deps) (*App, error) {
	if d.Log == nil {
		d.Log = slog.New(slog.DiscardHandler)
	}
	if d.TickEvery == 0 {
		d.TickEvery = time.Second
	}
	if d.DurablePrefix == "" {
		d.DurablePrefix = "execution"
	}
	d.Config.Log = d.Log
	e, err := oms.New(ctx, d.Config, d.Store, d.Brokers, d.Ledger, d.Instruments)
	if err != nil {
		return nil, err
	}
	if d.NC != nil {
		nc := d.NC
		e.Heartbeat = func(st *ordersv1.TradingStatus) {
			b, _ := proto.Marshal(st)
			_ = nc.Publish("sys.health.execution", b)
		}
	}
	if d.Registry != nil {
		d.Registry.MustRegister(collector{e: e})
	}
	return &App{Engine: e, deps: d}, nil
}

// Register adds ExecutionService to gs.
func (a *App) Register(gs *grpc.Server) {
	ordersv1.RegisterExecutionServiceServer(gs, &server.Server{E: a.Engine})
}

// Server returns the ExecutionService implementation.
func (a *App) Server() ordersv1.ExecutionServiceServer { return &server.Server{E: a.Engine} }

func decode[T proto.Message](m jetstream.Msg, v T) error {
	if err := proto.Unmarshal(m.Data(), v); err != nil {
		return bus.ErrPoison
	}
	return nil
}

// Run consumes the bus and ticks until ctx ends.
func (a *App) Run(ctx context.Context) {
	e, js, p, log := a.Engine, a.deps.JS, a.deps.DurablePrefix, a.deps.Log
	met := bus.NewConsumerMetrics(a.deps.Registry)
	if js != nil {
		go bus.Consume(ctx, js, bus.ConsumerSpec{Stream: "ORDERS", Durable: p + "-intents", Filters: []string{"ord.intent"}}, func(ctx context.Context, m jetstream.Msg) error {
			it := &ordersv1.OrderIntent{}
			if err := decode(m, it); err != nil {
				return err
			}
			_, err := e.Submit(ctx, it, nil)
			if errors.Is(err, oms.ErrInvalid) {
				return bus.ErrPoison
			}
			return err
		}, log, met)
		go bus.Consume(ctx, js, bus.ConsumerSpec{Stream: "ORDERS", Durable: p + "-order-updates", Filters: []string{"ord.update.>"}}, func(ctx context.Context, m jetstream.Msg) error {
			u := &brokerv1.BrokerOrderUpdate{}
			if err := decode(m, u); err != nil {
				return err
			}
			return e.OnOrderUpdate(ctx, u)
		}, log, met)
		go bus.Consume(ctx, js, bus.ConsumerSpec{Stream: "ORDERS", Durable: p + "-executions", Filters: []string{"exec.>"}}, func(ctx context.Context, m jetstream.Msg) error {
			x := &ordersv1.ExecutionReport{}
			if err := decode(m, x); err != nil {
				return err
			}
			return e.OnExecution(ctx, x)
		}, log, met)
		go bus.Consume(ctx, js, bus.ConsumerSpec{Stream: "ORDERS", Durable: p + "-kill", Filters: []string{"risk.kill"}}, func(ctx context.Context, m jetstream.Msg) error {
			k := &ordersv1.KillSwitchEvent{}
			if err := decode(m, k); err != nil {
				return err
			}
			return e.OnKillEvent(ctx, k)
		}, log, met)
		go bus.Consume(ctx, js, bus.ConsumerSpec{Stream: "LEDGER", Durable: p + "-snapshots", Filters: []string{"acct.snapshot.>"}}, func(ctx context.Context, m jetstream.Msg) error {
			s := &ledgerv1.AccountSnapshot{}
			if err := decode(m, s); err != nil {
				return err
			}
			return e.OnSnapshot(ctx, s)
		}, log, met)
		go bus.Consume(ctx, js, bus.ConsumerSpec{Stream: "LEDGER", Durable: p + "-recon", Filters: []string{"recon.result.>"}}, func(_ context.Context, m jetstream.Msg) error {
			r := &ledgerv1.ReconciliationResult{}
			if err := decode(m, r); err != nil {
				return err
			}
			e.OnRecon(r)
			return nil
		}, log, met)
		go a.consumeBars(ctx)
	}
	if a.deps.NC != nil {
		sub, err := a.deps.NC.Subscribe("sys.health.strategy-runtime", func(*nats.Msg) { e.OnRuntimeHeartbeat() })
		if err == nil {
			defer func() { _ = sub.Unsubscribe() }()
		}
	}
	t := time.NewTicker(a.deps.TickEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			e.Tick(ctx)
		}
	}
}

var intervals = map[mdv1.Timeframe]time.Duration{mdv1.Timeframe_TIMEFRAME_1M: time.Minute, mdv1.Timeframe_TIMEFRAME_5M: 5 * time.Minute,
	mdv1.Timeframe_TIMEFRAME_15M: 15 * time.Minute, mdv1.Timeframe_TIMEFRAME_30M: 30 * time.Minute, mdv1.Timeframe_TIMEFRAME_1H: time.Hour,
	mdv1.Timeframe_TIMEFRAME_1D: 24 * time.Hour}

// consumeBars keeps the latest final bar per instrument (last price and freshness, RM-004). The
// MARKETDATA stream is memory-backed; an ordered consumer starting at the last message per subject
// warms the state on restart.
func (a *App) consumeBars(ctx context.Context) {
	for ctx.Err() == nil {
		c, err := a.deps.JS.OrderedConsumer(ctx, "MARKETDATA", jetstream.OrderedConsumerConfig{FilterSubjects: []string{"md.bar.*.*"},
			DeliverPolicy: jetstream.DeliverLastPerSubjectPolicy})
		if err == nil {
			var cc jetstream.ConsumeContext
			cc, err = c.Consume(func(m jetstream.Msg) {
				b := &mdv1.Bar{}
				if proto.Unmarshal(m.Data(), b) != nil || !b.GetIsFinal() {
					return
				}
				px, perr := decimal.NewFromString(b.GetClose().GetValue())
				if perr != nil {
					return
				}
				a.Engine.OnBar(b.GetInstrumentId(), px, b.GetTsClose(), int64(intervals[b.GetTimeframe()]))
			})
			if err == nil {
				<-ctx.Done()
				cc.Stop()
				return
			}
		}
		a.deps.Log.Warn("bar consumer restarting", "err", err)
		select {
		case <-ctx.Done():
		case <-time.After(time.Second):
		}
	}
}
