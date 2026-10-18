package book

import (
	"fmt"

	"github.com/shopspring/decimal"
)

// CapitalConfig is the per-account capital policy (VP-009).
type CapitalConfig struct {
	// ReservePct of equity (and ReserveAbs) are kept out of reach of every strategy.
	ReservePct decimal.Decimal
	ReserveAbs decimal.Decimal
	// StrategyBPShare caps the strategies' combined open-order cash at this fraction of the broker's
	// reported buying power, protecting the manual book's margin (VP-020).
	StrategyBPShare decimal.Decimal
	// BlockAllocationDecrease rejects a decrease below the strategy's invested + reserved capital
	// (default) instead of forcing a reduction.
	BlockAllocationDecrease bool
}

// DefaultCapitalConfig returns the documented defaults.
func DefaultCapitalConfig() CapitalConfig {
	return CapitalConfig{ReservePct: decimal.RequireFromString("0.10"), StrategyBPShare: decimal.NewFromInt(1), BlockAllocationDecrease: true}
}

// Constraints is the ownership answer for the order path (VP-003..VP-005).
type Constraints struct {
	StrategyQty, OtherOwnersQty, AccountQty, ManualQty, MaxSellable decimal.Decimal
	ShortAllowed                                                    bool
	Reason                                                          string
	Halted                                                          bool
}

// Constraints answers what owner may sell or short in instr. brokerQty is the broker's latest
// quantity (nil when no snapshot); the result never lets a sale take the account below the other
// owners' quantity, by the ledger's count or the broker's (VP-003).
func (b *Book) Constraints(acct, instr, owner string, brokerQty *decimal.Decimal, brk *Breaks, res *Reservations) Constraints {
	a := b.Accounts[acct]
	c := Constraints{StrategyQty: Zero, OtherOwnersQty: Zero, AccountQty: Zero, ManualQty: Zero, MaxSellable: Zero}
	if a == nil {
		c.Reason = "unknown account"
		return c
	}
	o := a.Owner(owner)
	own := Zero
	if o != nil {
		own = o.Qty(instr)
	}
	total := a.LedgerQty(instr)
	c.StrategyQty, c.AccountQty, c.OtherOwnersQty = own, total, total.Sub(own)
	c.ManualQty = a.Owner(Manual).Qty(instr)
	maxSell := own.Sub(res.ReservedSell(acct, owner, instr))
	if brokerQty != nil {
		// Never below the other owners' quantity at the broker either.
		maxSell = decimal.Min(maxSell, brokerQty.Sub(c.OtherOwnersQty).Sub(res.ReservedSell(acct, owner, instr)))
	}
	c.MaxSellable = decimal.Max(Zero, maxSell)
	switch {
	case IsUnmapped(instr):
		c.Reason = "instrument is not in the instrument master; strategies cannot trade it"
		c.MaxSellable = Zero
	case o == nil:
		c.Reason = "owner not registered in this account"
	case owner == Manual:
		c.Reason = "manual orders may sell manual quantity only"
	case !o.AllowShort:
		c.Reason = "strategy is not allowed to short"
	case !o.Dedicated:
		c.Reason = "shorting requires a dedicated account (VP-005)"
	case !c.OtherOwnersQty.IsZero():
		c.Reason = fmt.Sprintf("other owners hold %s in this account (VP-005)", c.OtherOwnersQty)
	default:
		c.ShortAllowed = true
	}
	c.Halted = brk.Halted(acct, owner, instr)
	return c
}

// Capital is the capital answer for one owner.
type Capital struct {
	Allocation, CashAvailable, BuyingPowerAvailable, Reserved decimal.Decimal
}

// Capital computes an owner's capital (VP-020): allocation + realized + dividends − open cost −
// open-order reservations, further capped by the strategies' share of the broker buying power.
// `manual` has no allocation; its buying power is the broker's minus every reservation.
func (b *Book) Capital(acct, owner string, snap *Snapshot, cfg CapitalConfig, res *Reservations) Capital {
	a := b.Accounts[acct]
	out := Capital{Allocation: Zero, CashAvailable: Zero, BuyingPowerAvailable: Zero, Reserved: Zero}
	if a == nil {
		return out
	}
	o := a.Owner(owner)
	if o == nil {
		return out
	}
	out.Reserved = res.ReservedCash(acct, owner)
	bp := Zero
	if snap != nil {
		bp = snap.BuyingPower
	}
	if owner == Manual {
		out.CashAvailable = decimal.Max(Zero, bp.Sub(res.ReservedCash(acct, "")))
		out.BuyingPowerAvailable = out.CashAvailable
		return out
	}
	out.Allocation = o.Allocation
	out.CashAvailable = o.Allocation.Add(o.Realized).Add(o.Dividends).Sub(o.Invested()).Sub(out.Reserved)
	strategyBP := bp.Mul(cfg.StrategyBPShare).Sub(b.strategyReservedCash(acct, res))
	out.BuyingPowerAvailable = decimal.Max(Zero, decimal.Min(out.CashAvailable, strategyBP))
	return out
}

func (b *Book) strategyReservedCash(acct string, res *Reservations) decimal.Decimal {
	c := Zero
	for _, x := range res.All() {
		if x.Account == acct && x.Buy && x.Owner != Manual {
			c = c.Add(x.Notional())
		}
	}
	return c
}

