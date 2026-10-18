package adapter

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/attribution"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/session"
)

// Options tune an Account.
type Options struct {
	// Backoff between reconnect attempts (BR-012).
	Backoff resilience.Backoff
	// LookupAttempts and LookupBackoff bound the lookup by client_order_id after an ambiguous submit
	// (BR-007).
	LookupAttempts int
	LookupBackoff  resilience.Backoff
	// SnapshotInterval between account snapshots.
	SnapshotInterval time.Duration
	// ResyncLookback is how far back the first resync looks when no execution is stored yet.
	ResyncLookback time.Duration
	// ResyncOverlap is subtracted from the stored cursor on resync (BR-011).
	ResyncOverlap time.Duration
	Now           func() time.Time
}

// DefaultOptions are production defaults.
func DefaultOptions() Options {
	return Options{
		Backoff:          resilience.DefaultBackoff,
		LookupAttempts:   5,
		LookupBackoff:    resilience.Backoff{Base: 200 * time.Millisecond, Cap: 2 * time.Second},
		SnapshotInterval: 15 * time.Second,
		ResyncLookback:   24 * time.Hour,
		ResyncOverlap:    5 * time.Minute,
		Now:              time.Now,
	}
}

// Metrics shared by all accounts in a process.
type Metrics struct {
	State      *prometheus.GaugeVec
	Orders     *prometheus.CounterVec
	SubmitLat  *prometheus.HistogramVec
	UpdateLat  *prometheus.HistogramVec
	Execs      *prometheus.CounterVec
	DupExecs   *prometheus.CounterVec
	Reconnects *prometheus.CounterVec
}

// NewMetrics registers broker metrics on reg (nil: unregistered, for tests).
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		State:      prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "tb_broker_session_state", Help: "Broker session state (SessionState enum value) per account."}, []string{"account"}),
		Orders:     prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_broker_order_ops_total", Help: "Order operations by outcome."}, []string{"account", "op", "outcome"}),
		SubmitLat:  prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "tb_broker_submit_seconds", Help: "Gateway receive to broker response for submits.", Buckets: []float64{.005, .01, .02, .05, .1, .25, .5, 1, 2.5}}, []string{"account"}),
		UpdateLat:  prometheus.NewHistogramVec(prometheus.HistogramOpts{Name: "tb_broker_update_propagation_seconds", Help: "Broker event time to durable store + outbox.", Buckets: []float64{.005, .01, .02, .05, .1, .25, .5, 1, 5}}, []string{"account"}),
		Execs:      prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_broker_executions_total", Help: "New executions recorded."}, []string{"account"}),
		DupExecs:   prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_broker_duplicate_executions_total", Help: "Duplicate execution messages dropped (BR-008)."}, []string{"account"}),
		Reconnects: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_broker_reconnects_total", Help: "Broker session reconnect attempts."}, []string{"account"}),
	}
	if reg != nil {
		reg.MustRegister(m.State, m.Orders, m.SubmitLat, m.UpdateLat, m.Execs, m.DupExecs, m.Reconnects)
	}
	return m
}

// Account runs one broker account: its session loop, resync, snapshots, and order operations.
type Account struct {
	b       Broker
	info    Info
	sess    *session.Machine
	store   Store
	aud     Auditor
	gate    LiveGate
	log     *slog.Logger
	opt     Options
	metrics *Metrics
	fan     *Fanout

	mu         sync.Mutex
	orders     map[string]*brokerv1.BrokerOrder // latest merged view by broker_order_id (BR-009)
	lastSnap   *ledgerv1.AccountSnapshot
	marginBad  bool
	snapKick   chan struct{}
	resyncKick chan struct{}
}

