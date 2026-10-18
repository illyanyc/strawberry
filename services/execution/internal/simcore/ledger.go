package simcore

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/ledger/book"
)

// Ledger implements oms.Ledger over the ledger's accounting core (services/ledger/book), so a
// simulated fill is booked by the same FIFO-lot, ownership, reservation, and capital code as a live
// one (BT-003, VP-003..VP-005, VP-011, VP-020). It mirrors the ledger engine's thin wrappers:
// owner attribution from the client order ID, day P&L from the session's first equity
// observation, and the high-water mark.
type Ledger struct {
	b     *book.Book
	brk   *book.Breaks
	res   *book.Reservations
	cfg   book.CapitalConfig
	snaps map[string]*book.Snapshot
	eq    map[string]*equityState
	cal   *calendar.Calendar
	now   func() int64
	seq   int
}

type equityState struct {
	date     string
	dayStart decimal.Decimal
	hwm      decimal.Decimal
}

func newLedger(cal *calendar.Calendar, now func() int64) *Ledger {
	return &Ledger{b: book.New(), brk: book.NewBreaks(), res: book.NewReservations(), cfg: book.DefaultCapitalConfig(),
		snaps: map[string]*book.Snapshot{}, eq: map[string]*equityState{}, cal: cal, now: now}
}

func dec(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }

func pd(x *commonv1.Decimal) decimal.Decimal {
	if x == nil || x.GetValue() == "" {
		return decimal.Zero
	}
	v, err := decimal.NewFromString(x.GetValue())
	if err != nil {
		return decimal.Zero
	}
	return v
}

// apply applies one event and converts its effects into a LedgerUpdate (as the ledger engine
// publishes on ledger.update.<acct>).
func (l *Ledger) apply(ev book.Event) (*ledgerv1.LedgerUpdate, error) {
	fx, ok, err := l.b.Apply(ev, l.brk, l.res)
	if err != nil || !ok {
		return nil, err
	}
	upd := &ledgerv1.LedgerUpdate{BrokerAccountId: ev.Account, EventId: ev.Key(), SourceId: ev.Source, AsOf: ev.Time}
	for _, k := range fx.Changed {
		o := l.b.Accounts[k.Account].Owner(k.Owner)
		q, c, r := book.Zero, book.Zero, book.Zero
		if p := o.Positions[k.InstrumentID]; p != nil {
			q, c, r = p.Qty(), p.Cost(), p.Realized
		}
		upd.Positions = append(upd.Positions, &ledgerv1.StrategyPosition{BrokerAccountId: k.Account, Owner: k.Owner, InstrumentId: k.InstrumentID,
			Qty: dec(q), CostBasis: dec(c), RealizedPnl: dec(r), AsOf: ev.Time})
	}
	l.observe()
	if len(upd.Positions) == 0 {
		return nil, nil
	}
	return upd, nil
}

// ownerFor attributes an execution exactly as the ledger engine does (VP-001, VP-017).
func (l *Ledger) ownerFor(clientOrderID string) (owner string, platformManual bool, unknownShort, intent string) {
	if !attribution.IsPlatform(clientOrderID) {
		return book.Manual, false, "", ""
	}
	id, err := attribution.Parse(clientOrderID, 0)
	if err != nil {
		return book.Manual, false, "malformed", ""
	}
	if id.Manual() {
		return book.Manual, true, "", id.IntentID
	}
	if o := l.b.OwnerOf(id.StrategyShortID); o != "" {
		return o, false, "", id.IntentID
	}
	return book.Manual, false, id.StrategyShortID, id.IntentID
}

