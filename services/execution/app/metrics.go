package app

import (
	"context"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/illyanyc/strawberry/services/execution/internal/oms"
)

// collector exports OMS state (alert rules in infra/observability/prometheus/rules/trading.yml).
type collector struct{ e *oms.Engine }

var (
	descEnabled   = prometheus.NewDesc("tb_trading_enabled", "1 when execution accepts new intents on at least one account and is not globally frozen.", nil, nil)
	descAccepting = prometheus.NewDesc("tb_execution_account_accepting", "1 when the account accepts new intents.", []string{"account"}, nil)
	descOpen      = prometheus.NewDesc("tb_execution_open_orders", "Open platform orders (excluding protective legs).", nil, nil)
	descStuck     = prometheus.NewDesc("tb_execution_stuck_orders", "Non-protective orders open longer than 5 minutes.", nil, nil)
	descUnknown   = prometheus.NewDesc("tb_execution_unknown_orders", "Orders in state UNKNOWN (OE-005).", nil, nil)
	descKills     = prometheus.NewDesc("tb_execution_kill_switches_active", "Latched kill switches.", nil, nil)
	descHalts     = prometheus.NewDesc("tb_execution_halts_active", "Active strategy/instrument halts.", nil, nil)
	descDecisions = prometheus.NewDesc("tb_execution_decisions", "Risk decisions held in memory by outcome (approved or first failed check).", []string{"outcome"}, nil)
	descRuntime   = prometheus.NewDesc("tb_execution_runtime_heartbeat_age_seconds", "Age of the last strategy-runtime heartbeat.", nil, nil)
	descLoss      = prometheus.NewDesc("tb_execution_daily_loss_utilization", "Session loss as a fraction of the strategy's daily loss limit (RM-005).", []string{"strategy"}, nil)
	descDayTrades = prometheus.NewDesc("tb_execution_day_trades", "Rolling five-session day-trade count per account (RM-008 metric).", []string{"account"}, nil)
)

func (c collector) Describe(ch chan<- *prometheus.Desc) {
	for _, d := range []*prometheus.Desc{descEnabled, descAccepting, descOpen, descStuck, descUnknown, descKills, descHalts, descDecisions, descRuntime, descDayTrades, descLoss} {
		ch <- d
	}
}

func (c collector) Collect(ch chan<- prometheus.Metric) {
	st := c.e.Status()
	enabled := 0.0
	for _, a := range st.GetAccounts() {
		v := 0.0
		if a.GetAcceptingIntents() {
			v = 1
			if st.GetGlobalState() == "running" {
				enabled = 1
			}
		}
		ch <- prometheus.MustNewConstMetric(descAccepting, prometheus.GaugeValue, v, a.GetBrokerAccountId())
		ch <- prometheus.MustNewConstMetric(descDayTrades, prometheus.GaugeValue, float64(a.GetDayTradeCount()), a.GetBrokerAccountId())
	}
	ch <- prometheus.MustNewConstMetric(descEnabled, prometheus.GaugeValue, enabled)
	m := c.e.Metrics(5 * time.Minute)
	ch <- prometheus.MustNewConstMetric(descOpen, prometheus.GaugeValue, float64(m.Open))
	ch <- prometheus.MustNewConstMetric(descStuck, prometheus.GaugeValue, float64(m.Stuck))
	ch <- prometheus.MustNewConstMetric(descUnknown, prometheus.GaugeValue, float64(m.Unknown))
	ch <- prometheus.MustNewConstMetric(descKills, prometheus.GaugeValue, float64(m.Kills))
	ch <- prometheus.MustNewConstMetric(descHalts, prometheus.GaugeValue, float64(m.Halts))
	for k, v := range m.Decisions {
		ch <- prometheus.MustNewConstMetric(descDecisions, prometheus.GaugeValue, float64(v), k)
	}
	ch <- prometheus.MustNewConstMetric(descRuntime, prometheus.GaugeValue, m.RuntimeAge.Seconds())
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	for id, v := range c.e.DailyLossUtilization(ctx) {
		ch <- prometheus.MustNewConstMetric(descLoss, prometheus.GaugeValue, v, id)
	}
}