// NewAccount wires an account. gate may be nil for paper accounts.
func NewAccount(b Broker, store Store, aud Auditor, gate LiveGate, log *slog.Logger, m *Metrics, opt Options) *Account {
	if opt.Now == nil {
		opt.Now = time.Now
	}
	if m == nil {
		m = NewMetrics(nil)
	}
	info := b.Info()
	a := &Account{
		b: b, info: info, store: store, aud: aud, gate: gate, opt: opt, metrics: m,
		log:        log.With("account", info.AccountID),
		sess:       session.New(info.AccountID, opt.Now),
		fan:        NewFanout(),
		orders:     map[string]*brokerv1.BrokerOrder{},
		snapKick:   make(chan struct{}, 1),
		resyncKick: make(chan struct{}, 1),
	}
	a.sess.OnChange(func(from, to session.State, reasons []string) {
		m.State.WithLabelValues(info.AccountID).Set(float64(to))
		a.log.Info("broker session state", "from", from.String(), "to", to.String(), "reasons", reasons)
	})
	m.State.WithLabelValues(info.AccountID).Set(float64(a.sess.State()))
	return a
}

// Info returns the account description.
func (a *Account) Info() Info { return a.info }

// Session returns the session machine.
func (a *Account) Session() *session.Machine { return a.sess }

// Fanout returns the in-process subscriber hub for the gRPC streams.
func (a *Account) Fanout() *Fanout { return a.fan }

// Broker returns the underlying broker (read-only operations by the gRPC layer).
func (a *Account) Broker() Broker { return a.b }

// LastSnapshot returns the most recent snapshot, if any.
func (a *Account) LastSnapshot() *ledgerv1.AccountSnapshot {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.lastSnap
}

func (a *Account) isLive() bool { return a.info.Environment == brokerv1.Environment_ENVIRONMENT_LIVE }

// Run drives the session until ctx ends (BR-010..BR-013, BR-021).
func (a *Account) Run(ctx context.Context) {
	attempt := 0
	wait := func(d time.Duration) bool { return resilience.Sleep(ctx, d) == nil }
	for ctx.Err() == nil {
		if a.isLive() {
			ok, why := false, "live gate not configured"
			if a.gate != nil {
				ok, why = a.gate.Allowed(ctx, a.info.AccountID)
			}
			if !ok {
				// BR-013 / OE-010: the live adapter stays stopped; re-check periodically.
				_ = a.sess.To(session.Stopped, why)
				if !wait(time.Minute) {
					return
				}
				continue
			}
			if a.sess.State() == session.Stopped {
				_ = a.sess.To(session.Disconnected, "live gate opened")
			}
		}
		if a.sess.State() != session.Degraded {
			_ = a.sess.To(session.Disconnected, a.sess.Status().GetDegradedReasons()...)
			_ = a.sess.To(session.Connecting)
		}
		a.metrics.Reconnects.WithLabelValues(a.info.AccountID).Inc()
		if err := a.b.Connect(ctx); err != nil {
			if ctx.Err() != nil {
				return
			}
			_ = a.sess.To(session.Disconnected, safeReason(err))
			d := a.opt.Backoff.Delay(attempt)
			if errors.Is(err, ErrAuth) || errors.Is(err, ErrNoCredential) {
				d = a.opt.Backoff.Cap // BR-012: no fast retry on auth failures
			}
			attempt++
			if !wait(d) {
				return
			}
			continue
		}
		if a.sess.State() == session.Connecting {
			_ = a.sess.To(session.Authenticated)
		}
		err := a.runConnected(ctx)
		if ctx.Err() != nil {
			return
		}
		reason := "trade stream lost"
		if err != nil {
			reason = "trade stream lost: " + safeReason(err)
		}
		// Stream gone: fail closed for new orders, keep cancels (REST) available (BR-010).
		if a.sess.State() == session.Ready {
			_ = a.sess.To(session.Degraded, reason)
		} else {
			_ = a.sess.To(session.Disconnected, reason)
		}
		_ = a.store.Alert(ctx, a.alert(alertsv1.Severity_SEVERITY_WARNING, "broker.session.degraded", reason))
		attempt++
		if !wait(a.opt.Backoff.Delay(attempt)) {
			return
		}
	}
}

