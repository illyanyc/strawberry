// Package sim is a deterministic broker simulator implementing adapter.Broker (section 04,
// ADR-0019). It models an exchange with settable prices and supports the fault injection the
// contract and chaos tests need: latency, partial fills, rejects, disconnects, duplicate and
// out-of-order execution messages, ambiguous submits, and manual orders placed "outside the
// platform". Same seed + same calls ⇒ same IDs and events.
package sim

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
)

// Config tunes the simulator.
type Config struct {
	AccountID   string
	Environment brokerv1.Environment
	// Latency added to every broker call and event.
	Latency time.Duration
	// PartialFills splits each fill into this many executions (≥ 1).
	PartialFills int
	// RejectSymbols are rejected as not tradable.
	RejectSymbols []string
	// DuplicateExecs emits every execution message twice (BR-008 tests).
	DuplicateExecs bool
	// OutOfOrder emits the partial fills of one order in reverse order (BR-009 tests).
	OutOfOrder   bool
	StartingCash decimal.Decimal
	// DefaultPrice for symbols without a SetPrice call.
	DefaultPrice decimal.Decimal
	Now          func() time.Time
	// Broker and Capabilities override the simulated account's identity and capabilities, e.g. an
	// IBKR-shaped account without bracket support for the D30 swing-routing tests.
	Broker       commonv1.Broker
	Capabilities *brokerv1.Capabilities
	// Resolve maps a symbol to the platform instrument_id for snapshot positions and manual
	// orders (an adapter resolves through the instrument master; "" leaves it empty).
	Resolve func(symbol string) string
}

// ErrDisconnected is returned by Stream after Disconnect.
var ErrDisconnected = errors.New("sim: disconnected")

type order struct {
	o       *brokerv1.BrokerOrder
	req     *brokerv1.BrokerOrderRequest
	parent  string
	active  bool // children of a bracket become active when the parent fills
	ocoPeer string
}

// Sim is the simulated broker for one account.
type Sim struct {
	mu              sync.Mutex
	cfg             Config
	seq             int
	orders          map[string]*order
	byClient        map[string]string
	execs           []*ordersv1.ExecutionReport
	positions       map[string]decimal.Decimal
	cash            decimal.Decimal
	prices          map[string]decimal.Decimal
	events          chan *brokerv1.BrokerOrderUpdate
	disc            chan struct{}
	marginCall, imd bool

	failAfterAccept  int
	failBeforeAccept int
	failLookups      int
	connectErr       error
}

