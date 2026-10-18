package app

import (
	"github.com/prometheus/client_golang/prometheus"

	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	"github.com/illyanyc/strawberry/services/ledger/internal/engine"
)

// collector exports reconciliation state per account (alert rules in
// infra/observability/prometheus/rules/trading.yml).
type collector struct{ e *engine.Engine }

var (
	descStatus = prometheus.NewDesc("tb_ledger_recon_status", "Latest reconciliation status per account (1=OK 2=BREAK 3=STALE 4=PENDING).", []string{"account"}, nil)
	descBreaks = prometheus.NewDesc("tb_ledger_open_breaks", "Open reconciliation breaks per account.", []string{"account"}, nil)
	descAge    = prometheus.NewDesc("tb_ledger_recon_as_of_seconds", "Unix time of the latest reconciliation run per account.", []string{"account"}, nil)
)

func (c collector) Describe(ch chan<- *prometheus.Desc) {
	ch <- descStatus
	ch <- descBreaks
	ch <- descAge
}

func (c collector) Collect(ch chan<- prometheus.Metric) {
	for acct, st := range c.e.Statuses() {
		ch <- prometheus.MustNewConstMetric(descStatus, prometheus.GaugeValue, float64(ledgerv1.ReconciliationStatus_value[st.Status]), acct)
		ch <- prometheus.MustNewConstMetric(descBreaks, prometheus.GaugeValue, float64(st.OpenBreaks), acct)
		ch <- prometheus.MustNewConstMetric(descAge, prometheus.GaugeValue, float64(st.AsOf)/1e9, acct)
	}
}