func (l *Ledger) execution(x *ordersv1.ExecutionReport) (*ledgerv1.LedgerUpdate, error) {
	owner, pm, unknown, intent := l.ownerFor(x.GetClientOrderId())
	return l.apply(book.Event{Kind: book.KindExecution, Account: x.GetBrokerAccountId(), Source: x.GetBrokerExecId(), Time: x.GetEventTime(),
		Exec: &book.Exec{ExecID: x.GetBrokerExecId(), ClientOrderID: x.GetClientOrderId(), IntentID: intent, InstrumentID: x.GetInstrumentId(),
			Symbol: x.GetSymbol(), Buy: x.GetSide() == commonv1.Side_SIDE_BUY, Qty: pd(x.GetQty()), Price: pd(x.GetPrice()), Fees: pd(x.GetFees()),
			Owner: owner, PlatformManual: pm, UnknownShortID: unknown}})
}

// mark sets the account marks (latest prices) used for valuation.
func (l *Ledger) mark(instr string, px decimal.Decimal) {
	for _, a := range l.b.Accounts {
		a.Marks[instr] = px
	}
}

// snapshot derives the account snapshot the broker simulator of the paper harness would report:
// cash, positions at the marks, equity, and buying power = cash (services/broker-gateway sim).
func (l *Ledger) snapshot(acct string) *ledgerv1.AccountSnapshot {
	a := l.b.Accounts[acct]
	s := &ledgerv1.AccountSnapshot{BrokerAccountId: acct, Mode: commonv1.Mode_MODE_BACKTEST, AsOf: l.now(), Currency: "USD", DayTradeCount: -1, Status: "ACTIVE"}
	if a == nil {
		return s
	}
	eq, lmv, smv := a.Cash, book.Zero, book.Zero
	bs := &book.Snapshot{AsOf: s.AsOf, Cash: a.Cash, BuyingPower: a.Cash}
	for _, instr := range a.Instruments() {
		q := a.LedgerQty(instr)
		if q.IsZero() {
			continue
		}
		mark, ok := a.Marks[instr]
		if !ok {
			continue
		}
		mv := q.Mul(mark)
		eq = eq.Add(mv)
		if mv.IsNegative() {
			smv = smv.Add(mv)
		} else {
			lmv = lmv.Add(mv)
		}
		s.Positions = append(s.Positions, &ledgerv1.Position{InstrumentId: instr, Qty: dec(q), MarketValue: dec(mv), AvgCost: dec(mark)})
		bs.Positions = append(bs.Positions, book.SnapPos{InstrumentID: instr, Qty: q, MarketValue: mv, AvgCost: mark})
	}
	s.Cash, s.BuyingPower, s.Equity, s.LongMarketValue, s.ShortMarketValue = dec(a.Cash), dec(a.Cash), dec(eq), dec(lmv), dec(smv)
	bs.Equity = eq
	l.snaps[acct] = bs
	return s
}

// observe updates every owner's session start equity and high-water mark (RM-005; the ledger
// engine's equity state: the first observation of a session is its baseline).
func (l *Ledger) observe() {
	sess, _, ok := l.cal.SessionAt(time.Unix(0, l.now()))
	date := ""
	if ok {
		date = sess.Date
	}
	for _, acct := range l.b.AccountIDs() {
		a := l.b.Accounts[acct]
		for _, id := range a.OwnerIDs() {
			v := a.Value(a.Owners[id])
			k := acct + "|" + id
			st := l.eq[k]
			switch {
			case st == nil:
				l.eq[k] = &equityState{date: date, dayStart: v.Equity, hwm: v.Equity}
			case date != "" && date != st.date:
				st.date, st.dayStart = date, v.Equity
			}
			if st := l.eq[k]; v.Equity.GreaterThan(st.hwm) {
				st.hwm = v.Equity
			}
		}
	}
}

func (l *Ledger) accountFor(owner string) string {
	for _, id := range l.b.AccountIDs() {
		if o := l.b.Accounts[id].Owner(owner); o != nil && (owner != book.Manual || len(l.b.AccountIDs()) == 1) {
			return id
		}
	}
	return ""
}