// New returns a simulator.
func New(cfg Config) *Sim {
	if cfg.PartialFills < 1 {
		cfg.PartialFills = 1
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.StartingCash.IsZero() {
		cfg.StartingCash = decimal.NewFromInt(100_000)
	}
	if cfg.DefaultPrice.IsZero() {
		cfg.DefaultPrice = decimal.NewFromInt(100)
	}
	if cfg.Environment == brokerv1.Environment_ENVIRONMENT_UNSPECIFIED {
		cfg.Environment = brokerv1.Environment_ENVIRONMENT_PAPER
	}
	return &Sim{cfg: cfg, orders: map[string]*order{}, byClient: map[string]string{}, positions: map[string]decimal.Decimal{},
		cash: cfg.StartingCash, prices: map[string]decimal.Decimal{}}
}

// Capabilities of a simulated account.
func Capabilities() *brokerv1.Capabilities {
	return &brokerv1.Capabilities{
		OrderTypes: []ordersv1.OrderType{ordersv1.OrderType_ORDER_TYPE_MARKET, ordersv1.OrderType_ORDER_TYPE_LIMIT,
			ordersv1.OrderType_ORDER_TYPE_STOP, ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT},
		Tifs: []ordersv1.TimeInForce{ordersv1.TimeInForce_TIME_IN_FORCE_DAY, ordersv1.TimeInForce_TIME_IN_FORCE_GTC,
			ordersv1.TimeInForce_TIME_IN_FORCE_IOC, ordersv1.TimeInForce_TIME_IN_FORCE_FOK},
		SupportsBracket: true, SupportsOco: true, SupportsOto: true, SupportsFractional: true, SupportsShort: true,
		SupportsExtendedHours: true, MaxClientOrderIdLen: 128, IsMargin: true, SupportsReplace: true,
		DataFeeds: []string{"sim"},
	}
}

// Info implements adapter.Broker.
func (s *Sim) Info() adapter.Info {
	b, caps := commonv1.Broker_BROKER_SIMULATOR, Capabilities()
	if s.cfg.Broker != commonv1.Broker_BROKER_UNSPECIFIED {
		b = s.cfg.Broker
	}
	if s.cfg.Capabilities != nil {
		caps = s.cfg.Capabilities
	}
	return adapter.Info{AccountID: s.cfg.AccountID, Broker: b, Environment: s.cfg.Environment, Capabilities: caps}
}

func (s *Sim) resolve(sym string) string {
	if s.cfg.Resolve == nil {
		return ""
	}
	return s.cfg.Resolve(sym)
}

func (s *Sim) sleep(ctx context.Context) error {
	if s.cfg.Latency <= 0 {
		return ctx.Err()
	}
	t := time.NewTimer(s.cfg.Latency)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func (s *Sim) now() int64 { return s.cfg.Now().UnixNano() }

func (s *Sim) nextID(prefix string) string {
	s.seq++
	return fmt.Sprintf("%s-%06d", prefix, s.seq)
}

// ---- fault injection

// FailNextSubmitAfterAccept makes the next n submits create the order but return an ambiguous
// timeout to the caller (BR-007).
func (s *Sim) FailNextSubmitAfterAccept(n int) { s.mu.Lock(); s.failAfterAccept = n; s.mu.Unlock() }

// FailNextSubmitBeforeAccept makes the next n submits time out without creating the order.
func (s *Sim) FailNextSubmitBeforeAccept(n int) { s.mu.Lock(); s.failBeforeAccept = n; s.mu.Unlock() }

// FailNextLookups makes the next n GetOrder calls fail with a transport error.
func (s *Sim) FailNextLookups(n int) { s.mu.Lock(); s.failLookups = n; s.mu.Unlock() }

// SetConnectError makes Connect fail with err (nil clears).
func (s *Sim) SetConnectError(err error) { s.mu.Lock(); s.connectErr = err; s.mu.Unlock() }

// Disconnect drops the current stream; events until the next Stream call are not delivered (they
// are visible to ListOrders / ListExecutions, as at a real broker).
func (s *Sim) Disconnect() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.disc != nil {
		close(s.disc)
		s.disc = nil
		s.events = nil
	}
}

// Streaming reports whether a stream is attached.
func (s *Sim) Streaming() bool { s.mu.Lock(); defer s.mu.Unlock(); return s.events != nil }

// SetMargin sets the broker-reported margin flags (BR-021).
func (s *Sim) SetMargin(marginCall, imd bool) {
	s.mu.Lock()
	s.marginCall, s.imd = marginCall, imd
	s.mu.Unlock()
}

// SetPrice moves the market for symbol and matches resting orders.
func (s *Sim) SetPrice(symbol string, p decimal.Decimal) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.prices[strings.ToUpper(symbol)] = p
	ids := make([]string, 0, len(s.orders))
	for id := range s.orders {
		ids = append(ids, id)
	}
	slices.Sort(ids)
	for _, id := range ids {
		s.match(s.orders[id])
	}
}

// InjectManualOrder places and fills an order "in the broker's own app": no tb1- attribution
// (VP-001, BR-019). It returns the broker order ID.
func (s *Sim) InjectManualOrder(symbol string, side commonv1.Side, qty decimal.Decimal) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	req := &brokerv1.BrokerOrderRequest{
		ClientOrderId: "app-" + s.nextID("m"), Instrument: &commonv1.Instrument{Symbol: strings.ToUpper(symbol), InstrumentId: s.resolve(strings.ToUpper(symbol))}, Side: side,
		Qty: adapter.D(qty), Type: ordersv1.OrderType_ORDER_TYPE_MARKET, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY,
	}
	o := s.create(req)
	s.match(o)
	return o.o.GetBrokerOrderId()
}

// ---- adapter.Broker

// Connect implements adapter.Broker.
func (s *Sim) Connect(ctx context.Context) error {
	if err := s.sleep(ctx); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.connectErr
}

