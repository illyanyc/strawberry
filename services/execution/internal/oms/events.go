package oms

import (
	"context"
	"time"

	"github.com/shopspring/decimal"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
)

// findOrder maps a broker order to the OMS order: by broker order ID, else (top-level orders) by
// attribution ID.
func (e *Engine) findOrder(acct string, b *brokerv1.BrokerOrder) *ordersv1.Order {
	if id, ok := e.byBroker[acct+"|"+b.GetBrokerOrderId()]; ok {
		return e.orders[id]
	}
	if b.GetParentBrokerOrderId() == "" {
		for _, id := range e.byClient[acct+"|"+b.GetClientOrderId()] {
			o := e.orders[id]
			if o.GetBrokerOrderId() == "" || o.GetBrokerOrderId() == b.GetBrokerOrderId() {
				return o
			}
		}
	}
	return nil
}

// OnOrderUpdate applies a broker order event (ord.update.<acct>); idempotent: states only move
// forward and duplicate events change nothing (OE-006, BR-009).
func (e *Engine) OnOrderUpdate(ctx context.Context, u *brokerv1.BrokerOrderUpdate) error {
	b := u.GetOrder()
	if !attribution.IsPlatform(b.GetClientOrderId()) {
		return nil // manual broker orders are the ledger's business (VP-001)
	}
	acct := u.GetBrokerAccountId()
	e.mu.Lock()
	o := e.findOrder(acct, b)
	tx := &Tx{}
	if o == nil && b.GetParentBrokerOrderId() != "" {
		if pid, ok := e.byBroker[acct+"|"+b.GetParentBrokerOrderId()]; ok {
			o = e.upsertLeg(e.orders[pid], b, tx)
		}
	}
	if o == nil {
		e.mu.Unlock()
		return nil // unknown platform order: the order reconciler raises orphan_order (OE-019)
	}
	prevState, prevFilled := o.GetState(), pd(o.GetFilledQty())
	if !IsFinal(o.GetState()) || b.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
		if rank(b.GetState()) >= rank(o.GetState()) || o.GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN {
			o.State = b.GetState()
		}
	}
	if f := pd(b.GetFilledQty()); f.GreaterThan(prevFilled) {
		o.FilledQty, o.AvgPrice = b.GetFilledQty(), b.GetAvgFillPrice()
	}
	if b.GetBrokerOrderId() != "" && o.GetBrokerOrderId() == "" {
		o.BrokerOrderId = b.GetBrokerOrderId()
	}
	o.BrokerStatus, o.UpdatedAt = b.GetBrokerStatus(), e.now()
	for _, leg := range b.GetLegs() {
		if o.GetParentOrderId() == "" {
			e.upsertLeg(o, leg, tx)
		}
	}
	e.index(o)
	changed := o.GetState() != prevState || !pd(o.GetFilledQty()).Equal(prevFilled)
	if prevState == ordersv1.OrderState_ORDER_STATE_UNKNOWN && o.GetState() != prevState {
		e.clearHalt(haltKey(acct, o.GetStrategyId(), o.GetInstrumentId(), "unknown_order:"+o.GetOrderId()), tx)
	}
	if changed {
		tx.Orders = append(tx.Orders, o)
		tx.Outbox = append(tx.Outbox, e.orderMsg(o))
	}
	err := e.commit(ctx, tx)
	final := IsFinal(o.GetState()) && !IsFinal(prevState)
	e.mu.Unlock()
	if final {
		e.afterFinal(ctx, o)
		e.maybeProtect(ctx, o)
	}
	return err
}

func rank(s ordersv1.OrderState) int {
	switch s {
	case ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT:
		return 1
	case ordersv1.OrderState_ORDER_STATE_SUBMITTED:
		return 2
	case ordersv1.OrderState_ORDER_STATE_ACCEPTED, ordersv1.OrderState_ORDER_STATE_PENDING_REPLACE:
		return 3
	case ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED, ordersv1.OrderState_ORDER_STATE_PENDING_CANCEL:
		return 4
	case ordersv1.OrderState_ORDER_STATE_UNKNOWN:
		return 0
	}
	if IsFinal(s) {
		return 5
	}
	return 0
}

