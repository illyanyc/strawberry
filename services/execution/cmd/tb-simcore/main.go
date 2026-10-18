// Command tb-simcore runs the live OMS engine, pre-trade gate, and ledger accounting core for the
// event-driven backtester (section 08, ADR-0031, BT-003). It speaks the tb.backtest.v1 simcore
// protocol on stdin/stdout and logs to stderr; it opens no sockets and reads no secrets.
package main

import (
	"context"
	"log/slog"
	"os"

	"github.com/illyanyc/strawberry/services/execution/internal/simcore"
)

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelWarn})).With("service", "tb-simcore")
	if err := simcore.Serve(context.Background(), simcore.New(log), os.Stdin, os.Stdout); err != nil {
		log.Error("simcore stopped", "err", err)
		os.Exit(1)
	}
}