// Constraints implements oms.Ledger.
func (l *Ledger) Constraints(_ context.Context, acct, instr, owner string) (*ledgerv1.OwnershipConstraints, error) {
	var bq *decimal.Decimal
	if s := l.snaps[acct]; s != nil {
		q := s.Qty(instr)
		bq = &q
	}
	c := l.b.Constraints(acct, instr, owner, bq, l.brk, l.res)
	return &ledgerv1.OwnershipConstraints{StrategyQty: dec(c.StrategyQty), OtherOwnersQty: dec(c.OtherOwnersQty), AccountQty: dec(c.AccountQty),
		MaxSellableByStrategy: dec(c.MaxSellable), ShortAllowed: c.ShortAllowed, Reason: c.Reason, ManualQty: dec(c.ManualQty), Halted: c.Halted}, nil
}

func capProto(c book.Capital) *ledgerv1.CapitalAvailability {
	return &ledgerv1.CapitalAvailability{Allocation: dec(c.Allocation), CashAvailable: dec(c.CashAvailable), BuyingPowerAvailable: dec(c.BuyingPowerAvailable),
		ReservedForOpenOrders: dec(c.Reserved)}
}

// Reserve implements oms.Ledger (VP-020). A business rejection is a normal response.
func (l *Ledger) Reserve(_ context.Context, r *ledgerv1.ReserveForOrderRequest) (*ledgerv1.ReserveForOrderResponse, error) {
	side := r.GetSide()
	if x := l.res.ByIntent(r.GetIntentId()); x != nil {
		return &ledgerv1.ReserveForOrderResponse{Reserved: true, ReservationId: x.ID, Capital: capProto(l.b.Capital(r.GetBrokerAccountId(), r.GetOwner(), l.snaps[r.GetBrokerAccountId()], l.cfg, l.res))}, nil
	}
	l.seq++
	rv := book.Reservation{ID: fmt.Sprintf("rsv-%d", l.seq), IntentID: r.GetIntentId(), Account: r.GetBrokerAccountId(), Owner: r.GetOwner(),
		InstrumentID: r.GetInstrumentId(), Buy: side == commonv1.Side_SIDE_BUY, Qty: pd(r.GetQty()), EstPrice: pd(r.GetEstPrice()), CreatedAt: l.now()}
	x, err := l.b.Reserve(rv, l.snaps[rv.Account], l.cfg, l.brk, l.res, side == commonv1.Side_SIDE_SELL_SHORT)
	capital := capProto(l.b.Capital(rv.Account, rv.Owner, l.snaps[rv.Account], l.cfg, l.res))
	if err != nil {
		return &ledgerv1.ReserveForOrderResponse{Reserved: false, RejectReason: err.Error(), Capital: capital}, nil
	}
	l.res.Put(x)
	return &ledgerv1.ReserveForOrderResponse{Reserved: true, ReservationId: x.ID, Capital: capProto(l.b.Capital(rv.Account, rv.Owner, l.snaps[rv.Account], l.cfg, l.res))}, nil
}

// Release implements oms.Ledger.
func (l *Ledger) Release(_ context.Context, intentID string) error {
	if x := l.res.ByIntent(intentID); x != nil {
		l.res.Delete(x)
	}
	return nil
}

// Portfolio implements oms.Ledger: the owner view the ledger engine serves (ownerView).
func (l *Ledger) Portfolio(_ context.Context, owner string) (*ledgerv1.OwnerView, error) {
	acct := l.accountFor(owner)
	if acct == "" {
		return nil, fmt.Errorf("simcore: owner %q not registered", owner)
	}
	return l.ownerView(acct, owner, true), nil
}

