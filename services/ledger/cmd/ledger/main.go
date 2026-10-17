// Command ledger will own real-account snapshots, virtual strategy ledgers, the manual bucket, lots,
// and reconciliation (section 06, Phase 2). In Phase 0 it runs the service template only.
package main

import (
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/svc"
)

type cfg struct {
	config.Common
	Bus bus.Config
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
	nc, err := bus.Connect(c.Bus, rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "bus", err)
	}
	defer nc.Close()
	rt.Ops.AddCheck("bus", bus.Ready(nc))
	rt.ServeOps()
	rt.Log.Info("ledger placeholder running; ledgers arrive in Phase 2")
	rt.Wait()
}
