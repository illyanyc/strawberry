package simcore

import (
	"context"
	"fmt"
	"sort"

	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/services/execution/internal/oms"
)

// Broker implements oms.Brokers for simulation. It never decides a fill: it records what the OMS
// submits (bracket legs held until the parent fills, broker-side OCO between legs, as at Alpaca)
// and applies the fills and order ends the backtester's fill model reports (BT-020..BT-024). Every
// order it creates or changes is queued for the backtester.
type Broker struct {
	now      func() int64
	accounts map[string]*brokerv1.BrokerAccount
	instr    map[string]*brokerv1.InstrumentInfo
	orders   map[string]*brokerv1.BrokerOrder // by broker order ID (legs included)
	byClient map[string]string                // acct|client order ID → top-level broker order ID
	seq      int
	changed  []string
}

func newBroker(now func() int64) *Broker {
	return &Broker{now: now, accounts: map[string]*brokerv1.BrokerAccount{}, instr: map[string]*brokerv1.InstrumentInfo{},
		orders: map[string]*brokerv1.BrokerOrder{}, byClient: map[string]string{}}
}

func (b *Broker) touch(id string) { b.changed = append(b.changed, id) }

// drain returns the orders changed since the last call (each once, latest state, in first-change
// order) as independent copies.
func (b *Broker) drain() []*brokerv1.BrokerOrder {
	seen := map[string]bool{}
	var out []*brokerv1.BrokerOrder
	for _, id := range b.changed {
		if seen[id] {
			continue
		}
		seen[id] = true
		out = append(out, b.view(b.orders[id]))
	}
	b.changed = b.changed[:0]
	return out
}

// view is a copy of o with its legs' current state.
func (b *Broker) view(o *brokerv1.BrokerOrder) *brokerv1.BrokerOrder {
	c := proto.Clone(o).(*brokerv1.BrokerOrder)
	for i, l := range c.GetLegs() {
		if cur := b.orders[l.GetBrokerOrderId()]; cur != nil {
			c.Legs[i] = proto.Clone(cur).(*brokerv1.BrokerOrder)
		}
	}
	return c
}

// Accounts implements oms.Brokers.
func (b *Broker) Accounts(context.Context) ([]*brokerv1.BrokerAccount, error) {
	ids := make([]string, 0, len(b.accounts))
	for id := range b.accounts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := make([]*brokerv1.BrokerAccount, 0, len(ids))
	for _, id := range ids {
		out = append(out, b.accounts[id])
	}
	return out, nil
}

// Session implements oms.Brokers: simulated sessions are always READY.
func (b *Broker) Session(_ context.Context, acct string) (*brokerv1.SessionStatus, error) {
	if b.accounts[acct] == nil {
		return nil, fmt.Errorf("simcore: unknown account %q", acct)
	}
	return &brokerv1.SessionStatus{BrokerAccountId: acct, State: brokerv1.SessionState_SESSION_STATE_READY, Connected: true, Authenticated: true,
		LastHeartbeat: b.now(), StateSince: 0}, nil
}

func (b *Broker) newID() string {
	b.seq++
	return fmt.Sprintf("sim-%06d", b.seq)
}

func reject(code brokerv1.RejectCode, msg string) *brokerv1.SubmitOrderResponse {
	return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED, Reject: &brokerv1.RejectReason{Code: code, Message: msg, Local: true}}
}