// Stream implements adapter.Broker.
func (s *Sim) Stream(ctx context.Context, onSubscribed func(), emit func(*brokerv1.BrokerOrderUpdate)) error {
	s.mu.Lock()
	if s.connectErr != nil {
		err := s.connectErr
		s.mu.Unlock()
		return err
	}
	ch := make(chan *brokerv1.BrokerOrderUpdate, 4096)
	disc := make(chan struct{})
	s.events, s.disc = ch, disc
	s.mu.Unlock()
	onSubscribed()
	for {
		select {
		case <-ctx.Done():
			s.mu.Lock()
			if s.disc == disc {
				s.events, s.disc = nil, nil
			}
			s.mu.Unlock()
			return ctx.Err()
		case <-disc:
			return ErrDisconnected
		case u := <-ch:
			if s.cfg.Latency > 0 {
				time.Sleep(s.cfg.Latency)
			}
			emit(u)
		}
	}
}

func (s *Sim) emit(u *brokerv1.BrokerOrderUpdate) {
	if s.events == nil {
		return
	}
	select {
	case s.events <- u:
	default:
	}
}

func cloneOrder(o *brokerv1.BrokerOrder) *brokerv1.BrokerOrder {
	c := proto.Clone(o).(*brokerv1.BrokerOrder)
	c.Legs = nil
	return c
}

func (s *Sim) snapshotOrder(o *order) *brokerv1.BrokerOrder {
	c := cloneOrder(o.o)
	for _, id := range s.childIDs(o.o.GetBrokerOrderId()) {
		c.Legs = append(c.Legs, cloneOrder(s.orders[id].o))
	}
	return c
}

func (s *Sim) childIDs(parent string) []string {
	var ids []string
	for id, x := range s.orders {
		if x.parent == parent {
			ids = append(ids, id)
		}
	}
	slices.Sort(ids)
	return ids
}

func (s *Sim) update(o *order, event string, exec *ordersv1.ExecutionReport) *brokerv1.BrokerOrderUpdate {
	o.o.UpdatedAt = s.now()
	return &brokerv1.BrokerOrderUpdate{BrokerAccountId: s.cfg.AccountID, Event: event, Order: s.snapshotOrder(o), Execution: exec,
		EventTime: o.o.GetUpdatedAt(), ReceivedAt: s.now()}
}

func (s *Sim) setState(o *order, st ordersv1.OrderState) {
	o.o.State = st
	o.o.BrokerStatus = strings.ToLower(strings.TrimPrefix(st.String(), "ORDER_STATE_"))
}

