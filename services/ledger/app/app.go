// Package app wires the ledger service (section 06): engine, bus consumers, gRPC server, and the
// session clock. cmd/ledger uses it in production; the Phase 2 paper harness (tests/paper) runs it
// in-process against embedded NATS and the broker simulator.
package app

import (
	"context"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"

	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/ledger/internal/engine"
	"github.com/illyanyc/strawberry/services/ledger/internal/server"
)

// Config is engine.Config re-exported for wiring.
type Config = engine.Config

// DefaultConfig returns the documented defaults.
func DefaultConfig() Config { return engine.DefaultConfig() }

// Store is the persistence interface.
type Store = engine.Store

// Memory is the in-memory store (tests, harness).
type Memory = engine.Memory

// NewMemoryStore returns an in-memory store; publish receives committed outbox messages.
func NewMemoryStore(publish func(outbox.Message)) *Memory {
	m := engine.NewMemory()
	m.Publish = publish
	return m
}

// NewPostgresStore returns the production store.
func NewPostgresStore(pool *pgxpool.Pool, relay *outbox.Relay) Store {
	return &engine.Postgres{Pool: pool, Relay: relay}
}

// StaticResolver maps symbols to instrument IDs.
type StaticResolver = engine.StaticResolver

// NewRefdataResolver resolves through the instrument master.
func NewRefdataResolver(pool *pgxpool.Pool) engine.Resolver {
	return &engine.RefdataResolver{Pool: pool}
}

// AllowedCallers is the gRPC allowlist (SE-040).
var AllowedCallers = server.AllowedCallers

// Deps are the app's collaborators.
type Deps struct {
	Config   Config
	Store    Store
	Resolver engine.Resolver
	JS       jetstream.JetStream
	Log      *slog.Logger
	Registry prometheus.Registerer
	// TickEvery drives session-boundary reconciliation and corporate actions (default 10 s).
	TickEvery time.Duration
	// DurablePrefix namespaces consumer names (harness runs several ledgers on one server).
	DurablePrefix string
}

// App is a running ledger.
type App struct {
	Engine *engine.Engine
	deps   Deps
}

// New loads state and returns the app.
func New(ctx context.Context, d Deps) (*App, error) {
	if d.Log == nil {
		d.Log = slog.New(slog.DiscardHandler)
	}
	if d.TickEvery == 0 {
		d.TickEvery = 10 * time.Second
	}
	if d.DurablePrefix == "" {
		d.DurablePrefix = "ledger"
	}
	e, err := engine.New(ctx, d.Config, d.Store, d.Resolver, d.Log)
	if err != nil {
		return nil, err
	}
	if d.Registry != nil {
		d.Registry.MustRegister(collector{e: e})
	}
	return &App{Engine: e, deps: d}, nil
}

// Register adds LedgerService to gs.
func (a *App) Register(gs *grpc.Server) {
	ledgerv1.RegisterLedgerServiceServer(gs, &server.Server{E: a.Engine})
}

// Server returns the LedgerService implementation (in-process clients in tests).
func (a *App) Server() ledgerv1.LedgerServiceServer { return &server.Server{E: a.Engine} }

func decode[T proto.Message](m jetstream.Msg, v T) error {
	if err := proto.Unmarshal(m.Data(), v); err != nil {
		return bus.ErrPoison
	}
	return nil
}

func poisonOr(err error) error {
	if engine.IsBadInput(err) {
		return bus.ErrPoison
	}
	return err
}

// Run consumes the bus and ticks until ctx ends.
func (a *App) Run(ctx context.Context) {
	met := bus.NewConsumerMetrics(a.deps.Registry)
	p := a.deps.DurablePrefix
	go bus.Consume(ctx, a.deps.JS, bus.ConsumerSpec{Stream: "ORDERS", Durable: p + "-exec", Filters: []string{"exec.>"}},
		func(ctx context.Context, m jetstream.Msg) error {
			x := &ordersv1.ExecutionReport{}
			if err := decode(m, x); err != nil {
				return err
			}
			return poisonOr(a.Engine.ApplyExecution(ctx, x))
		}, a.deps.Log, met)
	go bus.Consume(ctx, a.deps.JS, bus.ConsumerSpec{Stream: "LEDGER", Durable: p + "-snapshots", Filters: []string{"acct.snapshot.>"}},
		func(ctx context.Context, m jetstream.Msg) error {
			s := &ledgerv1.AccountSnapshot{}
			if err := decode(m, s); err != nil {
				return err
			}
			return poisonOr(a.Engine.OnSnapshot(ctx, s))
		}, a.deps.Log, met)
	go bus.Consume(ctx, a.deps.JS, bus.ConsumerSpec{Stream: "LEDGER", Durable: p + "-recon-requests", Filters: []string{"recon.request.>"}},
		func(ctx context.Context, m jetstream.Msg) error {
			r := &ledgerv1.ReconciliationRequest{}
			if err := decode(m, r); err != nil {
				return err
			}
			_, err := a.Engine.Reconcile(ctx, r.GetBrokerAccountId(), "reconnect", r.GetCorrelationId())
			return err
		}, a.deps.Log, met)
	go bus.Consume(ctx, a.deps.JS, bus.ConsumerSpec{Stream: "REF", Durable: p + "-corporate-actions", Filters: []string{"ref.corporate_action.>"}},
		func(ctx context.Context, m jetstream.Msg) error {
			ca := &mdv1.CorporateAction{}
			if err := decode(m, ca); err != nil {
				return err
			}
			return a.Engine.OnCorporateAction(ctx, ca)
		}, a.deps.Log, met)
	t := time.NewTicker(a.deps.TickEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := a.Engine.Tick(ctx); err != nil {
				a.deps.Log.Warn("ledger tick failed", "err", err)
			}
		}
	}
}
