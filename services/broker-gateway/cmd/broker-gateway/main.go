// Command broker-gateway owns broker sessions and credential custody. In Phase 0 it serves the
// write-only CredentialService; broker adapters arrive in Phase 1 (section 04).
//
// Subcommands:
//
//	broker-gateway                run the server
//	broker-gateway rewrap-keys    re-wrap all data keys under the active KEK (KEK rotation)
//	broker-gateway healthcheck    container liveness probe
package main

import (
	"fmt"
	"net"
	"os"
	"time"

	"google.golang.org/grpc"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/clock"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/secrets"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/credstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/credsvc"
)

type cfg struct {
	config.Common
	DB       db.Config
	GRPCAddr string `env:"TB_GRPC_ADDR" envDefault:":9090" json:"grpc_addr"`
	OwnerID  string `env:"TB_OWNER_ID" envDefault:"owner" json:"owner_id"`
	// NTP server for the clock-skew check (OP-020); empty disables it.
	NTPServer string        `env:"TB_NTP_SERVER" envDefault:"time.cloudflare.com" json:"ntp_server"`
	MaxSkew   time.Duration `env:"TB_CLOCK_MAX_SKEW" envDefault:"250ms" json:"max_skew"`
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

	verifier, err := svcauth.LoadVerifier(rt.Secrets, c.Service, "api-gateway")
	if err != nil {
		svc.Fatal(rt.Log, "service keys", err)
	}
	gs := grpc.NewServer(grpc.UnaryInterceptor(verifier.UnaryServerInterceptor(credsvc.AllowedCallers)))
	brokerv1.RegisterCredentialServiceServer(gs, credsvc.New(store, c.OwnerID, rt.Log))

	rt.Ops.AddCheck("postgres", db.Ping(pool))
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
	rt.Log.Info("broker-gateway listening", "grpc", c.GRPCAddr, "kek", ring.Active)
	if err := gs.Serve(lis); err != nil {
		svc.Fatal(rt.Log, "grpc", err)
	}
	rt.Wait()
}