func (s *Sim) create(req *brokerv1.BrokerOrderRequest) *order {
	id := s.nextID("sim")
	o := &order{req: req, active: true, o: &brokerv1.BrokerOrder{
		BrokerAccountId: s.cfg.AccountID, BrokerOrderId: id, ClientOrderId: req.GetClientOrderId(),
		Instrument: req.GetInstrument(), Side: req.GetSide(), Type: req.GetType(), Tif: req.GetTif(),
		OrderClass: brokerv1.OrderClass_ORDER_CLASS_SIMPLE, Qty: req.GetQty(), FilledQty: adapter.D(decimal.Zero),
		LimitPrice: req.GetLimitPrice(), StopPrice: req.GetStopPrice(), ExtendedHours: req.GetExtendedHours(),
		CreatedAt: s.now(), SubmittedAt: s.now(), UpdatedAt: s.now(),
	}}
	s.setState(o, ordersv1.OrderState_ORDER_STATE_ACCEPTED)
	s.orders[id] = o
	if req.GetClientOrderId() != "" {
		s.byClient[req.GetClientOrderId()] = id
	}
	if b := req.GetBracket(); b != nil && b.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_UNSPECIFIED && b.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_SIMPLE {
		o.o.OrderClass = b.GetOrderClass()
		exit := commonv1.Side_SIDE_SELL
		if req.GetSide() != commonv1.Side_SIDE_BUY {
			exit = commonv1.Side_SIDE_BUY
		}
		var legIDs []string
		mk := func(t ordersv1.OrderType, lim, stop *commonv1.Decimal) {
			cid := s.nextID("leg")
			leg := &order{parent: id, active: b.GetOrderClass() == brokerv1.OrderClass_ORDER_CLASS_OCO, o: &brokerv1.BrokerOrder{
				BrokerAccountId: s.cfg.AccountID, BrokerOrderId: cid, ClientOrderId: req.GetClientOrderId(), Instrument: req.GetInstrument(),
				Side: exit, Type: t, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_GTC, OrderClass: b.GetOrderClass(), Qty: req.GetQty(),
				FilledQty: adapter.D(decimal.Zero), LimitPrice: lim, StopPrice: stop, ParentBrokerOrderId: id,
				CreatedAt: s.now(), SubmittedAt: s.now(), UpdatedAt: s.now(),
			}}
			leg.req = &brokerv1.BrokerOrderRequest{Side: exit, Type: t, LimitPrice: lim, StopPrice: stop, Qty: req.GetQty(), Instrument: req.GetInstrument(), Tif: leg.o.Tif}
			s.setState(leg, ordersv1.OrderState_ORDER_STATE_ACCEPTED)
			leg.o.BrokerStatus = "held"
			s.orders[cid] = leg
			legIDs = append(legIDs, cid)
		}
		if b.GetTakeProfitLimitPrice().GetValue() != "" {
			mk(ordersv1.OrderType_ORDER_TYPE_LIMIT, b.GetTakeProfitLimitPrice(), nil)
		}
		if b.GetStopLossStopPrice().GetValue() != "" {
			if b.GetStopLossLimitPrice().GetValue() != "" {
				mk(ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT, b.GetStopLossLimitPrice(), b.GetStopLossStopPrice())
			} else {
				mk(ordersv1.OrderType_ORDER_TYPE_STOP, nil, b.GetStopLossStopPrice())
			}
		}
		if len(legIDs) == 2 {
			s.orders[legIDs[0]].ocoPeer = legIDs[1]
			s.orders[legIDs[1]].ocoPeer = legIDs[0]
		}
		if b.GetOrderClass() == brokerv1.OrderClass_ORDER_CLASS_OCO {
			// OCO is an exit pair only: the "parent" is a placeholder that is immediately done.
			s.setState(o, ordersv1.OrderState_ORDER_STATE_FILLED)
			o.o.FilledQty = o.o.Qty
			o.active = false
		}
	}
	s.emit(s.update(o, "new", nil))
	return o
}

func (s *Sim) price(sym string) decimal.Decimal {
	if p, ok := s.prices[strings.ToUpper(sym)]; ok {
		return p
	}
	return s.cfg.DefaultPrice
}

// marketable returns whether o can fill at the current price, and at what price.
func (s *Sim) marketable(o *order) (bool, decimal.Decimal) {
	px := s.price(o.o.GetInstrument().GetSymbol())
	buy := o.o.GetSide() == commonv1.Side_SIDE_BUY
	lim, _, _ := adapter.Dec(o.o.GetLimitPrice())
	stop, _, _ := adapter.Dec(o.o.GetStopPrice())
	switch o.o.GetType() {
	case ordersv1.OrderType_ORDER_TYPE_MARKET:
		return true, px
	case ordersv1.OrderType_ORDER_TYPE_LIMIT:
		if (buy && px.LessThanOrEqual(lim)) || (!buy && px.GreaterThanOrEqual(lim)) {
			return true, px
		}
	case ordersv1.OrderType_ORDER_TYPE_STOP:
		if (buy && px.GreaterThanOrEqual(stop)) || (!buy && px.LessThanOrEqual(stop)) {
			return true, px
		}
	case ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT:
		triggered := (buy && px.GreaterThanOrEqual(stop)) || (!buy && px.LessThanOrEqual(stop))
		if triggered && ((buy && px.LessThanOrEqual(lim)) || (!buy && px.GreaterThanOrEqual(lim))) {
			return true, px
		}
	}
	return false, decimal.Zero
}

