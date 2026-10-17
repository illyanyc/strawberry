// Package svc is the Go service template: config, logger, secrets, tracing, and the ops listener
// wired the same way in every service (rule OP-010).
package svc

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/ops"
	"github.com/illyanyc/strawberry/libs/go/secrets"
	"github.com/illyanyc/strawberry/libs/go/telemetry"
)

// Runtime bundles what every service needs.
type Runtime struct {
	Common  config.Common
	Log     *slog.Logger
	Ops     *ops.Server
	Secrets *secrets.Store
	// Ctx is canceled on SIGINT/SIGTERM.
	Ctx      context.Context
	cancel   context.CancelFunc
	shutdown func(context.Context) error
}

// Start loads cfg (a struct embedding config.Common, passed by pointer), handles the
// `healthcheck` subcommand, and builds the runtime. common must point at the embedded Common.
func Start(cfg any, common *config.Common) (*Runtime, error) {
	ops.RunHealthcheckIfRequested()
	if err := config.Load(cfg); err != nil {
		return nil, err
	}
	if err := common.Validate(); err != nil {
		return nil, fmt.Errorf("config: %w", err)
	}
	log := logging.New(common.Service, common.LogLevel, os.Stdout)
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	shutdown, err := telemetry.Setup(ctx, common.Service, common.OTLPEndpoint)
	if err != nil {
		cancel()
		return nil, err
	}
	return &Runtime{
		Common: *common, Log: log, Ops: ops.NewServer(common.Service, log),
		Secrets: secrets.NewStore(common.SecretsDir), Ctx: ctx, cancel: cancel, shutdown: shutdown,
	}, nil
}

// ServeOps runs the ops listener in the background; a listener failure cancels the runtime.
func (r *Runtime) ServeOps() {
	go func() {
		if err := r.Ops.ListenAndServe(r.Ctx, r.Common.OpsAddr); err != nil {
			r.Log.Error("ops listener failed", "error", err)
			r.cancel()
		}
	}()
}

// Wait blocks until shutdown is requested, then flushes telemetry.
func (r *Runtime) Wait() {
	<-r.Ctx.Done()
	r.Log.Info("shutting down")
	_ = r.shutdown(context.Background())
}

// Close cancels the runtime context.
func (r *Runtime) Close() { r.cancel() }

// Fatal logs and exits non-zero (fail fast at startup).
func Fatal(log *slog.Logger, msg string, err error) {
	if log == nil {
		log = logging.New("startup", "info", os.Stderr)
	}
	log.Error(msg, "error", err)
	os.Exit(1)
}
