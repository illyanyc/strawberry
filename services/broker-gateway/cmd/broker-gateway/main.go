// Command broker-gateway owns broker sessions and credential custody: the write-only
// CredentialService (Phase 0), and from Phase 1 the BrokerGatewayService and
// BrokerMarketDataService for Alpaca accounts and simulator accounts (section 04, ADR-0018/0019).
//
// Subcommands:
//
//	broker-gateway                run the server
//	broker-gateway rewrap-keys    re-wrap all data keys under the active KEK (KEK rotation)
//	broker-gateway healthcheck    container liveness probe
package main

import (
	"context"
	"fmt"
	"maps"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"google.golang.org/grpc"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/libs/go/clock"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/libs/go/secrets"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/alpaca"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/credstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/credsvc"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/deadman"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/execstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/grpcsvc"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/livegate"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/mdhub"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
)

type cfg struct {
	config.Common
	DB       db.Config
	Bus      bus.Config
	GRPCAddr string `env:"TB_GRPC_ADDR" envDefault:":9090" json:"grpc_addr"`
	OwnerID  string `env:"TB_OWNER_ID" envDefault:"owner" json:"owner_id"`
	// NTP server for the clock-skew check (OP-020); empty disables it.
	NTPServer string        `env:"TB_NTP_SERVER" envDefault:"time.cloudflare.com" json:"ntp_server"`
	MaxSkew   time.Duration `env:"TB_CLOCK_MAX_SKEW" envDefault:"250ms" json:"max_skew"`

	// Alpaca accounts as "<broker_account_id>:<paper|live>" pairs (credentials come from the
	// encrypted store, written through the UI).
	AlpacaAccounts []string `env:"TB_ALPACA_ACCOUNTS" envDefault:"alpaca-paper:paper" envSeparator:"," json:"alpaca_accounts"`
	// Simulator accounts (development and tests); empty disables them.
	SimAccounts []string `env:"TB_SIM_ACCOUNTS" envSeparator:"," json:"sim_accounts"`
	// Account whose credential is used for market data (one upstream socket per feed, BR-015).
	DataAccount string `env:"TB_ALPACA_DATA_ACCOUNT" envDefault:"alpaca-paper" json:"data_account"`
	// Feeds this credential may use; first is the default (alpaca.sip needs Algo Trader Plus, O2).
	DataFeeds []string `env:"TB_ALPACA_DATA_FEEDS" envDefault:"alpaca.sip,alpaca.iex" envSeparator:"," json:"data_feeds"`
	// BR-016 budgets (80% of documented limits).
	TradingRatePerMin int           `env:"TB_ALPACA_TRADING_RATE_PER_MIN" envDefault:"160" json:"trading_rate_per_min"`
	DataRatePerMin    int           `env:"TB_ALPACA_DATA_RATE_PER_MIN" envDefault:"8000" json:"data_rate_per_min"`
	SnapshotInterval  time.Duration `env:"TB_ALPACA_SNAPSHOT_INTERVAL" envDefault:"15s" json:"snapshot_interval"`
	// RM-017: cancel working platform orders when the execution heartbeat stops (0 disables).
	DeadmanTimeout time.Duration `env:"TB_DEADMAN_TIMEOUT" envDefault:"30s" json:"deadman_timeout"`
	// BR-013: global live flag (the per-account audited enablement lives in broker.live_enablement).
	TradingLiveEnabled bool `env:"TB_TRADING_LIVE_ENABLED" envDefault:"false" json:"trading_live_enabled"`
}

// credCache decrypts a credential at most once a minute per account (rotations apply within 60 s).
type credCache struct {
	store *credstore.Store
	mu    sync.Mutex
	m     map[string]cachedCred
}

type cachedCred struct {
	c  alpaca.Credentials
	at time.Time
}

