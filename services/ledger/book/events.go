package book

import (
	"fmt"
	"sort"

	"github.com/shopspring/decimal"
)

// Event kinds. Every ledger change is one of these, stored append-only with a source ID that is
// unique per (account, kind) (VP-I3, invariant 5).
const (
	KindExecution      = "execution"
	KindOpening        = "opening"
	KindSplit          = "split"
	KindDividend       = "dividend"
	KindSymbolChange   = "symbol_change"
	KindRegister       = "register"
	KindAllocation     = "allocation"
	KindTransfer       = "transfer"
	KindCashRebaseline = "cash_rebaseline"
	KindResolve        = "resolve"
	KindManualAdjust   = "manual_adjust"
	// KindFee and KindDelisting are produced by the backtest simcore only (BT-014, BT-008); the live
	// ledger learns about borrow fees and delistings from broker cash and position snapshots.
	KindFee       = "fee"
	KindDelisting = "delisting"
)

// Event is one append-only ledger entry source.
type Event struct {
	Kind    string `json:"kind"`
	Account string `json:"account"`
	Source  string `json:"source"`
	Time    int64  `json:"time"`
	Actor   string `json:"actor,omitempty"`

	Exec     *Exec           `json:"exec,omitempty"`
	Opening  *Opening        `json:"opening,omitempty"`
	Split    *Split          `json:"split,omitempty"`
	Dividend *Dividend       `json:"dividend,omitempty"`
	Symbol   *SymbolChange   `json:"symbol,omitempty"`
	Register *Register       `json:"register,omitempty"`
	Alloc    *Alloc          `json:"alloc,omitempty"`
	Transfer *Transfer       `json:"transfer,omitempty"`
	Cash     *CashRebaseline `json:"cash,omitempty"`
	Resolve  *Resolve        `json:"resolve,omitempty"`
	Adjust   *ManualAdjust   `json:"adjust,omitempty"`
	Fee      *Fee            `json:"fee,omitempty"`
	Delist   *Delisting      `json:"delist,omitempty"`
}

// Fee is a charge to one owner that is not part of a fill (short borrow, BT-014). It is realized
// (owner equity and cash go down) and counted in the owner's fees.
type Fee struct {
	Owner        string          `json:"owner"`
	InstrumentID string          `json:"instrument_id"`
	Amount       decimal.Decimal `json:"amount"`
}

// Delisting closes every owner's position in the instrument at Price (the last traded price after
// the delisting haircut, BT-008), longs and shorts alike, with the cash flow of a closing trade.
type Delisting struct {
	InstrumentID string          `json:"instrument_id"`
	Price        decimal.Decimal `json:"price"`
}

// ManualAdjust books an unexplained broker quantity difference to `manual` without a cash flow
// (transfers in/out, journal entries); only an audited owner resolution produces one (VP-019).
type ManualAdjust struct {
	InstrumentID string          `json:"instrument_id"`
	Symbol       string          `json:"symbol"`
	Qty          decimal.Decimal `json:"qty"`
	Price        decimal.Decimal `json:"price"`
}

// Key is the idempotency key.
func (e Event) Key() string { return e.Account + "|" + e.Kind + "|" + e.Source }

// Exec is one broker execution with its owner resolved at ingest (so replay never depends on a
// later registry change).
type Exec struct {
	ExecID        string          `json:"exec_id"`
	ClientOrderID string          `json:"client_order_id"`
	IntentID      string          `json:"intent_id,omitempty"`
	InstrumentID  string          `json:"instrument_id"`
	Symbol        string          `json:"symbol"`
	Buy           bool            `json:"buy"`
	Qty           decimal.Decimal `json:"qty"`
	Price         decimal.Decimal `json:"price"`
	Fees          decimal.Decimal `json:"fees"`
	Owner         string          `json:"owner"`
	// PlatformManual marks owner orders entered through the platform UI (short ID "manual").
	PlatformManual bool `json:"platform_manual,omitempty"`
	// UnknownShortID is set when a tb1- ID carried an unregistered short ID (VP-017).
	UnknownShortID string `json:"unknown_short_id,omitempty"`
}

