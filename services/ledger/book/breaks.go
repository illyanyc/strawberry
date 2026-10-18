package book

import (
	"sort"

	"github.com/shopspring/decimal"
)

// Break types (VP-014); names match tb.ledger.v1.BreakType without the prefix, lowercased.
const (
	BreakTiming             = "timing"
	BreakEncroachment       = "encroachment"
	BreakUnknownExecution   = "unknown_execution"
	BreakMissingExecution   = "missing_execution"
	BreakCorporateAction    = "corporate_action"
	BreakCashMismatch       = "cash_mismatch"
	BreakOrphanOrder        = "orphan_order"
	BreakGhostOrder         = "ghost_order"
	BreakOversell           = "oversell"
	BreakUnmappedInstrument = "unmapped_instrument"
)

// Break statuses.
const (
	BreakOpen         = "open"
	BreakAutoResolved = "auto_resolved"
	BreakResolved     = "resolved"
)

// Severities: critical breaks halt the affected strategies (VP-008); warnings alert only.
const (
	SeverityCritical = "critical"
	SeverityWarning  = "warning"
)

// Owner resolutions (VP-019).
const (
	ResolutionAccept           = "accept"
	ResolutionTransferToManual = "transfer_to_manual"
	ResolutionRebaselineCash   = "rebaseline_cash"
)

// Break is one reconciliation break.
type Break struct {
	ID           string                     `json:"id"`
	Account      string                     `json:"account"`
	Type         string                     `json:"type"`
	Status       string                     `json:"status"`
	InstrumentID string                     `json:"instrument_id,omitempty"`
	Symbol       string                     `json:"symbol,omitempty"`
	BrokerQty    decimal.Decimal            `json:"broker_qty"`
	LedgerQty    decimal.Decimal            `json:"ledger_qty"`
	Owners       map[string]decimal.Decimal `json:"owners,omitempty"`
	Affected     []string                   `json:"affected,omitempty"`
	Detail       string                     `json:"detail"`
	Detected     int64                      `json:"detected"`
	Resolved     int64                      `json:"resolved,omitempty"`
	ResolvedBy   string                     `json:"resolved_by,omitempty"`
	Resolution   string                     `json:"resolution,omitempty"`
	Severity     string                     `json:"severity"`
	Source       string                     `json:"source,omitempty"`
	Suggestions  []string                   `json:"suggestions,omitempty"`
	// InstrumentWide halts every strategy for the instrument, not just Affected.
	InstrumentWide bool `json:"instrument_wide,omitempty"`
	// Diff-based breaks: first time the difference was seen and how many snapshots it persisted.
	FirstSeen int64 `json:"first_seen,omitempty"`
	Snapshots int   `json:"snapshots,omitempty"`
	// Alerted is set once the critical alert for this break was published.
	Alerted bool `json:"alerted,omitempty"`
}

// IsOpen reports whether the break still needs attention.
func (b *Break) IsOpen() bool { return b.Status == BreakOpen }

// Halts reports whether an open break blocks owner in instr (VP-008).
func (b *Break) Halts(owner, instr string) bool {
	if !b.IsOpen() || b.Severity != SeverityCritical || b.Type == BreakTiming {
		return false
	}
	if b.InstrumentID == "" {
		return true
	}
	if b.InstrumentID != instr {
		return false
	}
	if b.InstrumentWide {
		return true
	}
	for _, a := range b.Affected {
		if a == owner {
			return true
		}
	}
	return false
}

// Breaks is the break registry for every account.
type Breaks struct {
	m map[string]*Break
}

// NewBreaks returns an empty registry.
func NewBreaks() *Breaks { return &Breaks{m: map[string]*Break{}} }

// Get returns a break by ID.
func (r *Breaks) Get(id string) *Break { return r.m[id] }

// Put stores b (load path).
func (r *Breaks) Put(b *Break) { r.m[b.ID] = b }

// Open registers b as open unless a break with its ID exists; it returns the stored break.
func (r *Breaks) Open(b Break) *Break {
	if x := r.m[b.ID]; x != nil {
		return x
	}
	b.Status = BreakOpen
	if b.Owners == nil {
		b.Owners = map[string]decimal.Decimal{}
	}
	r.m[b.ID] = &b
	return &b
}

// List returns breaks for an account (all accounts when acct is empty), newest first.
func (r *Breaks) List(acct string, openOnly bool) []*Break {
	var out []*Break
	for _, b := range r.m {
		if (acct == "" || b.Account == acct) && (!openOnly || b.IsOpen()) {
			out = append(out, b)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Detected != out[j].Detected {
			return out[i].Detected > out[j].Detected
		}
		return out[i].ID < out[j].ID
	})
	return out
}

// Halted reports whether owner is blocked in instr in acct by any open break.
func (r *Breaks) Halted(acct, owner, instr string) bool {
	for _, b := range r.m {
		if b.Account == acct && b.Halts(owner, instr) {
			return true
		}
	}
	return false
}

// Reservations is the reservation registry.
type Reservations struct {
	byID     map[string]*Reservation
	byIntent map[string]*Reservation
}

// NewReservations returns an empty registry.
func NewReservations() *Reservations {
	return &Reservations{byID: map[string]*Reservation{}, byIntent: map[string]*Reservation{}}
}

// Put stores r.
func (r *Reservations) Put(x *Reservation) {
	r.byID[x.ID] = x
	r.byIntent[x.IntentID] = x
}

// Get returns a reservation by ID.
func (r *Reservations) Get(id string) *Reservation { return r.byID[id] }

// ByIntent returns the reservation of an intent.
func (r *Reservations) ByIntent(intent string) *Reservation {
	if intent == "" {
		return nil
	}
	return r.byIntent[intent]
}

// Delete removes a reservation.
func (r *Reservations) Delete(x *Reservation) {
	delete(r.byID, x.ID)
	delete(r.byIntent, x.IntentID)
}

// All returns every reservation (stable order).
func (r *Reservations) All() []*Reservation {
	out := make([]*Reservation, 0, len(r.byID))
	for _, x := range r.byID {
		out = append(out, x)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// ReservedSell is the own quantity reserved for open sells of owner in instr.
func (r *Reservations) ReservedSell(acct, owner, instr string) decimal.Decimal {
	q := Zero
	for _, x := range r.byID {
		if x.Account == acct && x.Owner == owner && x.InstrumentID == instr && !x.Buy {
			q = q.Add(x.Remaining)
		}
	}
	return q
}

// ReservedCash is the cash reserved for open buys of owner ("" = every owner) in acct.
func (r *Reservations) ReservedCash(acct, owner string) decimal.Decimal {
	c := Zero
	for _, x := range r.byID {
		if x.Account == acct && x.Buy && (owner == "" || x.Owner == owner) {
			c = c.Add(x.Notional())
		}
	}
	return c
}