// ownerView mirrors the ledger engine's owner view. openOnly omits closed positions (realized P&L
// only), which the OMS skips anyway; queries for reports ask for the full view.
func (l *Ledger) ownerView(acct, owner string, openOnly bool) *ledgerv1.OwnerView {
	a := l.b.Accounts[acct]
	o := a.Owner(owner)
	if o == nil {
		return &ledgerv1.OwnerView{Owner: owner}
	}
	v := a.Value(o)
	c := l.b.Capital(acct, owner, l.snaps[acct], l.cfg, l.res)
	out := &ledgerv1.OwnerView{Owner: o.ID, ShortId: o.ShortID, Allocation: dec(o.Allocation), CashAvailable: dec(c.CashAvailable), Reserved: dec(c.Reserved),
		Equity: dec(v.Equity), RealizedPnl: dec(o.Realized), UnrealizedPnl: dec(v.Unrealized), Fees: dec(o.Fees), Dividends: dec(o.Dividends),
		GrossExposure: dec(v.Gross), NetExposure: dec(v.Net), AllowShort: o.AllowShort, DedicatedAccount: o.Dedicated, DayPnl: dec(book.Zero), HighWaterMark: dec(v.Equity)}
	if st := l.eq[acct+"|"+owner]; st != nil {
		out.DayPnl = dec(v.Equity.Sub(st.dayStart))
		out.HighWaterMark = dec(decimal.Max(st.hwm, v.Equity))
	}
	ids := make([]string, 0, len(o.Positions))
	for id, p := range o.Positions {
		if !p.Qty().IsZero() || (!openOnly && !p.Realized.IsZero()) {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	for _, id := range ids {
		p := o.Positions[id]
		q := p.Qty()
		mark, ok := a.Marks[id]
		avg := book.Zero
		if !q.IsZero() {
			avg = p.Cost().DivRound(q, 6)
			if !ok {
				mark = avg
			}
		}
		out.Positions = append(out.Positions, &ledgerv1.OwnerPosition{InstrumentId: id, Symbol: a.Symbols[id], Qty: dec(q), CostBasis: dec(p.Cost()), AvgCost: dec(avg),
			MarkPrice: dec(mark), MarketValue: dec(q.Mul(mark)), UnrealizedPnl: dec(q.Mul(mark).Sub(p.Cost())), RealizedPnl: dec(p.Realized)})
	}
	return out
}

// RegisterOwner implements oms.Ledger (VP-012).
func (l *Ledger) RegisterOwner(_ context.Context, r *ledgerv1.RegisterOwnerRequest) error {
	_, err := l.apply(book.Event{Kind: book.KindRegister, Account: r.GetBrokerAccountId(), Source: "register:" + r.GetOwner(), Time: l.now(),
		Register: &book.Register{Owner: r.GetOwner(), ShortID: r.GetShortId(), AllowShort: r.GetAllowShort(), Dedicated: r.GetDedicatedAccount()}})
	return err
}

// SetAllocation implements oms.Ledger (VP-009): checked against the allocatable capital.
func (l *Ledger) SetAllocation(_ context.Context, r *ledgerv1.SetAllocationRequest) error {
	acct := r.GetBrokerAccountId()
	if acct == "" {
		acct = l.accountFor(r.GetOwner())
	}
	amt := pd(r.GetAmount())
	if err := l.b.CheckAllocation(acct, r.GetOwner(), amt, l.snaps[acct], l.cfg, l.res); err != nil {
		return err
	}
	prev := book.Zero
	if o := l.b.Accounts[acct].Owner(r.GetOwner()); o != nil {
		prev = o.Allocation
	}
	// RM-005, VP-024 (ledger engine shiftDayStart): a capital flow is never day P&L or drawdown.
	if st := l.eq[acct+"|"+r.GetOwner()]; st != nil {
		delta := amt.Sub(prev)
		st.dayStart, st.hwm = st.dayStart.Add(delta), st.hwm.Add(delta)
	}
	l.seq++
	_, err := l.apply(book.Event{Kind: book.KindAllocation, Account: acct, Source: fmt.Sprintf("alloc:%s:%d", r.GetOwner(), l.seq), Time: l.now(),
		Alloc: &book.Alloc{Owner: r.GetOwner(), Amount: amt}})
	return err
}