// Opening assigns every broker position to `manual` the first time an account is seen (VP-015).
type Opening struct {
	Cash      decimal.Decimal `json:"cash"`
	Positions []OpeningPos    `json:"positions"`
}

// OpeningPos is one opening position.
type OpeningPos struct {
	InstrumentID string          `json:"instrument_id"`
	Symbol       string          `json:"symbol"`
	Qty          decimal.Decimal `json:"qty"`
	AvgCost      decimal.Decimal `json:"avg_cost"`
}

// Split multiplies every owner's quantity by Ratio (new/old) and keeps total cost (VP-010).
type Split struct {
	InstrumentID string          `json:"instrument_id"`
	Ratio        decimal.Decimal `json:"ratio"`
}

// Dividend credits Amount per share to every owner holding the instrument at the ex-date (VP-023).
type Dividend struct {
	InstrumentID string          `json:"instrument_id"`
	Amount       decimal.Decimal `json:"amount"`
}

// SymbolChange records a new ticker for an instrument (position unchanged, VP-010).
type SymbolChange struct {
	InstrumentID string `json:"instrument_id"`
	NewSymbol    string `json:"new_symbol"`
}

// Register adds or updates a strategy owner (VP-012).
type Register struct {
	Owner      string `json:"owner"`
	ShortID    string `json:"short_id"`
	AllowShort bool   `json:"allow_short"`
	Dedicated  bool   `json:"dedicated"`
}

// Alloc sets an owner's allocation (VP-009).
type Alloc struct {
	Owner  string          `json:"owner"`
	Amount decimal.Decimal `json:"amount"`
}

// Transfer moves quantity (with its cost basis, FIFO) between owners; only an audited owner
// resolution produces one (VP-006, VP-019).
type Transfer struct {
	From         string          `json:"from"`
	To           string          `json:"to"`
	InstrumentID string          `json:"instrument_id"`
	Qty          decimal.Decimal `json:"qty"`
}

// CashRebaseline sets the expected cash to the broker's (owner resolution of a cash break).
type CashRebaseline struct {
	Cash decimal.Decimal `json:"cash"`
}

// Resolve closes a break with an owner resolution.
type Resolve struct {
	BreakID    string `json:"break_id"`
	Resolution string `json:"resolution"`
	Note       string `json:"note,omitempty"`
}

// Effects lists what an applied event changed, for persistence and bus events.
type Effects struct {
	Changed      []PosKey
	Breaks       []*Break
	Washes       []WashSale
	Reservations []*Reservation
	Alerts       []string
}

// PosKey is one owner position.
type PosKey struct {
	Account, Owner, InstrumentID string
}

func (fx *Effects) touch(acct, owner, instr string) {
	k := PosKey{acct, owner, instr}
	for _, x := range fx.Changed {
		if x == k {
			return
		}
	}
	fx.Changed = append(fx.Changed, k)
}

// Applied reports whether an event with this key was already applied.
func (b *Book) IsApplied(e Event) bool { return b.Applied[e.Key()] }