// runConnected starts the stream, resyncs, serves until the stream ends.
func (a *Account) runConnected(ctx context.Context) error {
	sctx, cancel := context.WithCancel(ctx)
	defer cancel()
	subscribed := make(chan struct{})
	var once sync.Once
	errCh := make(chan error, 1)
	go func() {
		errCh <- a.b.Stream(sctx, func() { once.Do(func() { close(subscribed) }) }, func(u *brokerv1.BrokerOrderUpdate) {
			a.handleUpdate(sctx, u)
		})
	}()
	select {
	case <-subscribed:
	case err := <-errCh:
		return err
	case <-ctx.Done():
		return ctx.Err()
	}
	if err := a.syncAndReady(sctx, "reconnect resync"); err != nil {
		cancel()
		<-errCh //nolint:errcheck // stream error is superseded by the resync error
		return err
	}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		a.snapshotLoop(sctx)
	}()
	err := <-errCh
	cancel()
	wg.Wait()
	return err
}

// syncAndReady performs the resync (BR-011) and moves the session to Ready unless a margin event is
// active (BR-021).
func (a *Account) syncAndReady(ctx context.Context, reason string) error {
	_ = a.sess.To(session.Syncing)
	if err := a.Resync(ctx, reason); err != nil {
		return err
	}
	snap, err := a.b.Snapshot(ctx)
	if err != nil {
		return fmt.Errorf("snapshot after resync: %w", err)
	}
	a.recordSnapshot(ctx, snap)
	if a.marginEvent() {
		return a.sess.To(session.Degraded, a.marginReason())
	}
	return a.sess.To(session.Ready)
}

// Resync lists open orders, recent orders, and executions since the stored cursor minus the overlap,
// records anything missed (deduplicated, BR-008), and requests reconciliation (BR-011, OE-008).
func (a *Account) Resync(ctx context.Context, reason string) error {
	_, curTime, err := a.store.Cursor(ctx, a.info.AccountID)
	if err != nil {
		return fmt.Errorf("resync cursor: %w", err)
	}
	since := a.opt.Now().Add(-a.opt.ResyncLookback)
	if curTime > 0 {
		since = time.Unix(0, curTime).Add(-a.opt.ResyncOverlap)
	}
	open, err := a.b.ListOpenOrders(ctx)
	if err != nil {
		return fmt.Errorf("resync open orders: %w", err)
	}
	recent, err := a.b.ListOrders(ctx, since)
	if err != nil {
		return fmt.Errorf("resync orders: %w", err)
	}
	execs, err := a.b.ListExecutions(ctx, since)
	if err != nil {
		return fmt.Errorf("resync executions: %w", err)
	}
	byID := map[string]*brokerv1.BrokerOrder{}
	for _, o := range append(recent, open...) {
		byID[o.GetBrokerOrderId()] = o
	}
	now := a.opt.Now().UnixNano()
	for _, e := range execs {
		o := byID[e.GetBrokerOrderId()]
		if o == nil {
			o = &brokerv1.BrokerOrder{BrokerAccountId: a.info.AccountID, BrokerOrderId: e.GetBrokerOrderId(), ClientOrderId: e.GetClientOrderId()}
		}
		a.handleUpdate(ctx, &brokerv1.BrokerOrderUpdate{BrokerAccountId: a.info.AccountID, Event: "fill", Order: o, Execution: e,
			EventTime: e.GetEventTime(), ReceivedAt: now, FromResync: true})
	}
	for _, o := range byID {
		a.handleUpdate(ctx, &brokerv1.BrokerOrderUpdate{BrokerAccountId: a.info.AccountID, Event: "resync", Order: o,
			EventTime: o.GetUpdatedAt(), ReceivedAt: now, FromResync: true})
	}
	req := &ledgerv1.ReconciliationRequest{BrokerAccountId: a.info.AccountID, Reason: reason, RequestedAt: now, CorrelationId: newID()}
	if err := a.store.RequestReconciliation(ctx, req); err != nil {
		return fmt.Errorf("resync reconciliation request: %w", err)
	}
	a.log.Info("resync complete", "open_orders", len(open), "orders", len(recent), "executions", len(execs), "since", since.UTC())
	return nil
}

