// Command api-gateway serves the authenticated REST + SSE API and the static UI.
//
// Subcommands:
//
//	api-gateway               run the server
//	api-gateway setup-token   print a one-time first-run setup token to the terminal (never logged)
//	api-gateway healthcheck   container liveness probe
package main

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/nats-io/nats.go/jetstream"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/secrets"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/gateway"
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

	var alerts gateway.AlertPublisher
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
		rt.Ops.AddCheck("bus", bus.Ready(nc))
	}

	srv, err := gateway.New(cfg, gateway.Deps{Pool: pool, MasterKey: master, Log: rt.Log, Alerts: alerts, Creds: creds})
	if err != nil {
		svc.Fatal(rt.Log, "init", err)
	}
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
	go func() {
		<-rt.Ctx.Done()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(ctx)
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

type natsAlerts struct{ js jetstream.JetStream }

func (n natsAlerts) Publish(ctx context.Context, a *alertsv1.Alert) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	return bus.Publish(ctx, n.js, "alert.security", a.GetAlertId(), a)
}
