package engine

import (
	"context"
	"sync"

	"github.com/shopspring/decimal"

	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/ledger/internal/book"
)

// Batch is everything one engine operation changes; Store.Commit writes it atomically.
type Batch struct {
	Events              []book.Event
	Positions           []PositionRow
	Reservations        []*book.Reservation
	DeletedReservations []string
	Breaks              []*book.Break
	Reports             []*ledgerv1.ReconciliationResult
	Equity              []EquityRow
	Washes              []book.WashSale
	Snapshot            *ledgerv1.AccountSnapshot
	PendingActions      map[string]*mdv1.CorporateAction
	AppliedActions      []string
	Outbox              []outbox.Message
	Audit               []audit.Event
}

// PositionRow is the materialized owner position written with its event (rebuild check, SQL views).
type PositionRow struct {
	Account, Owner, InstrumentID string
	Qty, Cost, Realized          decimal.Decimal
	Lots                         []book.Lot
}

// EquityRow is one equity-curve point (VP-024).
type EquityRow struct {
	Account, Owner                                  string
	TS                                              int64
	Date                                            string
	Equity, Cash, MarketValue, Realized, Unrealized decimal.Decimal
	Gross, DayStart, HWM                            decimal.Decimal
}

// DayStart is the restored session-start equity and high-water mark per account|owner.
type DayStart struct {
	Date          string
	DayStart, HWM decimal.Decimal
}

// State is what Load returns.
type State struct {
	Events         []book.Event
	Breaks         []*book.Break
	Reservations   []*book.Reservation
	Snapshots      []*ledgerv1.AccountSnapshot
	EquityStart    map[string]DayStart
	PendingActions map[string]*mdv1.CorporateAction
}

// Store persists the ledger. Commit is atomic: the event log, materialized rows, operational
// tables, audit records, and outbox messages commit together or not at all.
type Store interface {
	Load(ctx context.Context) (*State, error)
	Commit(ctx context.Context, b *Batch) error
	Positions(ctx context.Context) ([]PositionRow, error)
	Equity(ctx context.Context, acct, owner string, start, end int64) ([]EquityRow, error)
	Reports(ctx context.Context, acct string, since int64, limit int) ([]*ledgerv1.ReconciliationResult, error)
	Washes(ctx context.Context, since int64) ([]book.WashSale, error)
}

// Memory is an in-memory Store for tests and the paper harness. Publish (optional) receives every
// outbox message after commit, in order.
type Memory struct {
	mu        sync.Mutex
	events    []book.Event
	keys      map[string]bool
	positions map[string]PositionRow
	breaks    map[string]*book.Break
	rsv       map[string]*book.Reservation
	reports   []*ledgerv1.ReconciliationResult
	equity    []EquityRow
	washes    []book.WashSale
	snaps     map[string]*ledgerv1.AccountSnapshot
	pending   map[string]*mdv1.CorporateAction
	Audits    []audit.Event
	Outbox    []outbox.Message
	Publish   func(outbox.Message)
	// FailNext makes the next Commit fail (fault injection).
	FailNext error
}

// NewMemory returns an empty store.
func NewMemory() *Memory {
	return &Memory{keys: map[string]bool{}, positions: map[string]PositionRow{}, breaks: map[string]*book.Break{}, rsv: map[string]*book.Reservation{},
		snaps: map[string]*ledgerv1.AccountSnapshot{}, pending: map[string]*mdv1.CorporateAction{}}
}

// Load implements Store.
func (m *Memory) Load(context.Context) (*State, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	st := &State{Events: append([]book.Event(nil), m.events...), EquityStart: map[string]DayStart{}, PendingActions: map[string]*mdv1.CorporateAction{}}
	for _, b := range m.breaks {
		c := *b
		st.Breaks = append(st.Breaks, &c)
	}
	for _, r := range m.rsv {
		c := *r
		st.Reservations = append(st.Reservations, &c)
	}
	for _, s := range m.snaps {
		st.Snapshots = append(st.Snapshots, s)
	}
	for _, e := range m.equity {
		st.EquityStart[e.Account+"|"+e.Owner] = DayStart{Date: e.Date, DayStart: e.DayStart, HWM: e.HWM}
	}
	for k, v := range m.pending {
		st.PendingActions[k] = v
	}
	return st, nil
}

// Commit implements Store.
func (m *Memory) Commit(_ context.Context, b *Batch) error {
	m.mu.Lock()
	if m.FailNext != nil {
		err := m.FailNext
		m.FailNext = nil
		m.mu.Unlock()
		return err
	}
	for _, e := range b.Events {
		if m.keys[e.Key()] {
			continue
		}
		m.keys[e.Key()] = true
		m.events = append(m.events, e)
	}
	for _, p := range b.Positions {
		p.Lots = append([]book.Lot(nil), p.Lots...)
		m.positions[p.Account+"|"+p.Owner+"|"+p.InstrumentID] = p
	}
	for _, r := range b.Reservations {
		c := *r
		m.rsv[r.ID] = &c
	}
	for _, id := range b.DeletedReservations {
		delete(m.rsv, id)
	}
	for _, x := range b.Breaks {
		c := *x
		m.breaks[x.ID] = &c
	}
	m.reports = append(m.reports, b.Reports...)
	m.equity = append(m.equity, b.Equity...)
	m.washes = append(m.washes, b.Washes...)
	if b.Snapshot != nil {
		m.snaps[b.Snapshot.GetBrokerAccountId()] = b.Snapshot
	}
	for k, v := range b.PendingActions {
		m.pending[k] = v
	}
	for _, k := range b.AppliedActions {
		delete(m.pending, k)
	}
	m.Audits = append(m.Audits, b.Audit...)
	m.Outbox = append(m.Outbox, b.Outbox...)
	pub := m.Publish
	m.mu.Unlock()
	if pub != nil {
		for _, o := range b.Outbox {
			pub(o)
		}
	}
	return nil
}

// Positions implements Store.
func (m *Memory) Positions(context.Context) ([]PositionRow, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]PositionRow, 0, len(m.positions))
	for _, p := range m.positions {
		out = append(out, p)
	}
	return out, nil
}

// Equity implements Store.
func (m *Memory) Equity(_ context.Context, acct, owner string, start, end int64) ([]EquityRow, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []EquityRow
	for _, e := range m.equity {
		if e.Account == acct && e.Owner == owner && (start == 0 || e.TS >= start) && (end == 0 || e.TS <= end) {
			out = append(out, e)
		}
	}
	return out, nil
}

// Reports implements Store.
func (m *Memory) Reports(_ context.Context, acct string, since int64, limit int) ([]*ledgerv1.ReconciliationResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []*ledgerv1.ReconciliationResult
	for i := len(m.reports) - 1; i >= 0; i-- {
		r := m.reports[i]
		if (acct == "" || r.GetBrokerAccountId() == acct) && r.GetAsOf() >= since {
			out = append(out, r)
			if limit > 0 && len(out) >= limit {
				break
			}
		}
	}
	return out, nil
}

// Washes implements Store.
func (m *Memory) Washes(_ context.Context, since int64) ([]book.WashSale, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []book.WashSale
	for _, w := range m.washes {
		if w.SellTime >= since {
			out = append(out, w)
		}
	}
	return out, nil
}

// Messages returns outbox messages on subject (tests).
func (m *Memory) Messages(subject string) []outbox.Message {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []outbox.Message
	for _, o := range m.Outbox {
		if subject == "" || o.Subject == subject {
			out = append(out, o)
		}
	}
	return out
}