// merge applies an update to the order view so filled quantity and state never move backwards
// (BR-009) and returns the merged order.
func (a *Account) merge(o *brokerv1.BrokerOrder) *brokerv1.BrokerOrder {
	if o == nil || o.GetBrokerOrderId() == "" {
		return o
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	prev := a.orders[o.GetBrokerOrderId()]
	cur := proto.Clone(o).(*brokerv1.BrokerOrder)
	if prev != nil {
		pf, _, _ := Dec(prev.GetFilledQty())
		cf, _, _ := Dec(cur.GetFilledQty())
		if pf.GreaterThan(cf) {
			cur.FilledQty = prev.GetFilledQty()
			cur.AvgFillPrice = prev.GetAvgFillPrice()
		}
		if stateRank(prev.GetState()) > stateRank(cur.GetState()) {
			cur.State = prev.GetState()
			cur.BrokerStatus = prev.GetBrokerStatus()
		}
		if cur.GetClientOrderId() == "" {
			cur.ClientOrderId = prev.GetClientOrderId()
		}
		if cur.GetInstrument() == nil {
			cur.Instrument = prev.GetInstrument()
		}
	}
	a.orders[o.GetBrokerOrderId()] = cur
	return cur
}

// handleUpdate records an update durably (store + outbox) and fans it out to stream subscribers.
func (a *Account) handleUpdate(ctx context.Context, u *brokerv1.BrokerOrderUpdate) {
	a.sess.Heartbeat()
	u = proto.Clone(u).(*brokerv1.BrokerOrderUpdate)
	u.BrokerAccountId = a.info.AccountID
	if u.GetReceivedAt() == 0 {
		u.ReceivedAt = a.opt.Now().UnixNano()
	}
	if u.Order != nil && u.Order.GetBrokerAccountId() == "" {
		u.Order.BrokerAccountId = a.info.AccountID
	}
	u.Order = a.merge(u.GetOrder())
	if e := u.GetExecution(); e != nil {
		e.BrokerAccountId = a.info.AccountID
		e.Broker = a.info.Broker
		e.Mode = a.info.Mode()
		if e.GetIngestedAt() == 0 {
			e.IngestedAt = a.opt.Now().UnixNano()
		}
		if e.GetClientOrderId() == "" {
			e.ClientOrderId = u.GetOrder().GetClientOrderId()
		}
		if e.GetInstrumentId() == "" {
			e.InstrumentId = u.GetOrder().GetInstrument().GetInstrumentId()
		}
		isNew, err := a.store.RecordExecution(ctx, a.info.AccountID, e, u)
		if err != nil {
			a.log.Error("record execution failed; forcing resync", "exec_id", e.GetBrokerExecId(), "err", err)
			a.kickResync()
			return
		}
		if !isNew {
			a.metrics.DupExecs.WithLabelValues(a.info.AccountID).Inc()
		} else {
			a.metrics.Execs.WithLabelValues(a.info.AccountID).Inc()
			if e.GetEventTime() > 0 {
				a.metrics.UpdateLat.WithLabelValues(a.info.AccountID).Observe(time.Duration(a.opt.Now().UnixNano() - e.GetEventTime()).Seconds())
			}
			a.fan.PublishExecution(e)
			a.kickSnapshot()
		}
	}
	if u.GetOrder() == nil {
		return
	}
	isNew, err := a.store.RecordOrderUpdate(ctx, a.info.AccountID, u)
	if err != nil {
		a.log.Error("record order update failed; forcing resync", "order", u.GetOrder().GetBrokerOrderId(), "err", err)
		a.kickResync()
		return
	}
	if isNew {
		if u.GetOrder().GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN {
			// BR-006 / OE-005: unmapped broker status → alert, fail closed downstream.
			_ = a.store.Alert(ctx, a.alert(alertsv1.Severity_SEVERITY_CRITICAL, "broker.unmapped_status",
				fmt.Sprintf("order %s has unmapped broker status %q", u.GetOrder().GetBrokerOrderId(), u.GetOrder().GetBrokerStatus())))
		}
		a.fan.PublishOrder(u)
	}
}

func (a *Account) kickSnapshot() {
	select {
	case a.snapKick <- struct{}{}:
	default:
	}
}

func (a *Account) kickResync() {
	select {
	case a.resyncKick <- struct{}{}:
	default:
	}
}

func (a *Account) snapshotLoop(ctx context.Context) {
	t := time.NewTicker(a.opt.SnapshotInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		case <-a.snapKick:
		case <-a.resyncKick:
			if err := a.Resync(ctx, "store error recovery"); err != nil {
				a.log.Warn("recovery resync failed", "err", err)
			}
			continue
		}
		snap, err := a.b.Snapshot(ctx)
		if err != nil {
			if ctx.Err() == nil {
				a.log.Warn("snapshot failed", "err", safeReason(err))
			}
			continue
		}
		wasBad := a.marginEvent()
		a.recordSnapshot(ctx, snap)
		switch bad := a.marginEvent(); {
		case bad && a.sess.State() == session.Ready:
			// BR-021 / RM-013: margin call or IMD halts the account.
			_ = a.sess.To(session.Degraded, a.marginReason())
			_ = a.store.Alert(ctx, a.alert(alertsv1.Severity_SEVERITY_CRITICAL, "broker.margin_event", a.marginReason()))
		case !bad && wasBad && a.sess.State() == session.Degraded:
			if err := a.syncAndReady(ctx, "margin event cleared"); err != nil {
				a.log.Warn("resync after margin event failed", "err", err)
				_ = a.sess.To(session.Degraded, "resync failed")
			}
		}
	}
}