func (cc *credCache) fn(account string, env credstore.Environment) alpaca.CredentialFunc {
	return func(ctx context.Context) (alpaca.Credentials, error) {
		key := account + "|" + string(env)
		cc.mu.Lock()
		if c, ok := cc.m[key]; ok && time.Since(c.at) < time.Minute {
			cc.mu.Unlock()
			return c.c, nil
		}
		cc.mu.Unlock()
		fields, err := cc.store.Get(ctx, account, env)
		if err != nil {
			return alpaca.Credentials{}, fmt.Errorf("%w (%s)", adapter.ErrNoCredential, account)
		}
		c, err := alpaca.FieldsToCredentials(fields)
		for k := range fields {
			fields[k] = ""
		}
		if err != nil {
			return alpaca.Credentials{}, err
		}
		cc.mu.Lock()
		cc.m[key] = cachedCred{c: c, at: time.Now()}
		cc.mu.Unlock()
		return c, nil
	}
}

type auditor struct{ w *audit.Writer }

func (a auditor) Append(ctx context.Context, e audit.Event) error {
	_, _, err := a.w.Append(ctx, e)
	return err
}

func parseAccount(s string) (string, brokerv1.Environment, credstore.Environment, error) {
	id, env, ok := strings.Cut(strings.TrimSpace(s), ":")
	switch {
	case !ok || id == "":
		return "", 0, "", fmt.Errorf("account %q: want <id>:<paper|live>", s)
	case env == "paper":
		return id, brokerv1.Environment_ENVIRONMENT_PAPER, credstore.Paper, nil
	case env == "live":
		return id, brokerv1.Environment_ENVIRONMENT_LIVE, credstore.Live, nil
	default:
		return "", 0, "", fmt.Errorf("account %q: environment must be paper or live", s)
	}
}

