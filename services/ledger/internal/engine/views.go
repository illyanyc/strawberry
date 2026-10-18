package engine

import (
	"context"
	"sort"

	"github.com/shopspring/decimal"

	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	"github.com/illyanyc/strawberry/services/ledger/internal/book"
)

var breakTypes = map[string]ledgerv1.BreakType{
	book.BreakTiming: ledgerv1.BreakType_BREAK_TYPE_TIMING, book.BreakEncroachment: ledgerv1.BreakType_BREAK_TYPE_ENCROACHMENT,
	book.BreakUnknownExecution: ledgerv1.BreakType_BREAK_TYPE_UNKNOWN_EXECUTION, book.BreakMissingExecution: ledgerv1.BreakType_BREAK_TYPE_MISSING_EXECUTION,
	book.BreakCorporateAction: ledgerv1.BreakType_BREAK_TYPE_CORPORATE_ACTION, book.BreakCashMismatch: ledgerv1.BreakType_BREAK_TYPE_CASH_MISMATCH,
	book.BreakOrphanOrder: ledgerv1.BreakType_BREAK_TYPE_ORPHAN_ORDER, book.BreakGhostOrder: ledgerv1.BreakType_BREAK_TYPE_GHOST_ORDER,
	book.BreakOversell: ledgerv1.BreakType_BREAK_TYPE_OVERSELL, book.BreakUnmappedInstrument: ledgerv1.BreakType_BREAK_TYPE_UNMAPPED_INSTRUMENT,
}

func breakProto(b *book.Break) *ledgerv1.Break {
	st := ledgerv1.BreakStatus_BREAK_STATUS_OPEN
	switch b.Status {
	case book.BreakAutoResolved:
		st = ledgerv1.BreakStatus_BREAK_STATUS_AUTO_RESOLVED
	case book.BreakResolved:
		st = ledgerv1.BreakStatus_BREAK_STATUS_RESOLVED
	}
	out := &ledgerv1.Break{BreakId: b.ID, BrokerAccountId: b.Account, Type: breakTypes[b.Type], Status: st, InstrumentId: b.InstrumentID, Symbol: b.Symbol,
		BrokerQty: dec(b.BrokerQty), LedgerQty: dec(b.LedgerQty), AffectedOwners: b.Affected, Detail: b.Detail, DetectedAt: b.Detected,
		ResolvedAt: b.Resolved, ResolvedBy: b.ResolvedBy, Resolution: b.Resolution, SuggestedResolutions: b.Suggestions, Severity: b.Severity, SourceId: b.Source}
	for _, o := range sortedKeys(b.Owners) {
		out.OwnerBreakdown = append(out.OwnerBreakdown, &ledgerv1.OwnerQty{Owner: o, Qty: dec(b.Owners[o])})
	}
	return out
}

func lotProto(acct string, l book.Lot, instr string) *ledgerv1.Lot {
	return &ledgerv1.Lot{LotId: l.ID, BrokerAccountId: acct, Owner: l.Owner, InstrumentId: instr, Qty: dec(l.Qty), Cost: dec(l.Cost), OpenedAt: l.OpenedAt, SourceId: l.Source}
}

func (e *Engine) ownerView(a *book.Account, o *book.Owner) *ledgerv1.OwnerView {
	v := a.Value(o)
	cap := e.b.Capital(a.ID, o.ID, e.snaps[a.ID], e.cfg.Capital, e.rsv)
	out := &ledgerv1.OwnerView{Owner: o.ID, ShortId: o.ShortID, Allocation: dec(o.Allocation), CashAvailable: dec(cap.CashAvailable), Reserved: dec(cap.Reserved),
		Equity: dec(v.Equity), RealizedPnl: dec(o.Realized), UnrealizedPnl: dec(v.Unrealized), Fees: dec(o.Fees), Dividends: dec(o.Dividends),
		GrossExposure: dec(v.Gross), NetExposure: dec(v.Net), AllowShort: o.AllowShort, DedicatedAccount: o.Dedicated, DayPnl: dec(book.Zero), HighWaterMark: dec(v.Equity)}
	if st := e.equity[a.ID+"|"+o.ID]; st != nil {
		out.DayPnl = dec(v.Equity.Sub(st.dayStart))
		out.HighWaterMark = dec(decimal.Max(st.hwm, v.Equity))
	}
	instrs := make([]string, 0, len(o.Positions))
	for id, p := range o.Positions {
		if !p.Qty().IsZero() || !p.Realized.IsZero() {
			instrs = append(instrs, id)
		}
	}
	sort.Strings(instrs)
	for _, id := range instrs {
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
		pos := &ledgerv1.OwnerPosition{InstrumentId: id, Symbol: a.Symbols[id], Qty: dec(q), CostBasis: dec(p.Cost()), AvgCost: dec(avg), MarkPrice: dec(mark),
			MarketValue: dec(q.Mul(mark)), UnrealizedPnl: dec(q.Mul(mark).Sub(p.Cost())), RealizedPnl: dec(p.Realized), Halted: e.brk.Halted(a.ID, o.ID, id)}
		for _, l := range p.Lots {
			pos.Lots = append(pos.Lots, lotProto(a.ID, l, id))
		}
		out.Positions = append(out.Positions, pos)
	}
	return out
}

