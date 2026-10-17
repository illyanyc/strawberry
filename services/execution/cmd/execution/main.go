// Command execution will host the OMS state machine, pre-trade risk, routing and margin guards, and
// kill switches (section 07, Phase 2). In Phase 0 it runs the service template plus the clock-skew
// check it will consult before opening risk (OP-020). It accepts no intents yet: fail closed.
package main

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/clock"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/svc"
)

type cfg struct {
	config.Common
	Bus       bus.Config
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
	if c.Bus.User == "" {
		c.Bus.User = "execution"
	}
	nc, err := bus.Connect(c.Bus, rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "bus", err)
	}
	defer nc.Close()
	rt.Ops.AddCheck("bus", bus.Ready(nc))

	tradingEnabled := prometheus.NewGauge(prometheus.GaugeOpts{Name: "tb_trading_enabled", Help: "1 when execution accepts new intents."})
	rt.Ops.Registry().MustRegister(tradingEnabled)
	tradingEnabled.Set(0)
	if c.NTPServer != "" {
		ck := clock.NewChecker(c.NTPServer, time.Minute, c.MaxSkew, rt.Ops.Registry(), rt.Log)
		go ck.Run(rt.Ctx)
	}
	rt.ServeOps()
	rt.Log.Info("execution placeholder running; trading disabled until Phase 2")
	rt.Wait()
}
