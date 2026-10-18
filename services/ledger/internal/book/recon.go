package book

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/shopspring/decimal"
)

// ReconConfig tunes reconciliation (VP-013).
type ReconConfig struct {
	// Grace is how long a quantity difference may persist (as a timing difference) before it becomes
	// a break; MinSnapshots is the minimum number of snapshots it must persist for.
	Grace        time.Duration
	MinSnapshots int
	// CashTolerance is the absolute cash difference treated as pending settlement noise.
	CashTolerance decimal.Decimal
	// StaleAfter: a snapshot older than this makes the status STALE (fail closed).
	StaleAfter time.Duration
}

// DefaultReconConfig returns the documented defaults.
func DefaultReconConfig() ReconConfig {
	return ReconConfig{Grace: 60 * time.Second, MinSnapshots: 2, CashTolerance: decimal.NewFromInt(5), StaleAfter: 90 * time.Second}
}

// UnmappedKey is the instrument key for broker positions with no instrument_id (VP-010): they are
// tracked for `manual` only and strategies can never trade them.
func UnmappedKey(symbol string) string { return "sym:" + strings.ToUpper(symbol) }

// IsUnmapped reports whether key is an UnmappedKey.
func IsUnmapped(key string) bool { return strings.HasPrefix(key, "sym:") }

// SnapPos is one broker position, instrument already resolved by the engine.
type SnapPos struct {
	InstrumentID string
	Symbol       string
	Qty          decimal.Decimal
	MarketValue  decimal.Decimal
	AvgCost      decimal.Decimal
}

// Snapshot is the broker account state the ledger reconciles against.
type Snapshot struct {
	AsOf        int64
	Cash        decimal.Decimal
	BuyingPower decimal.Decimal
	Equity      decimal.Decimal
	Positions   []SnapPos
}

// Qty returns the broker quantity for instr.
func (s *Snapshot) Qty(instr string) decimal.Decimal {
	for _, p := range s.Positions {
		if p.InstrumentID == instr {
			return p.Qty
		}
	}
	return Zero
}

// Statuses (tb.ledger.v1.ReconciliationStatus names, lowercased).
const (
	StatusOK      = "ok"
	StatusBreak   = "break"
	StatusPending = "pending"
	StatusStale   = "stale"
)

// Diff is one instrument whose broker and ledger quantities differ.
type Diff struct {
	InstrumentID string
	Symbol       string
	BrokerQty    decimal.Decimal
	LedgerQty    decimal.Decimal
	Owners       map[string]decimal.Decimal
}

// ReconResult is one reconciliation run.
type ReconResult struct {
	Account      string
	AsOf         int64
	SnapshotAsOf int64
	Status       string
	Diffs        []Diff
	Open         []*Break
	Halted       []string
	BrokerCash   decimal.Decimal
	LedgerCash   decimal.Decimal
	Compared     int
	Trigger      string
	// Changed lists breaks created, updated, or resolved by this run (to persist).
	Changed []*Break
}

func diffBreakID(acct, instr string) string { return "diff-" + acct + "-" + instr }

// openDiffBreak returns the open quantity-difference break for instr, if any.
func openDiffBreak(brk *Breaks, acct, instr string) *Break {
	prefix := diffBreakID(acct, instr)
	for _, b := range brk.List(acct, true) {
		if b.InstrumentID == instr && (b.ID == prefix || strings.HasPrefix(b.ID, prefix+"-")) {
			return b
		}
	}
	return nil
}
func cashBreakID(acct string) string { return "cash-" + acct }