// Apply applies e and returns its effects. It is idempotent: an event whose key was applied before
// is a no-op returning (nil, false) (OE-006).
func (b *Book) Apply(e Event, brk *Breaks, res *Reservations) (*Effects, bool, error) {
	if b.Applied[e.Key()] {
		return nil, false, nil
	}
	fx := &Effects{}
	a := b.Account(e.Account)
	var err error
	switch e.Kind {
	case KindExecution:
		err = b.applyExec(a, e, fx, brk, res)
	case KindOpening:
		b.applyOpening(a, e, fx)
	case KindSplit:
		b.applySplit(a, e, fx)
	case KindDividend:
		b.applyDividend(a, e, fx)
	case KindSymbolChange:
		a.Symbols[e.Symbol.InstrumentID] = e.Symbol.NewSymbol
	case KindRegister:
		err = b.applyRegister(a, e)
	case KindAllocation:
		o := a.Owner(e.Alloc.Owner)
		if o == nil {
			return nil, false, fmt.Errorf("ledger: allocation for unknown owner %q", e.Alloc.Owner)
		}
		o.Allocation = e.Alloc.Amount
	case KindTransfer:
		err = b.applyTransfer(a, e, fx)
	case KindManualAdjust:
		m := a.Owner(Manual)
		b.applyFill(m, e.Adjust.InstrumentID, e.Adjust.Qty, e.Adjust.Price, Zero, e.Time, "adjust:"+e.Source)
		a.applyShadow(e.Adjust.InstrumentID, Manual, e.Adjust.Qty, e.Adjust.Price, e.Time, "adjust:"+e.Source, "adjust:"+e.Source)
		if e.Adjust.Symbol != "" {
			a.Symbols[e.Adjust.InstrumentID] = e.Adjust.Symbol
		}
		fx.touch(a.ID, Manual, e.Adjust.InstrumentID)
	case KindCashRebaseline:
		a.Cash, a.CashBaseline = e.Cash.Cash, true
	case KindFee:
		err = b.applyFee(a, e, fx)
	case KindDelisting:
		err = b.applyDelisting(a, e, fx)
	case KindResolve:
		if bk := brk.Get(e.Resolve.BreakID); bk != nil {
			bk.Status, bk.Resolution, bk.ResolvedBy, bk.Resolved = BreakResolved, e.Resolve.Resolution, e.Actor, e.Time
			fx.Breaks = append(fx.Breaks, bk)
		}
	default:
		err = fmt.Errorf("ledger: unknown event kind %q", e.Kind)
	}
	if err != nil {
		return nil, false, err
	}
	b.Applied[e.Key()] = true
	return fx, true, nil
}

func (b *Book) applyRegister(a *Account, e Event) error {
	r := e.Register
	if r.Owner == "" || r.Owner == Manual || r.Owner == Broker {
		return fmt.Errorf("ledger: owner %q is reserved", r.Owner)
	}
	if prev, ok := b.ShortIDs[r.ShortID]; ok && prev != r.Owner {
		return fmt.Errorf("ledger: short ID %q already belongs to %q", r.ShortID, prev)
	}
	o := a.Owner(r.Owner)
	if o == nil {
		o = newOwner(r.Owner)
		a.Owners[r.Owner] = o
	}
	o.ShortID, o.AllowShort, o.Dedicated = r.ShortID, r.AllowShort, r.Dedicated
	b.ShortIDs[r.ShortID] = r.Owner
	return nil
}

func (b *Book) applyOpening(a *Account, e Event, fx *Effects) {
	a.Opened = true
	a.Cash, a.CashBaseline = e.Opening.Cash, true
	m := a.Owner(Manual)
	for _, p := range e.Opening.Positions {
		if p.Qty.IsZero() {
			continue
		}
		a.Symbols[p.InstrumentID] = p.Symbol
		pos := m.position(p.InstrumentID)
		lot := Lot{ID: b.nextID("lot"), Owner: Manual, Qty: p.Qty, Cost: p.Qty.Mul(p.AvgCost), OpenedAt: e.Time, Source: "opening:" + e.Source}
		pos.Lots = append(pos.Lots, lot)
		a.Shadow[p.InstrumentID] = append(a.Shadow[p.InstrumentID], lot)
		fx.touch(a.ID, Manual, p.InstrumentID)
	}
}

