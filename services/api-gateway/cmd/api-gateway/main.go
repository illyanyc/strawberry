// Command api-gateway serves the authenticated REST + SSE API and the static UI.
//
// Subcommands:
//
//	api-gateway               run the server
//	api-gateway setup-token   print a one-time first-run setup token to the terminal (never logged)
//	api-gateway healthcheck   container liveness probe
package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"os"
	"regexp"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	signalsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/signals/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/secrets"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/cfaccess"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/gateway"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/webpush"
)

func main() {
	var cfg gateway.Config
	rt, err := svc.Start(&cfg, &cfg.Common)
	if err != nil {
		svc.Fatal(nil, "startup", err)
	}
	defer rt.Close()
	if err := cfg.Validate(); err != nil {
		svc.Fatal(rt.Log, "config", err)
	}

	pool, err := db.Connect(rt.Ctx, cfg.DB, rt.Secrets)
	if err != nil {
		svc.Fatal(rt.Log, "database", err)
	}
	defer pool.Close()
	master, err := loadMasterKey(rt.Secrets)
	if err != nil {
		svc.Fatal(rt.Log, "gateway key", err)
	}

	if len(os.Args) > 2 && os.Args[1] == "tv-secret" {
		// Printed to the operator's terminal only, never logged or sent to the browser (SE-064).
		key, err := rt.Secrets.Get("tv_webhook_key")
		if err != nil {
			fmt.Fprintln(os.Stderr, "tv-secret: secret tv_webhook_key is missing; run make secrets")
			os.Exit(1)
		}
		name := os.Args[2]
		if !regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`).MatchString(name) {
			fmt.Fprintln(os.Stderr, "tv-secret: NAME must be lowercase letters, digits, and dashes (strategy tv-NAME)")
			os.Exit(1)
		}
		fmt.Printf("Webhook secret for strategy tv-%s: %s\n", name, gateway.TVSecret(key, "tv-"+name))
		return
	}

	if len(os.Args) > 1 && os.Args[1] == "setup-token" {
		srv, err := gateway.New(cfg, gateway.Deps{Pool: pool, MasterKey: master, Log: rt.Log})
		if err != nil {
			svc.Fatal(rt.Log, "init", err)
		}
		tok, err := srv.IssueSetupToken(rt.Ctx)
		if err != nil {
			fmt.Fprintln(os.Stderr, "setup-token:", err)
			os.Exit(1)
		}
		// Printed to the operator's terminal only, never to the log stream (SE-007).
		fmt.Printf("One-time setup token (valid 30 minutes): %s\n", tok)
		return
	}

	signer, err := svcauth.LoadSigner(rt.Secrets, cfg.Service)
	if err != nil {
		svc.Fatal(rt.Log, "service key", err)
	}
	creds, conn, err := gateway.DialCredentialClient(cfg.BrokerGatewayAddr, signer)
	if err != nil {
		svc.Fatal(rt.Log, "broker-gateway client", err)
	}
	defer conn.Close()
	trading, closeTrading, err := gateway.DialTrading(cfg, signer)
	if err != nil {
		svc.Fatal(rt.Log, "trading clients", err)
	}
	defer closeTrading()
	research, closeResearch, err := gateway.DialResearch(cfg, signer)
	if err != nil {
		svc.Fatal(rt.Log, "research clients", err)
	}
	defer closeResearch()
	analyst, closeAnalyst, err := gateway.DialAnalyst(cfg, signer)
	if err != nil {
		svc.Fatal(rt.Log, "analyst clients", err)
	}
	defer closeAnalyst()
	monitor, closeMonitor, err := gateway.DialMonitor(cfg, signer)
	if err != nil {
		svc.Fatal(rt.Log, "monitor client", err)
	}
	defer closeMonitor()

	var alerts gateway.AlertPublisher
	var market gateway.MarketBus
	var signals gateway.SignalBus
	nc, err := bus.Connect(cfg.Bus, rt.Secrets, cfg.Service)
	if err != nil {
		rt.Log.Warn("bus unavailable; security alerts will only be logged", "error", err)
	} else {
		defer nc.Close()
		js, err := jetstream.New(nc)
		if err != nil {
			svc.Fatal(rt.Log, "jetstream", err)
		}
		alerts = natsAlerts{js: js}
		market = natsMarket{nc: nc}
		signals = natsSignals{js: js}
		rt.Ops.AddCheck("bus", bus.Ready(nc))
	}

	push := loadPush(rt, cfg, pool)
	var tvKey []byte
	if cfg.TVWebhookAddr != "" {
		if k, err := rt.Secrets.Get("tv_webhook_key"); err == nil {
			tvKey = k
		} else {
			rt.Log.Warn("TradingView intake disabled: secret tv_webhook_key missing (run make secrets)")
		}
	}
	var access gateway.AccessVerifier
	if cfg.CFAccessTeam != "" {
		access = &cfaccess.Verifier{Team: cfg.CFAccessTeam}
	}
	srv, err := gateway.New(cfg, gateway.Deps{Pool: pool, MasterKey: master, Log: rt.Log, Alerts: alerts, Creds: creds, Trading: trading, Research: research,
		Analyst: analyst, Monitor: monitor, Market: market, Signals: signals, Push: push, TVKey: tvKey, AccessVerifier: access})
	if err != nil {
		svc.Fatal(rt.Log, "init", err)
	}
	srv.StartFillNotifier(rt.Ctx)
	rt.Ops.AddCheck("postgres", db.Ping(pool))
	rt.ServeOps()

	httpSrv := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           srv.Handler(rt.Ops.HTTPMetrics()),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		IdleTimeout:       120 * time.Second,
		// No WriteTimeout: SSE streams are long-lived; handlers bound their own work.
	}
	extra := []*http.Server{}
	if cfg.InternalAddr != "" {
		extra = append(extra, &http.Server{Addr: cfg.InternalAddr, Handler: srv.InternalHandler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 60 * time.Second})
	}
	if cfg.TVWebhookAddr != "" && len(tvKey) > 0 {
		// The webhook listener answers within TradingView's 3 s budget; nothing else is served on it.
		extra = append(extra, &http.Server{Addr: cfg.TVWebhookAddr, Handler: srv.WebhookHandler(), ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second})
	}
	for _, x := range extra {
		go func() {
			rt.Log.Info("api-gateway auxiliary listener", "addr", x.Addr)
			if err := x.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				rt.Log.Error("auxiliary listener failed", "addr", x.Addr, "error", err)
			}
		}()
	}
	go func() {
		<-rt.Ctx.Done()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(ctx)
		for _, x := range extra {
			_ = x.Shutdown(ctx)
		}
	}()
	rt.Log.Info("api-gateway listening", "addr", cfg.HTTPAddr, "rp_id", cfg.RPID)
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		svc.Fatal(rt.Log, "http", err)
	}
	rt.Wait()
}

func loadMasterKey(store *secrets.Store) ([]byte, error) {
	raw, err := store.Get("gateway_auth_key")
	if err != nil {
		return nil, err
	}
	defer secrets.Zero(raw)
	return base64.StdEncoding.DecodeString(string(raw))
}

type natsMarket struct{ nc *nats.Conn }

func (n natsMarket) Subscribe(subject string, fn func([]byte)) (func(), error) {
	sub, err := n.nc.Subscribe(subject, func(m *nats.Msg) { fn(m.Data) })
	if err != nil {
		return nil, err
	}
	return func() { _ = sub.Unsubscribe() }, nil
}

// loadPush configures Web Push from the VAPID key and the Alertmanager webhook token; without the
// key, push is disabled and the Notifications page says so (existing installs: run make secrets).
func loadPush(rt *svc.Runtime, cfg gateway.Config, pool *pgxpool.Pool) gateway.PushDeps {
	deps := gateway.PushDeps{Store: gateway.PGPushStore{Pool: pool}, Redact: cfg.PushRedact, Hosts: cfg.PushAllowedHosts}
	if tok, err := rt.Secrets.Get("alertmanager_webhook_token"); err == nil {
		deps.AMToken = bytes.TrimSpace(tok)
	}
	raw, err := rt.Secrets.Get("webpush_vapid_key")
	if err != nil {
		rt.Log.Warn("Web Push disabled: secret webpush_vapid_key missing (run make secrets)")
		return deps
	}
	keys, err := webpush.ParseKeys(raw)
	secrets.Zero(raw)
	if err != nil {
		rt.Log.Warn("Web Push disabled: bad VAPID key", "error", err)
		return deps
	}
	counter := prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_gateway_webpush_deliveries_total", Help: "Web Push deliveries by result (ok, gone, error)."}, []string{"result"})
	rt.Ops.Registry().MustRegister(counter)
	deps.Pusher = &webpush.Sender{Keys: keys, Subject: cfg.PushSubject, Client: &http.Client{Timeout: 10 * time.Second}}
	deps.Public = keys.PublicKeyB64()
	deps.Metrics = func(result string) { counter.WithLabelValues(result).Inc() }
	return deps
}

// natsSignals reads the SIGNALS stream with short-lived ordered consumers filtered to one
// instrument (sig.*.<instrument_id>) and publishes TradingView signals with a dedupe ID.
type natsSignals struct{ js jetstream.JetStream }

func (n natsSignals) Publish(ctx context.Context, subject, msgID string, sig *signalsv1.Signal) error {
	return bus.Publish(ctx, n.js, subject, msgID, sig)
}

func (n natsSignals) Recent(ctx context.Context, instrumentID string, since time.Time, limit int) ([]*signalsv1.Signal, error) {
	cons, err := n.js.OrderedConsumer(ctx, "SIGNALS", jetstream.OrderedConsumerConfig{
		FilterSubjects: []string{"sig.*." + instrumentID}, DeliverPolicy: jetstream.DeliverByStartTimePolicy, OptStartTime: &since,
		InactiveThreshold: 30 * time.Second,
	})
	if err != nil {
		return nil, err
	}
	pending := int(cons.CachedInfo().NumPending)
	const hardCap = 20000
	pending = min(pending, hardCap)
	out := make([]*signalsv1.Signal, 0, min(pending, limit))
	read := 0
	for read < pending {
		batch, err := cons.Fetch(min(pending-read, 500), jetstream.FetchMaxWait(time.Second))
		if err != nil {
			return nil, err
		}
		got := 0
		for m := range batch.Messages() {
			got++
			var sg signalsv1.Signal
			if proto.Unmarshal(m.Data(), &sg) == nil {
				out = append(out, &sg)
				if len(out) > limit {
					out = out[1:]
				}
			}
		}
		if batch.Error() != nil || got == 0 {
			break
		}
		read += got
	}
	return out, nil
}

type natsAlerts struct{ js jetstream.JetStream }

func (n natsAlerts) Publish(ctx context.Context, a *alertsv1.Alert) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return bus.Publish(ctx, n.js, "alert.security", a.GetAlertId(), a)
}