func main() {
	var c cfg
	rt, err := svc.Start(&c, &c.Common)
	if err != nil {
		svc.Fatal(nil, "startup", err)
	}
	defer rt.Close()
	if c.DB.User == "" {
		c.DB.User = "svc_broker_gateway"
	}
	if c.Bus.User == "" {
		c.Bus.User = "broker_gateway"
	}
	pool, err := db.Connect(rt.Ctx, c.DB, rt.Secrets)
	if err != nil {
		svc.Fatal(rt.Log, "database", err)
	}
	defer pool.Close()
	rawKEK, err := rt.Secrets.Get("broker_kek")
	if err != nil {
		svc.Fatal(rt.Log, "KEK", err)
	}
	ring, err := credstore.ParseKeyRing(rawKEK)
	secrets.Zero(rawKEK)
	if err != nil {
		svc.Fatal(rt.Log, "KEK", err)
	}
	store := credstore.New(pool, ring)

	if len(os.Args) > 1 && os.Args[1] == "rewrap-keys" {
		n, err := store.RewrapAll(rt.Ctx)
		if err != nil {
			fmt.Fprintln(os.Stderr, "rewrap-keys:", err)
			os.Exit(1)
		}
		fmt.Printf("re-wrapped %d credential(s) under KEK %q\n", n, ring.Active)
		return
	}

	nc, err := bus.Connect(c.Bus, rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "bus", err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		svc.Fatal(rt.Log, "jetstream", err)
	}
	relay := outbox.NewRelay(pool, "broker.outbox", outbox.JetStreamPublisher{JS: js}, rt.Log, outbox.NewMetrics(rt.Ops.Registry(), "broker.outbox"))
	go relay.Run(rt.Ctx)
	events := &execstore.Postgres{Pool: pool, Relay: relay}
	aud := auditor{w: audit.NewWriter(pool)}
	gate := livegate.New(c.TradingLiveEnabled, pool)
	metrics := adapter.NewMetrics(rt.Ops.Registry())
	opts := adapter.DefaultOptions()
	opts.SnapshotInterval = c.SnapshotInterval
	creds := &credCache{store: store, m: map[string]cachedCred{}}
	httpClient := &http.Client{Timeout: 10 * time.Second}

	var accounts []*adapter.Account
	var dataClient *alpaca.Client
	for _, s := range c.AlpacaAccounts {
		if strings.TrimSpace(s) == "" {
			continue
		}
		id, env, cenv, err := parseAccount(s)
		if err != nil {
			svc.Fatal(rt.Log, "config", err)
		}
		cl := &alpaca.Client{HTTP: httpClient, Endpoints: alpaca.EndpointsFor(env), Creds: creds.fn(id, cenv),
			Trading: resilience.NewLimiter(c.TradingRatePerMin, time.Minute, 10),
			Data:    resilience.NewLimiter(c.DataRatePerMin, time.Minute, 50)}
		b := &alpaca.Broker{AccountID: id, Environment: env, Client: cl, Feeds: c.DataFeeds}
		accounts = append(accounts, adapter.NewAccount(b, events, aud, gate, rt.Log, metrics, opts))
		if id == c.DataAccount {
			dataClient = cl
		}
	}
	for _, id := range c.SimAccounts {
		if id = strings.TrimSpace(id); id != "" {
			accounts = append(accounts, adapter.NewAccount(sim.New(sim.Config{AccountID: id}), events, aud, gate, rt.Log, metrics, opts))
		}
	}
	for _, a := range accounts {
		go a.Run(rt.Ctx)
	}

	if c.DeadmanTimeout > 0 {
		das := make([]deadman.Account, 0, len(accounts))
		for _, a := range accounts {
			das = append(das, a)
		}
		watch := &deadman.Watch{Accounts: das, Timeout: c.DeadmanTimeout, Calendar: calendar.XNYS(), Now: time.Now, Log: rt.Log, Alert: events.Alert}
		if _, err := nc.Subscribe("sys.health.execution", func(*nats.Msg) { watch.Beat() }); err != nil {
			svc.Fatal(rt.Log, "deadman subscribe", err)
		}
		go watch.Run(rt.Ctx)
	}

	mds := &grpcsvc.MarketDataServer{}
	if dataClient != nil {
		mds.REST = dataClient
		mds.Hub = mdhub.New(rt.Ctx, func(feed string) (mdhub.Upstream, error) {
			return alpaca.NewDataStream(feed, dataClient.Endpoints.DataWS, dataClient.Creds, rt.Log)
		}, rt.Ops.Registry())
	}

	verifier, err := svcauth.LoadVerifier(rt.Secrets, c.Service, "api-gateway", "execution", "ledger", "marketdata")
	if err != nil {
		svc.Fatal(rt.Log, "service keys", err)
	}
	allowed := maps.Clone(credsvc.AllowedCallers)
	maps.Copy(allowed, grpcsvc.AllowedCallers)
	gs := grpc.NewServer(grpc.UnaryInterceptor(verifier.UnaryServerInterceptor(allowed)),
		grpc.StreamInterceptor(verifier.StreamServerInterceptor(allowed)))
	brokerv1.RegisterCredentialServiceServer(gs, credsvc.New(store, c.OwnerID, rt.Log))
	brokerv1.RegisterBrokerGatewayServiceServer(gs, grpcsvc.New(accounts))
	brokerv1.RegisterBrokerMarketDataServiceServer(gs, mds)

	rt.Ops.AddCheck("postgres", db.Ping(pool))
	rt.Ops.AddCheck("bus", bus.Ready(nc))
	if c.NTPServer != "" {
		ck := clock.NewChecker(c.NTPServer, time.Minute, c.MaxSkew, rt.Ops.Registry(), rt.Log)
		go ck.Run(rt.Ctx)
	}
	rt.ServeOps()

	lis, err := (&net.ListenConfig{}).Listen(rt.Ctx, "tcp", c.GRPCAddr)
	if err != nil {
		svc.Fatal(rt.Log, "listen", err)
	}
	go func() {
		<-rt.Ctx.Done()
		gs.GracefulStop()
	}()
	rt.Log.Info("broker-gateway listening", "grpc", c.GRPCAddr, "kek", ring.Active, "accounts", len(accounts), "live_flag", c.TradingLiveEnabled)
	if err := gs.Serve(lis); err != nil {
		svc.Fatal(rt.Log, "grpc", err)
	}
	rt.Wait()
}