// AccountView is the full real account split by owner (section 06 GetAccountView).
func (e *Engine) AccountView(acct string) *ledgerv1.AccountView {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.accountViewLocked(acct)
}

func (e *Engine) accountViewLocked(acct string) *ledgerv1.AccountView {
	a := e.b.Accounts[acct]
	if a == nil {
		return nil
	}
	snap := e.snaps[acct]
	alloc := book.Allocatable(snap, e.cfg.Capital)
	reserve := book.Zero
	if snap != nil {
		reserve = snap.Equity.Sub(alloc)
	}
	v := &ledgerv1.AccountView{BrokerAccountId: acct, BrokerSnapshot: e.rawSnaps[acct], AllocatableCapital: dec(alloc), Reserve: dec(reserve),
		UnallocatedCash: dec(alloc.Sub(e.b.TotalAllocated(acct, ""))), LedgerCash: dec(a.Cash)}
	for _, id := range a.OwnerIDs() {
		ov := e.ownerView(a, a.Owners[id])
		if id == book.Manual {
			v.Manual = ov
		} else {
			v.Owners = append(v.Owners, ov)
		}
	}
	if r := e.lastRecon[acct]; r != nil {
		v.ReconStatus, v.ReconAsOf = r.GetStatus(), r.GetAsOf()
	}
	set := map[string]bool{}
	for _, id := range a.Instruments() {
		set[id] = true
	}
	if snap != nil {
		for _, p := range snap.Positions {
			set[p.InstrumentID] = true
		}
	}
	for _, id := range sortedKeys(set) {
		ib := &ledgerv1.InstrumentBreakdown{InstrumentId: id, Symbol: a.Symbols[id], LedgerQty: dec(a.LedgerQty(id)), BrokerQty: dec(book.Zero)}
		if snap != nil {
			ib.BrokerQty = dec(snap.Qty(id))
		}
		for _, oid := range a.OwnerIDs() {
			if q := a.Owners[oid].Qty(id); !q.IsZero() {
				ib.Owners = append(ib.Owners, &ledgerv1.OwnerQty{Owner: oid, Qty: dec(q)})
			}
		}
		for _, l := range a.Shadow[id] {
			ib.BrokerLots = append(ib.BrokerLots, lotProto(acct, l, id))
		}
		v.Instruments = append(v.Instruments, ib)
	}
	for _, b := range e.brk.List(acct, true) {
		v.OpenBreaks = append(v.OpenBreaks, breakProto(b))
	}
	return v
}

// Accounts returns every account view.
func (e *Engine) Accounts() []*ledgerv1.AccountView {
	e.mu.RLock()
	defer e.mu.RUnlock()
	var out []*ledgerv1.AccountView
	for _, id := range e.b.AccountIDs() {
		out = append(out, e.accountViewLocked(id))
	}
	return out
}

// StrategyPortfolio returns one strategy's virtual portfolio (first account it is registered in, or
// acct when given).
func (e *Engine) StrategyPortfolio(owner, acct string) (string, *ledgerv1.OwnerView) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	for _, id := range e.b.AccountIDs() {
		if acct != "" && id != acct {
			continue
		}
		a := e.b.Accounts[id]
		if o := a.Owner(owner); o != nil {
			return id, e.ownerView(a, o)
		}
	}
	return "", nil
}

// Breaks lists breaks.
func (e *Engine) Breaks(acct string, openOnly bool) []*ledgerv1.Break {
	e.mu.RLock()
	defer e.mu.RUnlock()
	var out []*ledgerv1.Break
	for _, b := range e.brk.List(acct, openOnly) {
		out = append(out, breakProto(b))
	}
	return out
}

// Lots lists owner lots, or broker shadow lots for owner "broker" (VP-021).
func (e *Engine) Lots(acct, owner, instr string) []*ledgerv1.Lot {
	e.mu.RLock()
	defer e.mu.RUnlock()
	a := e.b.Accounts[acct]
	if a == nil {
		return nil
	}
	var out []*ledgerv1.Lot
	if owner == book.Broker {
		for _, id := range sortedKeys(a.Shadow) {
			if instr == "" || id == instr {
				for _, l := range a.Shadow[id] {
					out = append(out, lotProto(acct, l, id))
				}
			}
		}
		return out
	}
	for _, oid := range a.OwnerIDs() {
		if owner != "" && oid != owner {
			continue
		}
		o := a.Owners[oid]
		for _, id := range sortedKeys(o.Positions) {
			if instr == "" || id == instr {
				for _, l := range o.Positions[id].Lots {
					out = append(out, lotProto(acct, l, id))
				}
			}
		}
	}
	return out
}

