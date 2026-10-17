// Command marketdata will own historical backfill, live streams, bar building, gap repair, and lake
// and cache writes (section 05, Phase 1). In Phase 0 it runs the standard service template only:
// ops endpoints and a NATS connection, so the stack, dashboards, and alerts cover it from day one.
package main

import (
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/svc"
)

type cfg struct {
	config.Common
	Bus bus.Config
	// LakeDir is the Parquet lake root (D4: host APFS, bind-mounted); unused until Phase 1.
	LakeDir string `env:"TB_LAKE_DIR" envDefault:"/data/lake" json:"lake_dir"`
}

func main() {
	var c cfg
	rt, err := svc.Start(&c, &c.Common)
	if err != nil {
		svc.Fatal(nil, "startup", err)
	}
	defer rt.Close()
	if c.Bus.User == "" {
		c.Bus.User = "marketdata"
	}
	nc, err := bus.Connect(c.Bus, rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "bus", err)
	}
	defer nc.Close()
	rt.Ops.AddCheck("bus", bus.Ready(nc))
	rt.ServeOps()
	rt.Log.Info("marketdata placeholder running; ingestion arrives in Phase 1", "lake_dir", c.LakeDir)
	rt.Wait()
}