// OnExecution records a fill (exec.<acct>), deduplicated on broker_exec_id (OE-006), for every
// owner (manual fills feed the day-trade count, RM-008).
func (e *Engine) OnExecution(ctx context.Context, x *ordersv1.ExecutionReport) error {
	acct := x.GetBrokerAccountId()
	key := acct + "|" + x.GetBrokerExecId()
	e.mu.Lock()
	if e.fills[key] != nil {
		e.mu.Unlock()
		return nil
	}
	f := &ordersv1.Fill{BrokerAccountId: acct, BrokerExecId: x.GetBrokerExecId(), InstrumentId: x.GetInstrumentId(), Side: x.GetSide(), Qty: x.GetQty(),
		Price: x.GetPrice(), Fees: x.GetFees(), EventTime: x.GetEventTime(), CorrelationId: x.GetCorrelationId(), ClientOrderId: x.GetClientOrderId(), StrategyId: Manual}
	var o *ordersv1.Order
	if id, ok := e.byBroker[acct+"|"+x.GetBrokerOrderId()]; ok {
		o = e.orders[id]
	}
	if o != nil {
		f.OrderId, f.IntentId, f.StrategyId, f.CorrelationId = o.GetOrderId(), o.GetIntentId(), o.GetStrategyId(), o.GetCorrelationId()
		if f.InstrumentId == "" {
			f.InstrumentId = o.GetInstrumentId()
		}
	} else if id, err := attribution.Parse(x.GetClientOrderId(), 0); err == nil {
		f.IntentId = id.IntentID
		for sid, s := range e.strategies {
			if s.GetShortId() == id.StrategyShortID {
				f.StrategyId = sid
			}
		}
	}
	if f.GetInstrumentId() == "" {
		f.InstrumentId = "sym:" + x.GetSymbol()
	}
	e.fills[key] = f
	e.trackTrip(f)
	err := e.commit(ctx, &Tx{Fills: []*ordersv1.Fill{f}})
	e.mu.Unlock()
	return err
}

// trackTrip maintains per-strategy round trips for the consecutive-loss limit (RM-003).
func (e *Engine) trackTrip(f *ordersv1.Fill) {
	if f.GetStrategyId() == Manual || f.GetStrategyId() == "" {
		return
	}
	k := f.GetStrategyId() + "|" + f.GetInstrumentId()
	t := e.trips[k]
	if t == nil {
		t = &roundTrip{}
		e.trips[k] = t
	}
	q, px := pd(f.GetQty()), pd(f.GetPrice())
	if f.GetSide() != commonv1.Side_SIDE_BUY {
		q = q.Neg()
	}
	if t.qty.IsZero() || t.qty.Sign() == q.Sign() {
		t.qty, t.cost = t.qty.Add(q), t.cost.Add(q.Mul(px))
		return
	}
	closeQty := decimal.Min(t.qty.Abs(), q.Abs())
	avg := t.cost.Div(t.qty)
	sign := decimal.NewFromInt(int64(t.qty.Sign()))
	t.pnl = t.pnl.Add(px.Sub(avg).Mul(closeQty).Mul(sign))
	t.cost = t.cost.Sub(avg.Mul(closeQty).Mul(sign))
	t.qty = t.qty.Add(q)
	if t.qty.IsZero() {
		if t.pnl.IsNegative() {
			e.losses[f.GetStrategyId()]++
		} else {
			e.losses[f.GetStrategyId()] = 0
		}
		t.pnl, t.cost = decimal.Zero, decimal.Zero
	}
}

// ---- order reconciliation (OE-019, OE-020)

