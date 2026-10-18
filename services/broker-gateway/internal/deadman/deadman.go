// Package deadman cancels working platform orders when the execution service's heartbeat stops
// (RM-017, section 07 dead-man check). It runs inside broker-gateway so it keeps working when
// execution is down. Protective orders are kept: bracket/OCO legs and stop-type orders only reduce
// risk, and removing them would leave positions naked (N5). Manual orders are never touched.
package deadman

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
)

// Account is the subset of adapter.Account the dead-man needs.
type Account interface {
	Info() adapter.Info
	Broker() adapter.Broker
	Cancel(ctx context.Context, ref adapter.OrderRef) (*brokerv1.CancelOrderResponse, error)
}

// Watch tracks heartbeats and acts on loss.
type Watch struct {
	Accounts []Account
	Timeout  time.Duration
	Calendar *calendar.Calendar
	Now      func() time.Time
	Alert    func(ctx context.Context, a *alertsv1.Alert) error
	Log      *slog.Logger

	mu    sync.Mutex
	last  time.Time
	fired bool
}

// Beat records an execution heartbeat.
func (w *Watch) Beat() {
	w.mu.Lock()
	w.last, w.fired = w.Now(), false
	w.mu.Unlock()
}

// Protective reports whether a broker order is a protective order the dead-man keeps.
func Protective(o *brokerv1.BrokerOrder) bool {
	if o.GetParentBrokerOrderId() != "" {
		return true
	}
	switch o.GetType() {
	case ordersv1.OrderType_ORDER_TYPE_STOP, ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT, ordersv1.OrderType_ORDER_TYPE_TRAILING_STOP:
		return true
	}
	return false
}

// Check fires once per heartbeat loss during the regular session; it returns the number of orders
// canceled. Before the first heartbeat nothing happens (execution never started trading).
func (w *Watch) Check(ctx context.Context) int {
	w.mu.Lock()
	now := w.Now()
	lost := !w.last.IsZero() && now.Sub(w.last) > w.Timeout && !w.fired && w.Calendar.IsOpen(now)
	if lost {
		w.fired = true
	}
	age := now.Sub(w.last)
	w.mu.Unlock()
	if !lost {
		return 0
	}
	n := 0
	var accts []string
	for _, a := range w.Accounts {
		open, err := a.Broker().ListOpenOrders(ctx)
		if err != nil {
			w.Log.Warn("dead-man: list open orders failed", "account", a.Info().AccountID, "err", err)
			continue
		}
		for _, o := range open {
			// Filled bracket parents are listed while their (protective) legs are open.
			if !attribution.IsPlatform(o.GetClientOrderId()) || Protective(o) || adapter.IsFinal(o.GetState()) {
				continue
			}
			if id, err := attribution.Parse(o.GetClientOrderId(), 0); err == nil && id.Manual() {
				continue
			}
			if _, err := a.Cancel(ctx, adapter.OrderRef{BrokerOrderID: o.GetBrokerOrderId()}); err == nil {
				n++
			}
		}
		accts = append(accts, a.Info().AccountID)
	}
	if w.Alert != nil {
		_ = w.Alert(ctx, &alertsv1.Alert{AlertId: fmt.Sprintf("deadman-%d", now.UnixNano()), Severity: alertsv1.Severity_SEVERITY_CRITICAL, Source: "broker-gateway",
			Subject: "execution heartbeat lost", Message: fmt.Sprintf("no execution heartbeat for %s; canceled %d working platform orders in %s (protective orders kept)",
				age.Round(time.Second), n, strings.Join(accts, ",")), DedupeKey: "deadman-execution", RunbookUrl: "docs/runbooks/kill-switch.md", OccurredAt: now.UnixNano()})
	}
	return n
}

// Run checks every second until ctx ends.
func (w *Watch) Run(ctx context.Context) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			w.Check(ctx)
		}
	}
}