func (a *Account) recordSnapshot(ctx context.Context, s *ledgerv1.AccountSnapshot) {
	s.BrokerAccountId = a.info.AccountID
	s.Broker = a.info.Broker
	s.Mode = a.info.Mode()
	if s.GetAsOf() == 0 {
		s.AsOf = a.opt.Now().UnixNano()
	}
	a.mu.Lock()
	a.lastSnap = s
	a.marginBad = s.GetIntradayMarginDeficit() || s.GetMarginCall()
	a.mu.Unlock()
	if err := a.store.RecordSnapshot(ctx, a.info.AccountID, s); err != nil {
		a.log.Warn("record snapshot failed", "err", err)
	}
	a.fan.PublishSnapshot(s)
}

func (a *Account) marginEvent() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.marginBad
}

func (a *Account) marginReason() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	var parts []string
	if a.lastSnap.GetMarginCall() {
		parts = append(parts, "margin call")
	}
	if a.lastSnap.GetIntradayMarginDeficit() {
		parts = append(parts, "intraday margin deficit")
	}
	return "broker reports " + strings.Join(parts, " and ")
}

func (a *Account) alert(sev alertsv1.Severity, subject, msg string) *alertsv1.Alert {
	return &alertsv1.Alert{
		AlertId: newID(), Severity: sev, Source: "broker-gateway", Subject: subject,
		Message: logging.Redact(a.info.AccountID + ": " + msg), DedupeKey: subject + ":" + a.info.AccountID,
		RunbookUrl: "docs/runbooks/broker-outage.md", OccurredAt: a.opt.Now().UnixNano(),
	}
}

// ---------------------------------------------------------------- order operations

func (a *Account) auditEvent(ctx context.Context, action, target string, details map[string]any) error {
	if a.aud == nil {
		return nil
	}
	details["broker_account_id"] = a.info.AccountID
	return a.aud.Append(ctx, audit.Event{Actor: "broker-gateway", Action: action, Target: target, Details: details})
}

func (a *Account) count(op, outcome string) {
	a.metrics.Orders.WithLabelValues(a.info.AccountID, op, outcome).Inc()
}

