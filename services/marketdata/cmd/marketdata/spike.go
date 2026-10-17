package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/illyanyc/strawberry/services/marketdata/internal/spikes"
)

// runSpike implements `marketdata spike <id> [flags]` (verify-early spikes, docs/verify-early.md).
// It returns true when a spike subcommand was handled.
func runSpike(args []string) bool {
	if len(args) < 2 || args[0] != "spike" {
		return false
	}
	switch args[1] {
	case "v4":
		fs := flag.NewFlagSet("spike v4", flag.ExitOnError)
		role := fs.String("role", "writer", "writer | reader")
		dir := fs.String("dir", "/data/lake/spikes/v4", "directory on the lake mount")
		dur := fs.Duration("duration", 2*time.Minute, "run time")
		_ = fs.Parse(args[2:])
		var res spikes.V4Result
		var err error
		if *role == "reader" {
			res, err = spikes.V4Reader(context.Background(), *dir, *dur)
		} else {
			res, err = spikes.V4Writer(context.Background(), *dir, *dur)
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, "spike v4:", err)
			os.Exit(1)
		}
		fmt.Println(res.JSON())
		if res.Partial > 0 {
			os.Exit(2)
		}
	default:
		fmt.Fprintf(os.Stderr, "unknown spike %q\n", args[1])
		os.Exit(1)
	}
	return true
}