// Allocatable is the capital strategies may be allocated: equity minus the reserve (VP-009).
func Allocatable(snap *Snapshot, cfg CapitalConfig) decimal.Decimal {
	if snap == nil {
		return Zero
	}
	v := snap.Equity.Sub(snap.Equity.Mul(cfg.ReservePct)).Sub(cfg.ReserveAbs)
	return decimal.Max(Zero, v)
}

// TotalAllocated sums every strategy allocation in acct, optionally excluding one owner.
func (b *Book) TotalAllocated(acct, except string) decimal.Decimal {
	a := b.Accounts[acct]
	t := Zero
	if a == nil {
		return t
	}
	for id, o := range a.Owners {
		if id != except && id != Manual {
			t = t.Add(o.Allocation)
		}
	}
	return t
}

// CheckAllocation validates a new allocation (VP-009): the sum of allocations may not exceed
// allocatable capital, and (by default) a decrease may not go below invested + reserved capital.
func (b *Book) CheckAllocation(acct, owner string, amount decimal.Decimal, snap *Snapshot, cfg CapitalConfig, res *Reservations) error {
	a := b.Accounts[acct]
	if a == nil || a.Owner(owner) == nil || owner == Manual {
		return fmt.Errorf("owner %q is not a strategy in %s", owner, acct)
	}
	if amount.IsNegative() {
		return fmt.Errorf("allocation must not be negative")
	}
	alloc := Allocatable(snap, cfg)
	if total := b.TotalAllocated(acct, owner).Add(amount); total.GreaterThan(alloc) {
		return fmt.Errorf("allocations would total %s, above allocatable capital %s (VP-009)", total, alloc)
	}
	o := a.Owner(owner)
	if cfg.BlockAllocationDecrease && amount.LessThan(o.Allocation) {
		used := o.Invested().Sub(o.Realized).Sub(o.Dividends).Add(res.ReservedCash(acct, owner))
		if amount.LessThan(used) {
			return fmt.Errorf("allocation %s is below the %s the strategy has invested or reserved; reduce exposure first", amount, used)
		}
	}
	return nil
}

// Reserve checks and builds a reservation (VP-020). It does not store it.
func (b *Book) Reserve(r Reservation, snap *Snapshot, cfg CapitalConfig, brk *Breaks, res *Reservations, short bool) (*Reservation, error) {
	if existing := res.ByIntent(r.IntentID); existing != nil {
		return existing, nil
	}
	if !r.Qty.IsPositive() || r.EstPrice.IsNegative() {
		return nil, fmt.Errorf("quantity must be positive and price non-negative")
	}
	a := b.Accounts[r.Account]
	if a == nil || a.Owner(r.Owner) == nil {
		return nil, fmt.Errorf("owner %q is not registered in %s", r.Owner, r.Account)
	}
	r.Remaining = r.Qty
	if r.Buy || short {
		capital := b.Capital(r.Account, r.Owner, snap, cfg, res)
		need := r.Qty.Mul(r.EstPrice)
		if need.GreaterThan(capital.BuyingPowerAvailable) {
			return nil, fmt.Errorf("needs %s, %s available to %s", need.StringFixed(2), capital.BuyingPowerAvailable.StringFixed(2), r.Owner)
		}
		if short {
			var bq *decimal.Decimal
			if snap != nil {
				q := snap.Qty(r.InstrumentID)
				bq = &q
			}
			c := b.Constraints(r.Account, r.InstrumentID, r.Owner, bq, brk, res)
			if !c.ShortAllowed {
				return nil, fmt.Errorf("short not allowed: %s", c.Reason)
			}
			r.Buy = true // a short reserves cash like a buy (margin)
		}
		return &r, nil
	}
	var bq *decimal.Decimal
	if snap != nil {
		q := snap.Qty(r.InstrumentID)
		bq = &q
	}
	c := b.Constraints(r.Account, r.InstrumentID, r.Owner, bq, brk, res)
	if r.Qty.GreaterThan(c.MaxSellable) {
		return nil, fmt.Errorf("sell %s exceeds %s's own sellable quantity %s (VP-004)", r.Qty, r.Owner, c.MaxSellable)
	}
	return &r, nil
}

// Valuation is an owner's mark-to-market.
type Valuation struct {
	MarketValue, Unrealized, Gross, Net, Equity decimal.Decimal
}

// Value marks an owner's positions with the account marks.
func (a *Account) Value(o *Owner) Valuation {
	v := Valuation{MarketValue: Zero, Unrealized: Zero, Gross: Zero, Net: Zero}
	for id, p := range o.Positions {
		q := p.Qty()
		if q.IsZero() {
			continue
		}
		mark, ok := a.Marks[id]
		if !ok {
			mark = p.Cost().Div(q)
		}
		mv := q.Mul(mark)
		v.MarketValue = v.MarketValue.Add(mv)
		v.Unrealized = v.Unrealized.Add(mv.Sub(p.Cost()))
		v.Gross = v.Gross.Add(mv.Abs())
		v.Net = v.Net.Add(mv)
	}
	v.Equity = o.Allocation.Add(o.Realized).Add(o.Dividends).Add(v.Unrealized)
	if o.ID == Manual {
		v.Equity = v.MarketValue
	}
	return v
}