// Submit places an order idempotently (BR-002, BR-003, BR-007, BR-014, BR-017, OE-003).
func (a *Account) Submit(ctx context.Context, req *brokerv1.BrokerOrderRequest, fractionable bool, instrumentTick decimal.Decimal) (*brokerv1.SubmitOrderResponse, error) {
	start := a.opt.Now()
	if !a.sess.CanSubmit() {
		a.count("submit", "unavailable")
		return nil, &UnavailableError{State: a.sess.State()}
	}
	coid := req.GetClientOrderId()
	if rej := Validate(req, a.info.Capabilities, fractionable); rej != nil {
		a.count("submit", "rejected_local")
		_ = a.auditEvent(ctx, "broker.order.reject", coid, map[string]any{"client_order_id": coid, "code": rej.Reason.GetCode().String(), "local": true, "message": rej.Reason.GetMessage()})
		return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED, Reject: rej.Reason}, nil
	}
	req = RoundPrices(req, instrumentTick)
	// OE-011 / SE-021: the request is audited before it is sent; no audit, no order.
	if err := a.auditEvent(ctx, "broker.order.submit", coid, map[string]any{
		"client_order_id": coid, "symbol": req.GetInstrument().GetSymbol(), "instrument_id": req.GetInstrument().GetInstrumentId(),
		"side": req.GetSide().String(), "qty": req.GetQty().GetValue(), "type": req.GetType().String(), "tif": req.GetTif().String(),
		"limit": req.GetLimitPrice().GetValue(), "stop": req.GetStopPrice().GetValue(), "intent_id": req.GetIntentId(),
	}); err != nil {
		a.count("submit", "audit_failed")
		return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED,
			Reject: &brokerv1.RejectReason{Code: brokerv1.RejectCode_REJECT_CODE_BROKER_OTHER, Message: "audit unavailable; order not sent", Local: true}}, nil
	}
	o, err := a.b.Submit(ctx, req)
	a.metrics.SubmitLat.WithLabelValues(a.info.AccountID).Observe(a.opt.Now().Sub(start).Seconds())
	var rej *RejectError
	switch {
	case err == nil:
		o = a.merge(o)
		a.count("submit", "accepted")
		a.auditOutcome(ctx, "broker.order.submit.result", coid, "accepted", o.GetBrokerOrderId(), false)
		return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED, Order: o, AcceptedAt: a.opt.Now().UnixNano()}, nil
	case errors.As(err, &rej):
		if rej.Reason.GetCode() == brokerv1.RejectCode_REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID {
			// Idempotent replay: the same client_order_id with the same order is the same order.
			if prev, lerr := a.b.GetOrder(ctx, OrderRef{ClientOrderID: coid}); lerr == nil && sameOrder(prev, req) {
				a.count("submit", "replayed")
				return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED, Order: a.merge(prev), RecoveredByLookup: true}, nil
			}
		}
		a.count("submit", "rejected")
		_ = a.auditEvent(ctx, "broker.order.reject", coid, map[string]any{"client_order_id": coid, "code": rej.Reason.GetCode().String(),
			"broker_code": rej.Reason.GetBrokerCode(), "local": rej.Reason.GetLocal(), "message": rej.Reason.GetMessage()})
		return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED, Reject: rej.Reason}, nil
	case errors.Is(err, ErrNotSent):
		a.count("submit", "not_sent")
		reason := &brokerv1.RejectReason{Code: brokerv1.RejectCode_REJECT_CODE_BROKER_OTHER, Message: "request not sent: " + safeReason(err), Local: true}
		_ = a.auditEvent(ctx, "broker.order.reject", coid, map[string]any{"client_order_id": coid, "code": reason.GetCode().String(), "local": true, "message": reason.GetMessage()})
		return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED, Reject: reason}, nil
	}
	// Ambiguous: the order may or may not exist. Look it up; never resubmit (BR-007, OE-003).
	a.log.Warn("ambiguous submit; looking up by client_order_id", "client_order_id", coid, "err", safeReason(err))
	for i := range a.opt.LookupAttempts {
		lctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		found, lerr := a.b.GetOrder(lctx, OrderRef{ClientOrderID: coid})
		cancel()
		if lerr == nil {
			found = a.merge(found)
			a.count("submit", "recovered")
			a.auditOutcome(ctx, "broker.order.submit.result", coid, "accepted_after_lookup", found.GetBrokerOrderId(), true)
			return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED, Order: found, RecoveredByLookup: true, AcceptedAt: a.opt.Now().UnixNano()}, nil
		}
		if i < a.opt.LookupAttempts-1 {
			_ = resilience.Sleep(context.WithoutCancel(ctx), a.opt.LookupBackoff.Ceiling(i))
		}
	}
	a.count("submit", "unknown")
	a.auditOutcome(ctx, "broker.order.submit.result", coid, "unknown", "", false)
	_ = a.store.Alert(ctx, a.alert(alertsv1.Severity_SEVERITY_CRITICAL, "broker.order.unknown", "submit outcome unknown for "+coid))
	return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_UNKNOWN}, nil
}

