// Command execution hosts the OMS, the pre-trade risk gate (D30 routing, intraday-margin guard,
// PDT guard off by default), latched kill switches, dead-man checks, and the trading-day lifecycle
// (section 07, ADR-0025). It serves tb.orders.v1.ExecutionService on TB_GRPC_ADDR, consumes
// ord.intent, ord.update.*, exec.*, risk.kill, acct.snapshot.*, recon.result.*, and md.bar.*, and
// talks to broker adapters and the ledger over gRPC with service tokens.
package main

import (
	"net"
	"strings"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/clock"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/execution/app"
)

type cfg struct {
	config.Common
	DB        db.Config
	Bus       bus.Config
	GRPCAddr  string        `env:"TB_GRPC_ADDR" envDefault:":9090" json:"grpc_addr"`
	NTPServer string        `env:"TB_NTP_SERVER" envDefault:"time.cloudflare.com" json:"ntp_server"`
	MaxSkew   time.Duration `env:"TB_CLOCK_MAX_SKEW" envDefault:"250ms" json:"max_skew"`
	// Broker adapters as audience=host:port pairs (D30: broker-gateway for Alpaca/simulator,
	// broker-gateway-ibkr for IBKR).
	BrokerEndpoints []string `env:"TB_BROKER_ENDPOINTS" envDefault:"broker-gateway=broker-gateway:9090" envSeparator:"," json:"broker_endpoints"`
	LedgerAddr      string   `env:"TB_LEDGER_ADDR" envDefault:"ledger:9090" json:"ledger_addr"`
	// RM-011 routing table and RM-008/RM-012/RM-014 account policies.
	Routing            string        `env:"TB_ROUTING" envDefault:"intraday=alpaca-paper|sim-paper;swing=ibkr-paper-bot" json:"routing"`
	Policies           string        `env:"TB_ACCOUNT_POLICIES" envDefault:"alpaca-paper:mult=1,cap=1,pdt=off,seg=true;sim-paper:mult=1,cap=1,pdt=off,seg=true;ibkr-paper-bot:mult=2,cap=0.5,pdt=off,seg=false" json:"policies"`
	Registry           string        `env:"TB_STRATEGY_REGISTRY" envDefault:"" json:"strategy_registry"`
	FlattenBeforeClose time.Duration `env:"TB_INTRADAY_FLATTEN_BEFORE_CLOSE" envDefault:"10m" json:"flatten_before_close"`
	DeadmanTimeout     time.Duration `env:"TB_DEADMAN_TIMEOUT" envDefault:"30s" json:"deadman_timeout"`
}

func main() {
	var c cfg
	rt, err := svc.Start(&c, &c.Common)
	if err != nil {
		svc.Fatal(nil, "startup", err)
	}
	defer rt.Close()
	if c.Bus.User == "" {
		c.Bus.User = "execution"
	}
	if c.DB.User == "" {
		c.DB.User = "svc_execution"
	}
	ec := app.DefaultConfig()
	if ec.Routing, err = app.ParseRouting(c.Routing); err != nil {
		svc.Fatal(rt.Log, "config", err)
	}
	if ec.Policies, err = app.ParsePolicies(c.Policies, ec.DefaultPolicy); err != nil {
		svc.Fatal(rt.Log, "config", err)
	}
	ec.FlattenBeforeClose, ec.DeadmanTimeout = c.FlattenBeforeClose, c.DeadmanTimeout
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
	signer, err := svcauth.LoadSigner(rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "service key", err)
	}
	brokers := &app.Brokers{Signer: signer}
	for _, ep := range c.BrokerEndpoints {
		aud, addr, ok := strings.Cut(strings.TrimSpace(ep), "=")
		if !ok {
			svc.Fatal(rt.Log, "config", errBadEndpoint(ep))
		}
		conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
		if err != nil {
			svc.Fatal(rt.Log, "broker endpoint", err)
		}
		defer conn.Close()
		brokers.Endpoints = append(brokers.Endpoints, app.BrokerEndpoint{Audience: aud, Client: brokerv1.NewBrokerGatewayServiceClient(conn)})
	}
	lconn, err := grpc.NewClient(c.LedgerAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		svc.Fatal(rt.Log, "ledger", err)
	}
	defer lconn.Close()
	if c.NTPServer != "" {
		ck := clock.NewChecker(c.NTPServer, time.Minute, c.MaxSkew, rt.Ops.Registry(), rt.Log)
		go ck.Run(rt.Ctx)
		ec.ClockOK = ck.Healthy
	}
	relay := outbox.NewRelay(pool, "execution.outbox", outbox.JetStreamPublisher{JS: js}, rt.Log, outbox.NewMetrics(rt.Ops.Registry(), "execution.outbox"))
	go relay.Run(rt.Ctx)
	a, err := app.New(rt.Ctx, app.Deps{Config: ec, Store: app.NewPostgresStore(pool, relay), Brokers: brokers,
		Ledger: &app.Ledger{Client: ledgerv1.NewLedgerServiceClient(lconn), Signer: signer}, Instruments: &app.RefdataInstruments{Pool: pool},
		NC: nc, JS: js, Log: rt.Log, Registry: rt.Ops.Registry()})
	if err != nil {
		svc.Fatal(rt.Log, "execution load", err)
	}
	reg, err := app.LoadRegistry(c.Registry)
	if err != nil {
		svc.Fatal(rt.Log, "registry", err)
	}
	go func() {
		// The ledger may start after execution; retry the seed until it succeeds.
		for rt.Ctx.Err() == nil {
			if err := app.Seed(rt.Ctx, a.Engine, reg); err == nil {
				return
			} else {
				rt.Log.Warn("strategy registry seed failed; retrying", "err", err)
			}
			time.Sleep(5 * time.Second)
		}
	}()
	go a.Run(rt.Ctx)
	verifier, err := svcauth.LoadVerifier(rt.Secrets, c.Service, "api-gateway")
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
	rt.Log.Info("execution listening", "grpc", c.GRPCAddr, "brokers", len(brokers.Endpoints))
	if err := gs.Serve(lis); err != nil {
		svc.Fatal(rt.Log, "grpc", err)
	}
	rt.Wait()
}

type errBadEndpoint string

func (e errBadEndpoint) Error() string {
	return "TB_BROKER_ENDPOINTS entry " + string(e) + ": want audience=host:port"
}