// EquityCurve returns stored equity points.
func (e *Engine) EquityCurve(ctx context.Context, acct, owner string, start, end int64) ([]*ledgerv1.EquityPoint, error) {
	if acct == "" {
		acct = e.AccountForOwner(owner)
	}
	rows, err := e.store.Equity(ctx, acct, owner, start, end)
	if err != nil {
		return nil, err
	}
	out := make([]*ledgerv1.EquityPoint, 0, len(rows))
	for _, r := range rows {
		out = append(out, &ledgerv1.EquityPoint{Ts: r.TS, Equity: dec(r.Equity), Cash: dec(r.Cash), MarketValue: dec(r.MarketValue),
			RealizedPnl: dec(r.Realized), UnrealizedPnl: dec(r.Unrealized), GrossExposure: dec(r.Gross)})
	}
	return out, nil
}

// WashSales returns wash-sale flags.
func (e *Engine) WashSales(ctx context.Context, since int64) ([]*ledgerv1.WashSaleFlag, error) {
	ws, err := e.store.Washes(ctx, since)
	if err != nil {
		return nil, err
	}
	out := make([]*ledgerv1.WashSaleFlag, 0, len(ws))
	for _, w := range ws {
		out = append(out, &ledgerv1.WashSaleFlag{FlagId: w.ID, InstrumentId: w.InstrumentID, SellOwner: w.SellOwner, SellAccount: w.SellAccount,
			SellSourceId: w.SellSource, Loss: dec(w.Loss), BuyOwner: w.BuyOwner, BuyAccount: w.BuyAccount, BuySourceId: w.BuySource, SellTime: w.SellTime, BuyTime: w.BuyTime})
	}
	return out, nil
}

// Reports returns stored reconciliation reports.
func (e *Engine) Reports(ctx context.Context, acct string, since int64, limit int) ([]*ledgerv1.ReconciliationResult, error) {
	return e.store.Reports(ctx, acct, since, limit)
}

// VerifyMaterialized compares the stored position rows with a fresh replay of the event log
// (section 06: "ledger rebuilt from events matches the materialized state exactly").
func (e *Engine) VerifyMaterialized(ctx context.Context) ([]string, error) {
	rows, err := e.store.Positions(ctx)
	if err != nil {
		return nil, err
	}
	st, err := e.store.Load(ctx)
	if err != nil {
		return nil, err
	}
	b, brk, rsv := book.New(), book.NewBreaks(), book.NewReservations()
	for _, ev := range st.Events {
		if _, _, err := b.Apply(ev, brk, rsv); err != nil {
			return nil, err
		}
	}
	var diffs []string
	seen := map[string]bool{}
	for _, r := range rows {
		key := r.Account + "|" + r.Owner + "|" + r.InstrumentID
		seen[key] = true
		q, c, real := book.Zero, book.Zero, book.Zero
		if a := b.Accounts[r.Account]; a != nil {
			if o := a.Owner(r.Owner); o != nil {
				if p := o.Positions[r.InstrumentID]; p != nil {
					q, c, real = p.Qty(), p.Cost(), p.Realized
				}
			}
		}
		if !q.Equal(r.Qty) || !c.Equal(r.Cost) || !real.Equal(r.Realized) {
			diffs = append(diffs, key+": stored "+r.Qty.String()+"/"+r.Cost.String()+"/"+r.Realized.String()+" replay "+q.String()+"/"+c.String()+"/"+real.String())
		}
	}
	for _, id := range b.AccountIDs() {
		a := b.Accounts[id]
		for _, oid := range a.OwnerIDs() {
			for instr, p := range a.Owners[oid].Positions {
				if key := id + "|" + oid + "|" + instr; !seen[key] && !p.Qty().IsZero() {
					diffs = append(diffs, key+": missing materialized row")
				}
			}
		}
	}
	sort.Strings(diffs)
	return diffs, nil
}

// HaltedPairs returns the latest halted pairs for an account (execution consumes the bus copy).
func HaltedMatch(pairs []string, owner, instr string) bool {
	for _, p := range pairs {
		o, i := book.ParseOwnerRef(p)
		if (o == "*" || o == owner) && (i == "*" || i == instr) {
			return true
		}
	}
	return false
}

// AccountStatus is a monitoring summary for one account.
type AccountStatus struct {
	Status     string
	OpenBreaks int
	AsOf       int64
}

// Statuses summarizes the latest reconciliation per account (metrics).
func (e *Engine) Statuses() map[string]AccountStatus {
	e.mu.RLock()
	defer e.mu.RUnlock()
	out := map[string]AccountStatus{}
	for _, id := range e.b.AccountIDs() {
		st := AccountStatus{OpenBreaks: len(e.brk.List(id, true))}
		if r := e.lastRecon[id]; r != nil {
			st.Status, st.AsOf = r.GetStatus().String(), r.GetAsOf()
		}
		out[id] = st
	}
	return out
}