// ReconcileOrders compares OMS open orders with the broker's for one account: unknown platform
// orders at the broker are orphans (halt + page); OMS orders the broker no longer lists are looked
// up by client_order_id; UNKNOWN orders are resolved or, after UnknownGiveUp without a trace at the
// broker, rejected as never sent.
func (e *Engine) ReconcileOrders(ctx context.Context, acct string) error {
	open, err := e.brokers.ListOpenOrders(ctx, acct)
	if err != nil {
		return err
	}
	e.mu.Lock()
	tx := &Tx{}
	seen := map[string]bool{}
	var resolved []*ordersv1.Order
	for _, b := range open {
		if !attribution.IsPlatform(b.GetClientOrderId()) {
			continue
		}
		o := e.findOrder(acct, b)
		if o == nil {
			id, _ := attribution.Parse(b.GetClientOrderId(), 0)
			owner := ""
			for sid, s := range e.strategies {
				if s.GetShortId() == id.StrategyShortID {
					owner = sid
				}
			}
			e.addHalt(acct, owner, b.GetInstrument().GetInstrumentId(), "orphan order "+b.GetClientOrderId()+" open at the broker, unknown to the OMS (OE-019)", "orphan:"+b.GetBrokerOrderId(), tx)
			continue
		}
		seen[o.GetOrderId()] = true
		wasUnknown := o.GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN
		e.applyBrokerOrder(o, b, tx)
		e.index(o)
		tx.Orders = append(tx.Orders, o)
		if wasUnknown && o.GetState() != ordersv1.OrderState_ORDER_STATE_UNKNOWN {
			e.clearHalt(haltKey(acct, o.GetStrategyId(), o.GetInstrumentId(), "unknown_order:"+o.GetOrderId()), tx)
			tx.Outbox = append(tx.Outbox, e.orderMsg(o))
			resolved = append(resolved, o)
		}
		for _, leg := range b.GetLegs() {
			if lid, ok := e.byBroker[acct+"|"+leg.GetBrokerOrderId()]; ok {
				seen[lid] = true
			}
		}
	}
	var lookup []*ordersv1.Order
	for _, o := range e.orders {
		if o.GetBrokerAccountId() == acct && !IsFinal(o.GetState()) && !seen[o.GetOrderId()] && o.GetState() != ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT {
			lookup = append(lookup, o)
		}
	}
	_ = e.commit(ctx, tx)
	e.mu.Unlock()
	for _, o := range resolved {
		if IsFinal(o.GetState()) {
			e.afterFinal(ctx, o)
			e.maybeProtect(ctx, o)
		}
	}
	for _, o := range lookup {
		r, err := e.brokers.GetOrder(ctx, acct, o.GetClientOrderId(), o.GetBrokerOrderId())
		if err != nil {
			continue
		}
		e.mu.Lock()
		tx := &Tx{}
		wasUnknown := o.GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN
		switch {
		case r.GetFound():
			e.applyBrokerOrder(o, r.GetOrder(), tx)
		case wasUnknown && e.cfg.Now().Sub(time.Unix(0, o.GetCreatedAt())) > e.cfg.UnknownGiveUp:
			o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_REJECTED, "never reached the broker (not found by client_order_id)"
		case !wasUnknown:
			o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_UNKNOWN, "ghost: open in the OMS, unknown to the broker (OE-019)"
			e.addHalt(acct, o.GetStrategyId(), o.GetInstrumentId(), "order "+o.GetClientOrderId()+" not found at the broker", "unknown_order:"+o.GetOrderId(), tx)
		}
		if wasUnknown && o.GetState() != ordersv1.OrderState_ORDER_STATE_UNKNOWN {
			e.clearHalt(haltKey(acct, o.GetStrategyId(), o.GetInstrumentId(), "unknown_order:"+o.GetOrderId()), tx)
		}
		e.index(o)
		o.UpdatedAt = e.now()
		tx.Orders = append(tx.Orders, o)
		tx.Outbox = append(tx.Outbox, e.orderMsg(o))
		_ = e.commit(ctx, tx)
		final := IsFinal(o.GetState())
		e.mu.Unlock()
		if final {
			e.afterFinal(ctx, o)
			e.maybeProtect(ctx, o)
		}
	}
	e.mu.Lock()
	a := e.acct(acct)
	a.ordersRecon, a.lastOrderRecon = true, e.cfg.Wall()
	e.mu.Unlock()
	return nil
}

// pageOnce queues a critical alert at most once per key (until reset).
func (e *Engine) pageOnce(key, subject, text, runbook string, tx *Tx) {
	if e.alerted[key] {
		return
	}
	e.alerted[key] = true
	tx.Outbox = append(tx.Outbox, e.alert(alertsv1.Severity_SEVERITY_CRITICAL, subject, text, key, runbook, ""))
}