// applyExec books one execution for its owner and classifies anything that breaks an ownership rule
// (VP-016 encroachment, VP-004 oversell, VP-017 unknown execution). Executions are always booked as
// the broker reports them: the ledger records truth and raises a break; it never "fixes" numbers.
func (b *Book) applyExec(a *Account, e Event, fx *Effects, brk *Breaks, res *Reservations) error {
	x := e.Exec
	if x.Qty.Sign() <= 0 || x.Price.IsNegative() {
		return fmt.Errorf("ledger: execution %s has qty %s price %s", x.ExecID, x.Qty, x.Price)
	}
	owner := x.Owner
	o := a.Owner(owner)
	if o == nil {
		// The owner was registered in another account; register it here too (same short ID).
		o = newOwner(owner)
		for _, other := range b.Accounts {
			if src := other.Owner(owner); src != nil {
				o.ShortID, o.AllowShort = src.ShortID, src.AllowShort
				break
			}
		}
		a.Owners[owner] = o
	}
	if x.Symbol != "" {
		a.Symbols[x.InstrumentID] = x.Symbol
	}
	q := x.Qty
	if !x.Buy {
		q = q.Neg()
	}
	othersBefore := a.LedgerQty(x.InstrumentID).Sub(o.Qty(x.InstrumentID))
	fr := b.applyFill(o, x.InstrumentID, q, x.Price, x.Fees, e.Time, x.ExecID)
	flow := q.Mul(x.Price).Neg().Sub(x.Fees)
	a.Cash = a.Cash.Add(flow)
	o.CashFlow = o.CashFlow.Add(flow)
	a.Marks[x.InstrumentID] = x.Price
	fx.touch(a.ID, owner, x.InstrumentID)
	lotID := ""
	if pos := o.Positions[x.InstrumentID]; pos != nil && len(pos.Lots) > 0 {
		lotID = pos.Lots[len(pos.Lots)-1].ID
	}
	a.applyShadow(x.InstrumentID, owner, q, x.Price, e.Time, x.ExecID, lotID)

	// Reservations shrink as their intent fills (VP-020).
	if r := res.ByIntent(x.IntentID); r != nil && !r.Remaining.IsZero() {
		r.Remaining = decimal.Max(Zero, r.Remaining.Sub(x.Qty))
		fx.Reservations = append(fx.Reservations, r)
	}

	b.washSale(a, x, owner, e.Time, fr, fx)

	switch {
	case x.UnknownShortID != "":
		bk := brk.Open(Break{ID: "brk-" + e.Account + "-" + x.ExecID, Account: a.ID, Type: BreakUnknownExecution, InstrumentID: x.InstrumentID,
			Symbol: x.Symbol, Severity: SeverityCritical, Detected: e.Time, Source: x.ExecID,
			Detail:      fmt.Sprintf("execution %s carries unregistered strategy short ID %q; booked to manual", x.ExecID, x.UnknownShortID),
			Suggestions: []string{ResolutionAccept}, InstrumentWide: true})
		fx.Breaks = append(fx.Breaks, bk)
	case owner == Manual && !x.Buy && fr.After.IsNegative() && fr.After.LessThan(fr.Before):
		// VP-016: a manual sell beyond the manual quantity consumed strategy-attributed shares.
		var affected []string
		for _, id := range a.OwnerIDs() {
			if id != Manual && a.Owners[id].Qty(x.InstrumentID).IsPositive() {
				affected = append(affected, id)
			}
		}
		if len(affected) > 0 {
			bk := brk.Open(Break{ID: "brk-" + e.Account + "-" + x.ExecID, Account: a.ID, Type: BreakEncroachment, InstrumentID: x.InstrumentID,
				Symbol: x.Symbol, Severity: SeverityCritical, Detected: e.Time, Source: x.ExecID, Affected: affected,
				Detail:      fmt.Sprintf("manual sell of %s left manual at %s while %v hold the instrument", x.Qty, fr.After, affected),
				Suggestions: []string{ResolutionTransferToManual}})
			fx.Breaks = append(fx.Breaks, bk)
			fx.Alerts = append(fx.Alerts, "encroachment:"+x.InstrumentID)
		}
	case owner != Manual && fr.After.IsNegative() && fr.After.LessThan(fr.Before) &&
		(!o.AllowShort || !o.Dedicated || !othersBefore.IsZero()):
		// VP-004/VP-005: the gate should make this impossible; record it and halt.
		bk := brk.Open(Break{ID: "brk-" + e.Account + "-" + x.ExecID, Account: a.ID, Type: BreakOversell, InstrumentID: x.InstrumentID,
			Symbol: x.Symbol, Severity: SeverityCritical, Detected: e.Time, Source: x.ExecID, Affected: []string{owner},
			Detail:      fmt.Sprintf("%s sold %s and is at %s (shorting not allowed here)", owner, x.Qty, fr.After),
			Suggestions: []string{ResolutionAccept}})
		fx.Breaks = append(fx.Breaks, bk)
	}
	return nil
}

const washWindowNS = int64(30 * 24 * 3600 * 1e9)