func (s *Sim) match(o *order) {
	if o == nil || !o.active || adapter.IsFinal(o.o.GetState()) {
		return
	}
	ok, px := s.marketable(o)
	if !ok {
		if tif := o.o.GetTif(); tif == ordersv1.TimeInForce_TIME_IN_FORCE_IOC || tif == ordersv1.TimeInForce_TIME_IN_FORCE_FOK {
			s.setState(o, ordersv1.OrderState_ORDER_STATE_CANCELED)
			o.o.CanceledAt = s.now()
			s.emit(s.update(o, "canceled", nil))
		}
		return
	}
	qty, _, _ := adapter.Dec(o.o.GetQty())
	filled, _, _ := adapter.Dec(o.o.GetFilledQty())
	remaining := qty.Sub(filled)
	n := int64(s.cfg.PartialFills)
	part := remaining.Div(decimal.NewFromInt(n)).Truncate(0)
	if part.IsZero() {
		part, n = remaining, 1
	}
	var updates []*brokerv1.BrokerOrderUpdate
	for i := int64(0); i < n; i++ {
		q := part
		if i == n-1 {
			q = remaining.Sub(part.Mul(decimal.NewFromInt(n - 1)))
		}
		filled = filled.Add(q)
		o.o.FilledQty = adapter.D(filled)
		o.o.AvgFillPrice = adapter.D(px)
		ev := "partial_fill"
		if filled.Equal(qty) {
			s.setState(o, ordersv1.OrderState_ORDER_STATE_FILLED)
			o.o.FilledAt = s.now()
			ev = "fill"
		} else {
			s.setState(o, ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED)
		}
		s.seq++
		e := &ordersv1.ExecutionReport{
			BrokerAccountId: s.cfg.AccountID, BrokerExecId: o.o.GetBrokerOrderId() + ":" + filled.String(),
			ClientOrderId: o.o.GetClientOrderId(), BrokerOrderId: o.o.GetBrokerOrderId(), InstrumentId: o.o.GetInstrument().GetInstrumentId(),
			Symbol: o.o.GetInstrument().GetSymbol(), Side: o.o.GetSide(), Qty: adapter.D(q), Price: adapter.D(px), Fees: adapter.D(decimal.Zero),
			EventTime: s.now(), Sequence: int64(s.seq), Broker: commonv1.Broker_BROKER_SIMULATOR, FeeCurrency: "USD",
		}
		s.execs = append(s.execs, e)
		signed := q
		if o.o.GetSide() != commonv1.Side_SIDE_BUY {
			signed = q.Neg()
		}
		sym := strings.ToUpper(o.o.GetInstrument().GetSymbol())
		s.positions[sym] = s.positions[sym].Add(signed)
		s.cash = s.cash.Sub(signed.Mul(px))
		updates = append(updates, s.update(o, ev, e))
	}
	if s.cfg.OutOfOrder {
		slices.Reverse(updates)
	}
	for _, u := range updates {
		s.emit(u)
		if s.cfg.DuplicateExecs {
			s.emit(u)
		}
	}
	if o.o.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
		// Bracket/OTO children become active when the parent fills; OCO peers cancel each other.
		for _, cid := range s.childIDs(o.o.GetBrokerOrderId()) {
			c := s.orders[cid]
			if !c.active {
				c.active = true
				c.o.BrokerStatus = "new"
				s.emit(s.update(c, "new", nil))
				s.match(c)
			}
		}
		if o.ocoPeer != "" {
			if peer := s.orders[o.ocoPeer]; peer != nil && !adapter.IsFinal(peer.o.GetState()) {
				s.setState(peer, ordersv1.OrderState_ORDER_STATE_CANCELED)
				peer.o.CanceledAt = s.now()
				s.emit(s.update(peer, "canceled", nil))
			}
		}
	}
}