func (a *Account) auditOutcome(ctx context.Context, action, coid, outcome, brokerOrderID string, lookup bool) {
	if err := a.auditEvent(ctx, action, coid, map[string]any{"client_order_id": coid, "outcome": outcome, "broker_order_id": brokerOrderID, "recovered_by_lookup": lookup}); err != nil {
		a.log.Error("audit of order outcome failed", "client_order_id", coid, "err", err)
	}
}

func sameOrder(o *brokerv1.BrokerOrder, req *brokerv1.BrokerOrderRequest) bool {
	oq, _, _ := Dec(o.GetQty())
	rq, _, _ := Dec(req.GetQty())
	return o.GetSide() == req.GetSide() && oq.Equal(rq) && o.GetType() == req.GetType() &&
		strings.EqualFold(o.GetInstrument().GetSymbol(), req.GetInstrument().GetSymbol())
}

func (a *Account) resolve(ctx context.Context, ref OrderRef) (*brokerv1.BrokerOrder, error) {
	o, err := a.b.GetOrder(ctx, ref)
	if err != nil {
		return nil, err
	}
	return a.merge(o), nil
}

// Cancel cancels one order (BR-010: allowed when Ready or Degraded; BR-014 audited).
func (a *Account) Cancel(ctx context.Context, ref OrderRef) (*brokerv1.CancelOrderResponse, error) {
	if !a.sess.CanCancel() {
		a.count("cancel", "unavailable")
		return nil, &UnavailableError{State: a.sess.State()}
	}
	target := ref.ClientOrderID + ref.BrokerOrderID
	o, err := a.resolve(ctx, ref)
	if errors.Is(err, ErrNotFound) {
		a.count("cancel", "not_found")
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_NOT_FOUND}, nil
	}
	if err != nil {
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_UNKNOWN}, nil
	}
	if IsFinal(o.GetState()) {
		a.count("cancel", "already_final")
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_ALREADY_FINAL, Order: o}, nil
	}
	if err := a.auditEvent(ctx, "broker.order.cancel", target, map[string]any{"client_order_id": o.GetClientOrderId(), "broker_order_id": o.GetBrokerOrderId()}); err != nil {
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_REJECTED,
			Reject: &brokerv1.RejectReason{Code: brokerv1.RejectCode_REJECT_CODE_BROKER_OTHER, Message: "audit unavailable; cancel not sent", Local: true}}, nil
	}
	cerr := a.b.Cancel(ctx, OrderRef{BrokerOrderID: o.GetBrokerOrderId(), ClientOrderID: o.GetClientOrderId()})
	var rej *RejectError
	if errors.As(cerr, &rej) {
		// The order may have become final between lookup and cancel.
		if cur, err := a.resolve(ctx, OrderRef{BrokerOrderID: o.GetBrokerOrderId()}); err == nil && IsFinal(cur.GetState()) {
			a.count("cancel", "already_final")
			return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_ALREADY_FINAL, Order: cur}, nil
		}
		a.count("cancel", "rejected")
		_ = a.auditEvent(ctx, "broker.order.reject", target, map[string]any{"op": "cancel", "code": rej.Reason.GetCode().String(), "broker_code": rej.Reason.GetBrokerCode()})
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_REJECTED, Order: o, Reject: rej.Reason}, nil
	}
	cur, err := a.resolve(ctx, OrderRef{BrokerOrderID: o.GetBrokerOrderId()})
	if cerr != nil && err != nil {
		a.count("cancel", "unknown")
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_UNKNOWN, Order: o}, nil
	}
	if cur == nil {
		cur = o
	}
	a.count("cancel", "requested")
	return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_REQUESTED, Order: cur}, nil
}