// Reconcile compares the broker snapshot with Σ owner quantities and the expected cash (VP-007,
// invariant 1). Differences start as timing differences and become breaks after the grace window
// and minimum snapshot count (VP-013); a difference that disappears auto-resolves. It never changes
// quantities (VP-008).
func Reconcile(a *Account, snap *Snapshot, brk *Breaks, now int64, cfg ReconConfig, trigger string) ReconResult {
	res := ReconResult{Account: a.ID, AsOf: now, Trigger: trigger, LedgerCash: a.Cash}
	if snap == nil || time.Duration(now-snap.AsOf) > cfg.StaleAfter {
		res.Status = StatusStale
		if snap != nil {
			res.SnapshotAsOf = snap.AsOf
		}
		res.Open = brk.List(a.ID, true)
		res.Halted = haltedPairs(res.Open)
		return res
	}
	res.SnapshotAsOf, res.BrokerCash = snap.AsOf, snap.Cash
	set := map[string]string{}
	for _, p := range snap.Positions {
		set[p.InstrumentID] = p.Symbol
	}
	for _, id := range a.Instruments() {
		if _, ok := set[id]; !ok {
			set[id] = a.Symbols[id]
		}
	}
	// Diff breaks for instruments that are no longer in either side still need a look.
	for _, b := range brk.List(a.ID, true) {
		if strings.HasPrefix(b.ID, "diff-") {
			if _, ok := set[b.InstrumentID]; !ok {
				set[b.InstrumentID] = b.Symbol
			}
		}
	}
	instrs := make([]string, 0, len(set))
	for id := range set {
		instrs = append(instrs, id)
	}
	sort.Strings(instrs)
	for _, instr := range instrs {
		res.Compared++
		bq, lq := snap.Qty(instr), a.LedgerQty(instr)
		existing := openDiffBreak(brk, a.ID, instr)
		if bq.Equal(lq) {
			if existing != nil {
				existing.Status, existing.Resolved, existing.Resolution = BreakAutoResolved, now, "quantities match"
				res.Changed = append(res.Changed, existing)
			}
			continue
		}
		owners := map[string]decimal.Decimal{}
		for _, oid := range a.OwnerIDs() {
			if q := a.Owners[oid].Qty(instr); !q.IsZero() {
				owners[oid] = q
			}
		}
		res.Diffs = append(res.Diffs, Diff{InstrumentID: instr, Symbol: set[instr], BrokerQty: bq, LedgerQty: lq, Owners: owners})
		if existing == nil {
			id := diffBreakID(a.ID, instr)
			if brk.Get(id) != nil {
				// An earlier difference on this instrument was resolved; start a new window.
				id = fmt.Sprintf("%s-%d", id, now)
			}
			existing = brk.Open(Break{ID: id, Account: a.ID, Type: BreakTiming, InstrumentID: instr, Symbol: set[instr],
				Severity: SeverityWarning, Detected: now, FirstSeen: now, Snapshots: 1, InstrumentWide: true,
				Source: fmt.Sprintf("snapshot:%d", snap.AsOf)})
		} else if fmt.Sprintf("snapshot:%d", snap.AsOf) != existing.Source {
			existing.Snapshots++
			existing.Source = fmt.Sprintf("snapshot:%d", snap.AsOf)
		}
		existing.BrokerQty, existing.LedgerQty, existing.Owners = bq, lq, owners
		existing.Detail = fmt.Sprintf("broker %s vs ledger %s", bq, lq)
		if existing.Type == BreakTiming && time.Duration(now-existing.FirstSeen) >= cfg.Grace && existing.Snapshots >= cfg.MinSnapshots {
			existing.Type, existing.Severity = BreakMissingExecution, SeverityCritical
			existing.Affected = nil
			for oid := range owners {
				if oid != Manual {
					existing.Affected = append(existing.Affected, oid)
				}
			}
			sort.Strings(existing.Affected)
			existing.Detail = fmt.Sprintf("broker %s vs ledger %s for %s beyond the %s grace window; no execution explains it", bq, lq, set[instr], cfg.Grace)
			existing.Suggestions = []string{ResolutionAccept, ResolutionBookToManual}
		}
		res.Changed = append(res.Changed, existing)
	}
	if a.CashBaseline {
		d := snap.Cash.Sub(a.Cash)
		cb := brk.Get(cashBreakID(a.ID))
		if d.Abs().LessThanOrEqual(cfg.CashTolerance) {
			if cb != nil && cb.IsOpen() {
				cb.Status, cb.Resolved, cb.Resolution = BreakAutoResolved, now, "cash within tolerance"
				res.Changed = append(res.Changed, cb)
			}
		} else {
			if cb == nil || !cb.IsOpen() {
				if cb != nil {
					brk.Delete(cb.ID)
				}
				cb = brk.Open(Break{ID: cashBreakID(a.ID), Account: a.ID, Type: BreakTiming, Severity: SeverityWarning, Detected: now, FirstSeen: now,
					Source: fmt.Sprintf("cash:%d", snap.AsOf), Snapshots: 1})
			} else if fmt.Sprintf("cash:%d", snap.AsOf) != cb.Source {
				cb.Snapshots++
				cb.Source = fmt.Sprintf("cash:%d", snap.AsOf)
			}
			cb.BrokerQty, cb.LedgerQty = snap.Cash, a.Cash
			cb.Detail = fmt.Sprintf("broker cash %s vs expected %s (difference %s)", snap.Cash, a.Cash, d)
			if cb.Type == BreakTiming && time.Duration(now-cb.FirstSeen) >= cfg.Grace && cb.Snapshots >= cfg.MinSnapshots {
				// Cash differences alert but do not halt (fees, interest, transfers); VP-018.
				cb.Type = BreakCashMismatch
				cb.Suggestions = []string{ResolutionRebaselineCash, ResolutionAccept}
			}
			res.Changed = append(res.Changed, cb)
		}
	}
	res.Open = brk.List(a.ID, true)
	res.Halted = haltedPairs(res.Open)
	res.Status = StatusOK
	for _, b := range res.Open {
		switch {
		case b.Severity == SeverityCritical && b.Type != BreakTiming:
			res.Status = StatusBreak
		case b.Type == BreakTiming && res.Status == StatusOK && b.InstrumentID != "":
			res.Status = StatusPending
		}
	}
	return res
}

// ResolutionBookToManual books an unexplained broker difference to `manual` (VP-019).
const ResolutionBookToManual = "book_to_manual"

// haltedPairs lists "owner|instrument" pairs blocked by open breaks; "*" matches any owner or
// instrument (VP-008). Timing differences block the instrument for every strategy until resolved.
func haltedPairs(open []*Break) []string {
	set := map[string]bool{}
	for _, b := range open {
		instr := b.InstrumentID
		switch {
		case b.Type == BreakTiming && instr != "":
			set["*|"+instr] = true
		case b.Severity != SeverityCritical:
		case instr == "":
			set["*|*"] = true
		case b.InstrumentWide || len(b.Affected) == 0:
			set["*|"+instr] = true
		default:
			for _, o := range b.Affected {
				set[o+"|"+instr] = true
			}
		}
	}
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// Delete removes a break (only used to replace an auto-resolved cash break).
func (r *Breaks) Delete(id string) { delete(r.m, id) }