// Submit implements adapter.Broker.
func (s *Sim) Submit(ctx context.Context, req *brokerv1.BrokerOrderRequest) (*brokerv1.BrokerOrder, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.failBeforeAccept > 0 {
		s.failBeforeAccept--
		return nil, context.DeadlineExceeded
	}
	if id, dup := s.byClient[req.GetClientOrderId()]; dup && req.GetClientOrderId() != "" {
		_ = id
		return nil, adapter.Reject(brokerv1.RejectCode_REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID, false, "422", "client_order_id must be unique")
	}
	sym := strings.ToUpper(req.GetInstrument().GetSymbol())
	if slices.Contains(s.cfg.RejectSymbols, sym) {
		return nil, adapter.Reject(brokerv1.RejectCode_REJECT_CODE_NOT_TRADABLE, false, "403", sym+" is not tradable")
	}
	if req.GetSide() == commonv1.Side_SIDE_BUY {
		qty, _, _ := adapter.Dec(req.GetQty())
		if qty.Mul(s.price(sym)).GreaterThan(s.cash) {
			return nil, adapter.Reject(brokerv1.RejectCode_REJECT_CODE_INSUFFICIENT_BUYING_POWER, false, "403", "insufficient buying power")
		}
	}
	o := s.create(req)
	s.match(o)
	if s.failAfterAccept > 0 {
		s.failAfterAccept--
		return nil, context.DeadlineExceeded
	}
	return s.snapshotOrder(o), nil
}

func (s *Sim) find(ref adapter.OrderRef) *order {
	id := ref.BrokerOrderID
	if id == "" {
		id = s.byClient[ref.ClientOrderID]
	}
	return s.orders[id]
}

// Cancel implements adapter.Broker.
func (s *Sim) Cancel(ctx context.Context, ref adapter.OrderRef) error {
	if err := s.sleep(ctx); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	o := s.find(ref)
	if o == nil {
		return adapter.ErrNotFound
	}
	if adapter.IsFinal(o.o.GetState()) {
		return adapter.Reject(brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER, false, "422", "order is not cancelable")
	}
	s.setState(o, ordersv1.OrderState_ORDER_STATE_CANCELED)
	o.o.CanceledAt = s.now()
	s.emit(s.update(o, "canceled", nil))
	for _, cid := range s.childIDs(o.o.GetBrokerOrderId()) {
		if c := s.orders[cid]; !adapter.IsFinal(c.o.GetState()) {
			s.setState(c, ordersv1.OrderState_ORDER_STATE_CANCELED)
			s.emit(s.update(c, "canceled", nil))
		}
	}
	return nil
}

// Replace implements adapter.Broker (Alpaca semantics: a new order replaces the old one).
func (s *Sim) Replace(ctx context.Context, brokerOrderID string, req *brokerv1.ReplaceOrderRequest) (*brokerv1.BrokerOrder, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	o := s.orders[brokerOrderID]
	if o == nil {
		return nil, adapter.ErrNotFound
	}
	if adapter.IsFinal(o.o.GetState()) {
		return nil, adapter.Reject(brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER, false, "422", "order is not replaceable")
	}
	nr := proto.Clone(o.req).(*brokerv1.BrokerOrderRequest)
	nr.ClientOrderId = req.GetNewClientOrderId()
	if req.GetQty().GetValue() != "" {
		nr.Qty = req.GetQty()
	}
	if req.GetLimitPrice().GetValue() != "" {
		nr.LimitPrice = req.GetLimitPrice()
	}
	if req.GetStopPrice().GetValue() != "" {
		nr.StopPrice = req.GetStopPrice()
	}
	if req.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_UNSPECIFIED {
		nr.Tif = req.GetTif()
	}
	nr.Bracket = nil
	n := s.create(nr)
	n.o.ReplacesBrokerOrderId = brokerOrderID
	o.o.ReplacedByBrokerOrderId = n.o.GetBrokerOrderId()
	s.setState(o, ordersv1.OrderState_ORDER_STATE_REPLACED)
	s.emit(s.update(o, "replaced", nil))
	s.match(n)
	return s.snapshotOrder(n), nil
}

// GetOrder implements adapter.Broker.
func (s *Sim) GetOrder(ctx context.Context, ref adapter.OrderRef) (*brokerv1.BrokerOrder, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.failLookups > 0 {
		s.failLookups--
		return nil, errors.New("sim: lookup transport error")
	}
	o := s.find(ref)
	if o == nil {
		return nil, adapter.ErrNotFound
	}
	return s.snapshotOrder(o), nil
}