// Submit implements oms.Brokers (idempotent on the client order ID, OE-003).
func (b *Broker) Submit(_ context.Context, r *brokerv1.BrokerOrderRequest) (*brokerv1.SubmitOrderResponse, error) {
	acct := r.GetBrokerAccountId()
	if id, ok := b.byClient[acct+"|"+r.GetClientOrderId()]; ok {
		return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED, Order: b.view(b.orders[id]), RecoveredByLookup: true}, nil
	}
	a := b.accounts[acct]
	if a == nil {
		return nil, fmt.Errorf("simcore: unknown account %q", acct)
	}
	info := b.instr[r.GetInstrument().GetInstrumentId()]
	switch {
	case info == nil || !info.GetTradable():
		return reject(brokerv1.RejectCode_REJECT_CODE_NOT_TRADABLE, "instrument not tradable in the simulation"), nil
	case r.GetSide() == commonv1.Side_SIDE_SELL_SHORT && !info.GetShortable():
		return reject(brokerv1.RejectCode_REJECT_CODE_NOT_SHORTABLE, "not shortable in the borrow snapshot"), nil
	case r.GetBracket() != nil && !a.GetCapabilities().GetSupportsBracket():
		return reject(brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER, "bracket orders not supported by this account"), nil
	}
	now := b.now()
	o := &brokerv1.BrokerOrder{BrokerAccountId: acct, BrokerOrderId: b.newID(), ClientOrderId: r.GetClientOrderId(), Instrument: r.GetInstrument(),
		Side: r.GetSide(), Type: r.GetType(), Tif: r.GetTif(), Qty: r.GetQty(), FilledQty: dec(decimal.Zero), LimitPrice: r.GetLimitPrice(),
		StopPrice: r.GetStopPrice(), State: ordersv1.OrderState_ORDER_STATE_ACCEPTED, BrokerStatus: "new", ExtendedHours: r.GetExtendedHours(),
		CreatedAt: now, SubmittedAt: now, UpdatedAt: now, OrderClass: brokerv1.OrderClass_ORDER_CLASS_SIMPLE}
	b.orders[o.GetBrokerOrderId()] = o
	b.byClient[acct+"|"+o.GetClientOrderId()] = o.GetBrokerOrderId()
	if br := r.GetBracket(); br != nil {
		o.OrderClass = br.GetOrderClass()
		exit := commonv1.Side_SIDE_SELL
		if r.GetSide() != commonv1.Side_SIDE_BUY {
			exit = commonv1.Side_SIDE_BUY
		}
		leg := func(typ ordersv1.OrderType, limit, stop *commonv1.Decimal) {
			l := &brokerv1.BrokerOrder{BrokerAccountId: acct, BrokerOrderId: b.newID(), ClientOrderId: r.GetClientOrderId(), Instrument: r.GetInstrument(),
				Side: exit, Type: typ, Tif: r.GetTif(), Qty: r.GetQty(), FilledQty: dec(decimal.Zero), LimitPrice: limit, StopPrice: stop,
				State: ordersv1.OrderState_ORDER_STATE_ACCEPTED, BrokerStatus: "held", ParentBrokerOrderId: o.GetBrokerOrderId(), CreatedAt: now, UpdatedAt: now}
			b.orders[l.GetBrokerOrderId()] = l
			o.Legs = append(o.Legs, l)
		}
		if pd(br.GetTakeProfitLimitPrice()).IsPositive() {
			leg(ordersv1.OrderType_ORDER_TYPE_LIMIT, br.GetTakeProfitLimitPrice(), nil)
		}
		leg(ordersv1.OrderType_ORDER_TYPE_STOP, nil, br.GetStopLossStopPrice())
	}
	b.touch(o.GetBrokerOrderId())
	return &brokerv1.SubmitOrderResponse{Outcome: brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED, Order: b.view(o), AcceptedAt: now}, nil
}

func (b *Broker) find(acct, clientOrderID, brokerOrderID string) *brokerv1.BrokerOrder {
	if brokerOrderID != "" {
		if o := b.orders[brokerOrderID]; o != nil && o.GetBrokerAccountId() == acct {
			return o
		}
		return nil
	}
	return b.orders[b.byClient[acct+"|"+clientOrderID]]
}

// end moves an order (and, for a parent, its working legs) to a terminal state.
func (b *Broker) end(o *brokerv1.BrokerOrder, st ordersv1.OrderState) {
	if oms.IsFinal(o.GetState()) {
		return
	}
	o.State, o.UpdatedAt = st, b.now()
	o.BrokerStatus = map[ordersv1.OrderState]string{ordersv1.OrderState_ORDER_STATE_CANCELED: "canceled", ordersv1.OrderState_ORDER_STATE_EXPIRED: "expired",
		ordersv1.OrderState_ORDER_STATE_REJECTED: "rejected"}[st]
	if st == ordersv1.OrderState_ORDER_STATE_CANCELED || st == ordersv1.OrderState_ORDER_STATE_EXPIRED {
		o.CanceledAt = b.now()
	}
	b.touch(o.GetBrokerOrderId())
	for _, l := range o.GetLegs() {
		if leg := b.orders[l.GetBrokerOrderId()]; leg != nil {
			b.end(leg, st)
		}
	}
}

// Cancel implements oms.Brokers: cancels complete immediately in simulation.
func (b *Broker) Cancel(_ context.Context, acct, clientOrderID, brokerOrderID string) (*brokerv1.CancelOrderResponse, error) {
	o := b.find(acct, clientOrderID, brokerOrderID)
	if o == nil {
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_NOT_FOUND}, nil
	}
	if oms.IsFinal(o.GetState()) {
		return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_ALREADY_FINAL, Order: b.view(o)}, nil
	}
	b.end(o, ordersv1.OrderState_ORDER_STATE_CANCELED)
	return &brokerv1.CancelOrderResponse{Outcome: brokerv1.CancelOutcome_CANCEL_OUTCOME_REQUESTED, Order: b.view(o)}, nil
}

// GetOrder implements oms.Brokers.
func (b *Broker) GetOrder(_ context.Context, acct, clientOrderID, brokerOrderID string) (*brokerv1.GetOrderResponse, error) {
	o := b.find(acct, clientOrderID, brokerOrderID)
	if o == nil {
		return &brokerv1.GetOrderResponse{Found: false}, nil
	}
	return &brokerv1.GetOrderResponse{Found: true, Order: b.view(o)}, nil
}