// Replace replaces one order (BR-003 attempt rule, BR-007 lookup on ambiguity, BR-014 audit).
func (a *Account) Replace(ctx context.Context, req *brokerv1.ReplaceOrderRequest, instrumentTick decimal.Decimal) (*brokerv1.ReplaceOrderResponse, error) {
	if !a.sess.CanSubmit() {
		a.count("replace", "unavailable")
		return nil, &UnavailableError{State: a.sess.State()}
	}
	maxLen := int(a.info.Capabilities.GetMaxClientOrderIdLen())
	cur, err1 := attribution.Parse(req.GetClientOrderId(), maxLen)
	next, err2 := attribution.Parse(req.GetNewClientOrderId(), maxLen)
	if err := errors.Join(err1, err2); err != nil || attribution.ValidReplacement(cur, next) != nil {
		msg := "invalid replacement attribution"
		if err != nil {
			msg = err.Error()
		}
		a.count("replace", "rejected_local")
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_REJECTED,
			Reject: &brokerv1.RejectReason{Code: brokerv1.RejectCode_REJECT_CODE_INVALID_ATTRIBUTION, Message: msg, Local: true}}, nil
	}
	if !a.info.Capabilities.GetSupportsReplace() {
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_REJECTED,
			Reject: &brokerv1.RejectReason{Code: brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER, Message: "replace not supported by account", Local: true}}, nil
	}
	o, err := a.resolve(ctx, OrderRef{ClientOrderID: req.GetClientOrderId()})
	if errors.Is(err, ErrNotFound) {
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_NOT_FOUND}, nil
	}
	if err != nil {
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_UNKNOWN}, nil
	}
	// Round changed prices with the original order's side (BR-017).
	rounded := RoundPrices(&brokerv1.BrokerOrderRequest{Side: o.GetSide(), LimitPrice: req.GetLimitPrice(), StopPrice: req.GetStopPrice()}, instrumentTick)
	req = proto.Clone(req).(*brokerv1.ReplaceOrderRequest)
	req.LimitPrice, req.StopPrice = rounded.GetLimitPrice(), rounded.GetStopPrice()
	if err := a.auditEvent(ctx, "broker.order.replace", req.GetClientOrderId(), map[string]any{
		"client_order_id": req.GetClientOrderId(), "new_client_order_id": req.GetNewClientOrderId(), "broker_order_id": o.GetBrokerOrderId(),
		"qty": req.GetQty().GetValue(), "limit": req.GetLimitPrice().GetValue(), "stop": req.GetStopPrice().GetValue(),
	}); err != nil {
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_REJECTED,
			Reject: &brokerv1.RejectReason{Code: brokerv1.RejectCode_REJECT_CODE_BROKER_OTHER, Message: "audit unavailable; replace not sent", Local: true}}, nil
	}
	n, err := a.b.Replace(ctx, o.GetBrokerOrderId(), req)
	var rej *RejectError
	switch {
	case err == nil:
		a.count("replace", "accepted")
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_ACCEPTED, Order: a.merge(n)}, nil
	case errors.As(err, &rej):
		a.count("replace", "rejected")
		_ = a.auditEvent(ctx, "broker.order.reject", req.GetClientOrderId(), map[string]any{"op": "replace", "code": rej.Reason.GetCode().String(), "broker_code": rej.Reason.GetBrokerCode()})
		return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_REJECTED, Reject: rej.Reason}, nil
	}
	for i := range a.opt.LookupAttempts {
		found, lerr := a.b.GetOrder(context.WithoutCancel(ctx), OrderRef{ClientOrderID: req.GetNewClientOrderId()})
		if lerr == nil {
			a.count("replace", "recovered")
			return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_ACCEPTED, Order: a.merge(found)}, nil
		}
		_ = resilience.Sleep(context.WithoutCancel(ctx), a.opt.LookupBackoff.Ceiling(i))
	}
	a.count("replace", "unknown")
	return &brokerv1.ReplaceOrderResponse{Outcome: brokerv1.ReplaceOutcome_REPLACE_OUTCOME_UNKNOWN}, nil
}

// GetOrder looks up one order.
func (a *Account) GetOrder(ctx context.Context, ref OrderRef) (*brokerv1.BrokerOrder, error) {
	return a.resolve(ctx, ref)
}

func newID() string { return ulid.MustNew(ulid.Now(), rand.Reader).String() }

// safeReason renders an error for logs/status without credential material (SE-012).
func safeReason(err error) string {
	if err == nil {
		return ""
	}
	s := logging.Redact(err.Error())
	if len(s) > 300 {
		s = s[:300]
	}
	return s
}