func (s *Sim) list(filter func(*order) bool) []*brokerv1.BrokerOrder {
	ids := make([]string, 0, len(s.orders))
	for id, o := range s.orders {
		if o.parent == "" && filter(o) {
			ids = append(ids, id)
		}
	}
	slices.Sort(ids)
	out := make([]*brokerv1.BrokerOrder, 0, len(ids))
	for _, id := range ids {
		out = append(out, s.snapshotOrder(s.orders[id]))
	}
	return out
}

// ListOpenOrders implements adapter.Broker.
func (s *Sim) ListOpenOrders(ctx context.Context) ([]*brokerv1.BrokerOrder, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	// Like Alpaca's nested listing, a filled bracket parent is listed while any leg is still open.
	return s.list(func(o *order) bool {
		if !adapter.IsFinal(o.o.GetState()) {
			return true
		}
		for _, id := range s.childIDs(o.o.GetBrokerOrderId()) {
			if !adapter.IsFinal(s.orders[id].o.GetState()) {
				return true
			}
		}
		return false
	}), nil
}

// ListOrders implements adapter.Broker.
func (s *Sim) ListOrders(ctx context.Context, since time.Time) ([]*brokerv1.BrokerOrder, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	t := since.UnixNano()
	return s.list(func(o *order) bool { return o.o.GetUpdatedAt() >= t }), nil
}

// ListExecutions implements adapter.Broker.
func (s *Sim) ListExecutions(ctx context.Context, since time.Time) ([]*ordersv1.ExecutionReport, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	t := since.UnixNano()
	var out []*ordersv1.ExecutionReport
	for _, e := range s.execs {
		if e.GetEventTime() >= t {
			out = append(out, e)
		}
	}
	return out, nil
}

// Snapshot implements adapter.Broker.
func (s *Sim) Snapshot(ctx context.Context) (*ledgerv1.AccountSnapshot, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	equity := s.cash
	syms := make([]string, 0, len(s.positions))
	for sym := range s.positions {
		syms = append(syms, sym)
	}
	slices.Sort(syms)
	snap := &ledgerv1.AccountSnapshot{BrokerAccountId: s.cfg.AccountID, Broker: commonv1.Broker_BROKER_SIMULATOR, Currency: "USD",
		AsOf: s.now(), MarginCall: s.marginCall, IntradayMarginDeficit: s.imd, DayTradeCount: -1, Status: "ACTIVE"}
	for _, sym := range syms {
		q := s.positions[sym]
		if q.IsZero() {
			continue
		}
		mv := q.Mul(s.price(sym))
		equity = equity.Add(mv)
		snap.Positions = append(snap.Positions, &ledgerv1.Position{Symbol: sym, InstrumentId: s.resolve(sym), Qty: adapter.D(q), MarketValue: adapter.D(mv), AvgCost: adapter.D(s.price(sym))})
		if q.IsPositive() {
			snap.LongMarketValue = adapter.D(mustDec(snap.GetLongMarketValue()).Add(mv))
		} else {
			snap.ShortMarketValue = adapter.D(mustDec(snap.GetShortMarketValue()).Add(mv))
		}
	}
	snap.Cash = adapter.D(s.cash)
	snap.Equity = adapter.D(equity)
	snap.BuyingPower = adapter.D(s.cash)
	return snap, nil
}

// InstrumentInfo implements adapter.Broker.
func (s *Sim) InstrumentInfo(ctx context.Context, inst *commonv1.Instrument, symbol string) (*brokerv1.InstrumentInfo, error) {
	if err := s.sleep(ctx); err != nil {
		return nil, err
	}
	if inst == nil {
		inst = &commonv1.Instrument{Symbol: strings.ToUpper(symbol)}
	}
	tradable := !slices.Contains(s.cfg.RejectSymbols, strings.ToUpper(inst.GetSymbol()))
	return &brokerv1.InstrumentInfo{Instrument: inst, Tradable: tradable, Shortable: true, EasyToBorrow: true, Fractionable: true,
		Marginable: true, MinTick: adapter.D(decimal.RequireFromString("0.01")), LotSize: adapter.D(decimal.NewFromInt(1)), Status: "active", AsOf: s.now()}, nil
}

func mustDec(x *commonv1.Decimal) decimal.Decimal {
	v, _, _ := adapter.Dec(x)
	return v
}