// washSale remembers buys and flags loss-realizing sales within 30 days of a purchase of the same
// instrument by any owner in any account (VP-022). Informational only.
func (b *Book) washSale(a *Account, x *Exec, owner string, at int64, fr FillResult, fx *Effects) {
	if x.Buy {
		b.Buys[x.InstrumentID] = append(b.Buys[x.InstrumentID], BuyRecord{Account: a.ID, Owner: owner, Source: x.ExecID, Time: at})
		return
	}
	if !fr.Realized.IsNegative() {
		return
	}
	relieved := map[string]bool{}
	for _, l := range fr.LossLots {
		relieved[l.Source] = true
	}
	for _, buy := range b.Buys[x.InstrumentID] {
		if relieved[buy.Source] || buy.Time > at+washWindowNS || buy.Time < at-washWindowNS {
			continue
		}
		w := WashSale{ID: fmt.Sprintf("wash-%s-%s", x.ExecID, buy.Source), InstrumentID: x.InstrumentID, SellAccount: a.ID, SellOwner: owner,
			SellSource: x.ExecID, SellTime: at, Loss: fr.Realized.Neg(), BuyAccount: buy.Account, BuyOwner: buy.Owner, BuySource: buy.Source, BuyTime: buy.Time}
		b.Washes = append(b.Washes, w)
		fx.Washes = append(fx.Washes, w)
		return
	}
}

func (b *Book) applySplit(a *Account, e Event, fx *Effects) {
	s := e.Split
	if !s.Ratio.IsPositive() {
		return
	}
	for _, id := range a.OwnerIDs() {
		pos := a.Owners[id].Positions[s.InstrumentID]
		if pos == nil {
			continue
		}
		for i := range pos.Lots {
			pos.Lots[i].Qty = pos.Lots[i].Qty.Mul(s.Ratio)
		}
		fx.touch(a.ID, id, s.InstrumentID)
	}
	for i := range a.Shadow[s.InstrumentID] {
		a.Shadow[s.InstrumentID][i].Qty = a.Shadow[s.InstrumentID][i].Qty.Mul(s.Ratio)
	}
	if m, ok := a.Marks[s.InstrumentID]; ok {
		a.Marks[s.InstrumentID] = m.DivRound(s.Ratio, 8)
	}
}

func (b *Book) applyDividend(a *Account, e Event, fx *Effects) {
	d := e.Dividend
	for _, id := range a.OwnerIDs() {
		o := a.Owners[id]
		q := o.Qty(d.InstrumentID)
		if q.IsZero() {
			continue
		}
		cash := q.Mul(d.Amount)
		o.Dividends = o.Dividends.Add(cash)
		o.CashFlow = o.CashFlow.Add(cash)
		a.Cash = a.Cash.Add(cash)
		fx.touch(a.ID, id, d.InstrumentID)
	}
}

func (b *Book) applyFee(a *Account, e Event, fx *Effects) error {
	f := e.Fee
	o := a.Owner(f.Owner)
	if o == nil {
		return fmt.Errorf("ledger: fee for unknown owner %q", f.Owner)
	}
	if f.Amount.IsNegative() {
		return fmt.Errorf("ledger: fee %s is negative", f.Amount)
	}
	pos := o.position(f.InstrumentID)
	pos.Realized = pos.Realized.Sub(f.Amount)
	o.Realized = o.Realized.Sub(f.Amount)
	o.Fees = o.Fees.Add(f.Amount)
	o.CashFlow = o.CashFlow.Sub(f.Amount)
	a.Cash = a.Cash.Sub(f.Amount)
	fx.touch(a.ID, o.ID, f.InstrumentID)
	return nil
}