// ListOpenOrders implements oms.Brokers (top-level orders with their legs).
func (b *Broker) ListOpenOrders(_ context.Context, acct string) ([]*brokerv1.BrokerOrder, error) {
	var out []*brokerv1.BrokerOrder
	for _, o := range b.orders {
		if o.GetBrokerAccountId() != acct || o.GetParentBrokerOrderId() != "" {
			continue
		}
		open := !oms.IsFinal(o.GetState())
		for _, l := range o.GetLegs() {
			open = open || !oms.IsFinal(b.orders[l.GetBrokerOrderId()].GetState())
		}
		if open {
			out = append(out, b.view(o))
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetBrokerOrderId() < out[j].GetBrokerOrderId() })
	return out, nil
}

// InstrumentInfo implements oms.Brokers.
func (b *Broker) InstrumentInfo(_ context.Context, _ string, inst *commonv1.Instrument) (*brokerv1.InstrumentInfo, error) {
	if info := b.instr[inst.GetInstrumentId()]; info != nil {
		c := proto.Clone(info).(*brokerv1.InstrumentInfo)
		c.AsOf = b.now()
		return c, nil
	}
	return &brokerv1.InstrumentInfo{Instrument: inst, Tradable: false, AsOf: b.now()}, nil
}

// fill applies a fill to an order and returns the execution report plus every order it changed
// (released legs, OCO siblings canceled by the broker).
func (b *Broker) fill(acct, brokerOrderID string, qty, px, fees decimal.Decimal, execID string) (*ordersv1.ExecutionReport, error) {
	o := b.orders[brokerOrderID]
	if o == nil || o.GetBrokerAccountId() != acct {
		return nil, fmt.Errorf("simcore: fill for unknown order %q", brokerOrderID)
	}
	if oms.IsFinal(o.GetState()) || o.GetBrokerStatus() == "held" {
		return nil, fmt.Errorf("simcore: fill for order %s in state %s/%s", brokerOrderID, o.GetState(), o.GetBrokerStatus())
	}
	filled := pd(o.GetFilledQty())
	remaining := pd(o.GetQty()).Sub(filled)
	if !qty.IsPositive() || qty.GreaterThan(remaining) {
		return nil, fmt.Errorf("simcore: fill qty %s for order %s with %s remaining", qty, brokerOrderID, remaining)
	}
	cum := filled.Add(qty)
	avg := px
	if filled.IsPositive() {
		avg = pd(o.GetAvgFillPrice()).Mul(filled).Add(px.Mul(qty)).DivRound(cum, 6)
	}
	o.FilledQty, o.AvgFillPrice, o.UpdatedAt = dec(cum), dec(avg), b.now()
	if cum.Equal(pd(o.GetQty())) {
		o.State, o.BrokerStatus, o.FilledAt = ordersv1.OrderState_ORDER_STATE_FILLED, "filled", b.now()
	} else {
		o.State, o.BrokerStatus = ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED, "partially_filled"
	}
	b.touch(o.GetBrokerOrderId())
	// The parent's fill releases its legs for the filled quantity (Alpaca brackets).
	for _, l := range o.GetLegs() {
		if leg := b.orders[l.GetBrokerOrderId()]; leg != nil && !oms.IsFinal(leg.GetState()) {
			leg.Qty, leg.BrokerStatus, leg.UpdatedAt = dec(cum), "new", b.now()
			b.touch(leg.GetBrokerOrderId())
		}
	}
	// Broker-side OCO: a filled leg cancels its siblings.
	if p := o.GetParentBrokerOrderId(); p != "" && o.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
		for _, l := range b.orders[p].GetLegs() {
			if sib := b.orders[l.GetBrokerOrderId()]; sib != nil && sib.GetBrokerOrderId() != o.GetBrokerOrderId() {
				b.end(sib, ordersv1.OrderState_ORDER_STATE_CANCELED)
			}
		}
	}
	if execID == "" {
		execID = brokerOrderID + ":" + cum.String()
	}
	return &ordersv1.ExecutionReport{BrokerAccountId: acct, BrokerExecId: execID, ClientOrderId: o.GetClientOrderId(), BrokerOrderId: brokerOrderID,
		InstrumentId: o.GetInstrument().GetInstrumentId(), Symbol: o.GetInstrument().GetSymbol(), Side: o.GetSide(), Qty: dec(qty), Price: dec(px), Fees: dec(fees),
		EventTime: b.now(), Mode: commonv1.Mode_MODE_BACKTEST, Broker: commonv1.Broker_BROKER_SIMULATOR, FeeCurrency: "USD"}, nil
}
