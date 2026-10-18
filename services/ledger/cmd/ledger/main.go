// Command ledger owns real-account snapshots, per-strategy virtual ledgers, the manual bucket, lots,
// the broker-lot shadow ledger, reservations, and reconciliation (section 06, ADR-0024). It serves
// tb.ledger.v1.LedgerService on TB_GRPC_ADDR and consumes exec.*, acct.snapshot.*, recon.request.*,
// and ref.corporate_action.* from JetStream.
package main

import (
	"net"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/shopspring/decimal"
	"google.golang.org/grpc"

	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/ledger/app"
)

type cfg struct {
	config.Common
	DB       db.Config
	Bus      bus.Config
	GRPCAddr string `env:"TB_GRPC_ADDR" envDefault:":9090" json:"grpc_addr"`
	// VP-013 / VP-009 / VP-020 policy (documented defaults).
	ReconGrace      time.Duration `env:"TB_LEDGER_RECON_GRACE" envDefault:"60s" json:"recon_grace"`
	ReconStaleAfter time.Duration `env:"TB_LEDGER_RECON_STALE_AFTER" envDefault:"90s" json:"recon_stale_after"`
	CashTolerance   string        `env:"TB_LEDGER_CASH_TOLERANCE" envDefault:"5" json:"cash_tolerance"`
	ReservePct      string        `env:"TB_LEDGER_RESERVE_PCT" envDefault:"0.10" json:"reserve_pct"`
	ReserveAbs      string        `env:"TB_LEDGER_RESERVE_ABS" envDefault:"0" json:"reserve_abs"`
	StrategyBPShare string        `env:"TB_LEDGER_STRATEGY_BP_SHARE" envDefault:"1" json:"strategy_bp_share"`
}

func main() {
	var c cfg
	rt, err := svc.Start(&c, &c.Common)
	if err != nil {
		svc.Fatal(nil, "startup", err)
	}
	defer rt.Close()
	if c.Bus.User == "" {
		c.Bus.User = "ledger"
	}
	if c.DB.User == "" {
		c.DB.User = "svc_ledger"
	}
	ec := app.DefaultConfig()
	ec.Recon.Grace, ec.Recon.StaleAfter = c.ReconGrace, c.ReconStaleAfter
	for _, p := range []struct {
		dst *decimal.Decimal
		v   string
	}{{&ec.Recon.CashTolerance, c.CashTolerance}, {&ec.Capital.ReservePct, c.ReservePct}, {&ec.Capital.ReserveAbs, c.ReserveAbs}, {&ec.Capital.StrategyBPShare, c.StrategyBPShare}} {
		v, err := decimal.NewFromString(p.v)
		if err != nil {
			svc.Fatal(rt.Log, "config", err)
		}
		*p.dst = v
	}
	pool, err := db.Connect(rt.Ctx, c.DB, rt.Secrets)
	if err != nil {
		svc.Fatal(rt.Log, "database", err)
	}
	defer pool.Close()
	nc, err := bus.Connect(c.Bus, rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "bus", err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		svc.Fatal(rt.Log, "jetstream", err)
	}
	relay := outbox.NewRelay(pool, "ledger.outbox", outbox.JetStreamPublisher{JS: js}, rt.Log, outbox.NewMetrics(rt.Ops.Registry(), "ledger.outbox"))
	go relay.Run(rt.Ctx)
	a, err := app.New(rt.Ctx, app.Deps{Config: ec, Store: app.NewPostgresStore(pool, relay), Resolver: app.NewRefdataResolver(pool),
		JS: js, Log: rt.Log, Registry: rt.Ops.Registry()})
	if err != nil {
		svc.Fatal(rt.Log, "ledger load", err)
	}
	go a.Run(rt.Ctx)

	verifier, err := svcauth.LoadVerifier(rt.Secrets, c.Service, "api-gateway", "execution")
	if err != nil {
		svc.Fatal(rt.Log, "service keys", err)
	}
	gs := grpc.NewServer(grpc.UnaryInterceptor(verifier.UnaryServerInterceptor(app.AllowedCallers)))
	a.Register(gs)
	rt.Ops.AddCheck("postgres", db.Ping(pool))
	rt.Ops.AddCheck("bus", bus.Ready(nc))
	rt.ServeOps()
	lis, err := (&net.ListenConfig{}).Listen(rt.Ctx, "tcp", c.GRPCAddr)
	if err != nil {
		svc.Fatal(rt.Log, "listen", err)
	}
	go func() {
		<-rt.Ctx.Done()
		gs.GracefulStop()
	}()
	rt.Log.Info("ledger listening", "grpc", c.GRPCAddr)
	if err := gs.Serve(lis); err != nil {
		svc.Fatal(rt.Log, "grpc", err)
	}
	rt.Wait()
}