func (b *Book) applyDelisting(a *Account, e Event, fx *Effects) error {
	d := e.Delist
	if d.Price.IsNegative() {
		return fmt.Errorf("ledger: delisting price %s is negative", d.Price)
	}
	for _, id := range a.OwnerIDs() {
		o := a.Owners[id]
		q := o.Qty(d.InstrumentID)
		if q.IsZero() {
			continue
		}
		src := "delist:" + e.Source + ":" + id
		b.applyFill(o, d.InstrumentID, q.Neg(), d.Price, Zero, e.Time, src)
		a.applyShadow(d.InstrumentID, id, q.Neg(), d.Price, e.Time, src, src)
		flow := q.Mul(d.Price)
		a.Cash = a.Cash.Add(flow)
		o.CashFlow = o.CashFlow.Add(flow)
		fx.touch(a.ID, id, d.InstrumentID)
	}
	a.Marks[d.InstrumentID] = d.Price
	return nil
}

func (b *Book) applyTransfer(a *Account, e Event, fx *Effects) error {
	t := e.Transfer
	from, to := a.Owner(t.From), a.Owner(t.To)
	if from == nil || to == nil {
		return fmt.Errorf("ledger: transfer between unknown owners %q → %q", t.From, t.To)
	}
	if !t.Qty.IsPositive() || from.Qty(t.InstrumentID).LessThan(t.Qty) {
		return fmt.Errorf("ledger: %s holds %s, cannot transfer %s", t.From, from.Qty(t.InstrumentID), t.Qty)
	}
	src := from.position(t.InstrumentID)
	dst := to.position(t.InstrumentID)
	remaining := t.Qty
	for remaining.IsPositive() && len(src.Lots) > 0 {
		lot := &src.Lots[0]
		m := decimal.Min(lot.Qty, remaining)
		cost := lot.Cost
		if !m.Equal(lot.Qty) {
			cost = lot.Cost.Mul(m).DivRound(lot.Qty, costScale)
		}
		dst.Lots = append(dst.Lots, Lot{ID: b.nextID("lot"), Owner: to.ID, Qty: m, Cost: cost, OpenedAt: lot.OpenedAt, Source: "transfer:" + e.Source})
		lot.Qty, lot.Cost = lot.Qty.Sub(m), lot.Cost.Sub(cost)
		remaining = remaining.Sub(m)
		if lot.Qty.IsZero() {
			src.Lots = src.Lots[1:]
		}
	}
	// A transfer onto a negative position nets it (e.g. manual -20 receiving 20 → 0).
	if to.Qty(t.InstrumentID).IsZero() || allSameSign(dst.Lots) {
		sortLots(dst.Lots)
	} else {
		netLots(dst)
	}
	fx.touch(a.ID, t.From, t.InstrumentID)
	fx.touch(a.ID, t.To, t.InstrumentID)
	return nil
}

func allSameSign(lots []Lot) bool {
	for i := 1; i < len(lots); i++ {
		if lots[i].Qty.Sign() != lots[0].Qty.Sign() {
			return false
		}
	}
	return true
}

func sortLots(lots []Lot) {
	sort.SliceStable(lots, func(i, j int) bool { return lots[i].OpenedAt < lots[j].OpenedAt })
}

// netLots offsets opposite-signed lots inside one position (FIFO), with no realized P&L: a transfer
// moves cost basis, it does not trade.
func netLots(p *Position) {
	var longs, shorts []Lot
	for _, l := range p.Lots {
		if l.Qty.IsPositive() {
			longs = append(longs, l)
		} else if l.Qty.IsNegative() {
			shorts = append(shorts, l)
		}
	}
	for len(longs) > 0 && len(shorts) > 0 {
		l, s := &longs[0], &shorts[0]
		m := decimal.Min(l.Qty, s.Qty.Abs())
		lc := l.Cost.Mul(m).DivRound(l.Qty, costScale)
		sc := s.Cost.Mul(m).DivRound(s.Qty.Abs(), costScale)
		if m.Equal(l.Qty) {
			lc = l.Cost
		}
		if m.Equal(s.Qty.Abs()) {
			sc = s.Cost
		}
		l.Qty, l.Cost = l.Qty.Sub(m), l.Cost.Sub(lc)
		s.Qty, s.Cost = s.Qty.Add(m), s.Cost.Sub(sc)
		if l.Qty.IsZero() {
			longs = longs[1:]
		}
		if s.Qty.IsZero() {
			shorts = shorts[1:]
		}
	}
	p.Lots = append(longs, shorts...)
	sortLots(p.Lots)
}
