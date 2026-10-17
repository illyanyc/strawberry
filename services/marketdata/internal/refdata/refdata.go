// Package refdata maintains the instrument master, symbol history, broker identifiers, tradability
// attributes, corporate actions, and point-in-time universes in schema refdata (section 03,
// ADR-0020, rules DF-005, DF-006, VP-010, MD-011, MD-012), and mirrors them into the lake (ref/)
// for Python readers. marketdata is the single writer.
package refdata

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"

	"github.com/oklog/ulid/v2"

	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

// Instrument is one row of the instrument master.
type Instrument struct {
	InstrumentID    string `parquet:"instrument_id"`
	AssetClass      string `parquet:"asset_class"`
	Currency        string `parquet:"currency"`
	Name            string `parquet:"name"`
	PrimaryExchange string `parquet:"primary_exchange"`
	SectorETF       string `parquet:"sector_etf"`
	ListedAt        int64  `parquet:"listed_at"`   // UTC ns, 0 = unknown
	DelistedAt      int64  `parquet:"delisted_at"` // UTC ns, 0 = still listed
}

// SymbolSpan is a ticker valid over [ValidFrom, ValidTo); ValidTo 0 = open.
type SymbolSpan struct {
	InstrumentID string `parquet:"instrument_id"`
	Symbol       string `parquet:"symbol"`
	Exchange     string `parquet:"exchange"`
	ValidFrom    int64  `parquet:"valid_from"`
	ValidTo      int64  `parquet:"valid_to"`
	Source       string `parquet:"source"`
}

// BrokerID maps a broker identifier to an instrument.
type BrokerID struct {
	InstrumentID       string `parquet:"instrument_id"`
	Broker             string `parquet:"broker"`
	BrokerInstrumentID string `parquet:"broker_instrument_id"`
	ValidFrom          int64  `parquet:"valid_from"`
	ValidTo            int64  `parquet:"valid_to"`
}

// Attributes are tradability flags valid over [ValidFrom, ValidTo).
type Attributes struct {
	InstrumentID string `parquet:"instrument_id"`
	Source       string `parquet:"source"`
	Tradable     bool   `parquet:"tradable"`
	Shortable    bool   `parquet:"shortable"`
	EasyToBorrow bool   `parquet:"easy_to_borrow"`
	Fractionable bool   `parquet:"fractionable"`
	Marginable   bool   `parquet:"marginable"`
	ValidFrom    int64  `parquet:"valid_from"`
	ValidTo      int64  `parquet:"valid_to"`
}

// CorporateAction is one source's report of an action (decimals as strings; ratio = new/old).
type CorporateAction struct {
	Source                string `parquet:"source"`
	SourceID              string `parquet:"source_id"`
	InstrumentID          string `parquet:"instrument_id"`
	Symbol                string `parquet:"symbol"`
	Type                  string `parquet:"type"` // split | dividend | symbol_change | delisting | merger | spin_off
	ExDate                int64  `parquet:"ex_date"`
	Ratio                 string `parquet:"ratio"`
	Amount                string `parquet:"amount"`
	NewSymbol             string `parquet:"new_symbol"`
	SuccessorInstrumentID string `parquet:"successor_instrument_id"`
	RecordDate            int64  `parquet:"record_date"`
	PayableDate           int64  `parquet:"payable_date"`
	DeclaredAt            int64  `parquet:"declared_at"`
	AvailableAt           int64  `parquet:"available_at"`
	IngestedAt            int64  `parquet:"ingested_at"`
	Agreement             string `parquet:"agreement"` // single_source | agreed | disagreed
}

// Member is point-in-time universe membership.
type Member struct {
	Universe     string `parquet:"universe"`
	InstrumentID string `parquet:"instrument_id"`
	ValidFrom    int64  `parquet:"valid_from"`
	ValidTo      int64  `parquet:"valid_to"`
}

// AvailableAt implements MD-011: a corporate action is knowable from its declaration date when the
// vendor reports one, otherwise from its ex-date (00:00 New York, when the market reflects it), and
// never later than when we first fetched it.
func AvailableAt(declaredAt, exDate, firstFetch int64) int64 {
	a := exDate
	if declaredAt > 0 {
		a = declaredAt
	}
	if a <= 0 || (firstFetch > 0 && firstFetch < a) {
		a = firstFetch
	}
	return a
}

// ErrUnknownSymbol is returned when a symbol does not resolve at the given time.
var ErrUnknownSymbol = errors.New("refdata: unknown symbol")

// Memory is the in-memory implementation used by tests and tools; Postgres mirrors its semantics.
type Memory struct {
	mu          sync.Mutex
	Instruments map[string]Instrument
	Symbols     []SymbolSpan
	BrokerIDs   []BrokerID
	Attrs       []Attributes
	Actions     map[string]CorporateAction // key source|source_id
	Members     []Member
}

// NewMemory returns an empty store.
func NewMemory() *Memory {
	return &Memory{Instruments: map[string]Instrument{}, Actions: map[string]CorporateAction{}}
}

func newID() string { return ulid.MustNew(ulid.Now(), rand.Reader).String() }

func within(from, to, t int64) bool { return from <= t && (to == 0 || t < to) }

// Resolve returns the instrument whose ticker was symbol at time at (DF-006, VP-010).
func (m *Memory) Resolve(_ context.Context, symbol string, at int64) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, s := range m.Symbols {
		if strings.EqualFold(s.Symbol, symbol) && within(s.ValidFrom, s.ValidTo, at) {
			return s.InstrumentID, nil
		}
	}
	return "", fmt.Errorf("%w %q at %d", ErrUnknownSymbol, symbol, at)
}

// ResolveBroker maps a broker identifier.
func (m *Memory) ResolveBroker(_ context.Context, broker, brokerInstrumentID string, at int64) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, b := range m.BrokerIDs {
		if b.Broker == broker && b.BrokerInstrumentID == brokerInstrumentID && within(b.ValidFrom, b.ValidTo, at) {
			return b.InstrumentID, nil
		}
	}
	return "", ErrUnknownSymbol
}

// History returns an instrument's symbol spans.
func (m *Memory) History(_ context.Context, instrumentID string) []SymbolSpan {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []SymbolSpan
	for _, s := range m.Symbols {
		if s.InstrumentID == instrumentID {
			out = append(out, s)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ValidFrom < out[j].ValidFrom })
	return out
}

// EnsureInstrument returns the instrument trading as symbol at time at, creating it (with a symbol
// span starting at listedAt) when none exists. A recycled ticker of a delisted instrument gets a new
// instrument_id.
func (m *Memory) EnsureInstrument(ctx context.Context, symbol string, at int64, inst Instrument, source string) (string, bool, error) {
	if id, err := m.Resolve(ctx, symbol, at); err == nil {
		return id, false, nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if inst.InstrumentID == "" {
		inst.InstrumentID = newID()
	}
	if inst.Currency == "" {
		inst.Currency = "USD"
	}
	from := inst.ListedAt
	if from == 0 {
		from = at
	}
	m.Instruments[inst.InstrumentID] = inst
	m.Symbols = append(m.Symbols, SymbolSpan{InstrumentID: inst.InstrumentID, Symbol: strings.ToUpper(symbol), Exchange: inst.PrimaryExchange,
		ValidFrom: from, ValidTo: inst.DelistedAt, Source: source})
	return inst.InstrumentID, true, nil
}

// RenameSymbol closes the current span at `at` and opens newSymbol for the same instrument
// (symbol change: same instrument_id, VP-010).
func (m *Memory) RenameSymbol(_ context.Context, instrumentID, newSymbol string, at int64, source string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for i, s := range m.Symbols {
		if s.InstrumentID == instrumentID && s.ValidTo == 0 {
			if strings.EqualFold(s.Symbol, newSymbol) {
				return nil
			}
			m.Symbols[i].ValidTo = at
			m.Symbols = append(m.Symbols, SymbolSpan{InstrumentID: instrumentID, Symbol: strings.ToUpper(newSymbol), Exchange: s.Exchange, ValidFrom: at, Source: source})
			return nil
		}
	}
	return fmt.Errorf("refdata: no open symbol span for %s", instrumentID)
}

// MarkDelisted ends the instrument's open symbol span and universe memberships at `at` (DF-006).
func (m *Memory) MarkDelisted(_ context.Context, instrumentID string, at int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	inst, ok := m.Instruments[instrumentID]
	if !ok {
		return fmt.Errorf("refdata: unknown instrument %s", instrumentID)
	}
	inst.DelistedAt = at
	m.Instruments[instrumentID] = inst
	for i, s := range m.Symbols {
		if s.InstrumentID == instrumentID && (s.ValidTo == 0 || s.ValidTo > at) {
			m.Symbols[i].ValidTo = at
		}
	}
	for i, mb := range m.Members {
		if mb.InstrumentID == instrumentID && (mb.ValidTo == 0 || mb.ValidTo > at) {
			m.Members[i].ValidTo = at
		}
	}
	return nil
}

// UpsertBrokerID records a broker identifier for the instrument (idempotent).
func (m *Memory) UpsertBrokerID(_ context.Context, b BrokerID) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, x := range m.BrokerIDs {
		if x.Broker == b.Broker && x.BrokerInstrumentID == b.BrokerInstrumentID && x.InstrumentID == b.InstrumentID {
			return nil
		}
	}
	m.BrokerIDs = append(m.BrokerIDs, b)
	return nil
}

// UpsertAttributes appends a new attribute span when flags changed (snapshots over time).
func (m *Memory) UpsertAttributes(_ context.Context, a Attributes) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for i, x := range m.Attrs {
		if x.InstrumentID == a.InstrumentID && x.Source == a.Source && x.ValidTo == 0 {
			if x.Tradable == a.Tradable && x.Shortable == a.Shortable && x.EasyToBorrow == a.EasyToBorrow && x.Fractionable == a.Fractionable && x.Marginable == a.Marginable {
				return false, nil
			}
			m.Attrs[i].ValidTo = a.ValidFrom
		}
	}
	m.Attrs = append(m.Attrs, a)
	return true, nil
}

// UpsertAction stores a corporate action idempotently on (source, source_id); the earliest
// available_at is kept (MD-011).
func (m *Memory) UpsertAction(_ context.Context, a CorporateAction) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := a.Source + "|" + a.SourceID
	if old, ok := m.Actions[k]; ok {
		if old.AvailableAt < a.AvailableAt {
			a.AvailableAt = old.AvailableAt
		}
		if old.IngestedAt < a.IngestedAt {
			a.IngestedAt = old.IngestedAt
		}
		m.Actions[k] = a
		return false, nil
	}
	m.Actions[k] = a
	return true, nil
}

// SetAgreement updates the cross-source agreement status of an action.
func (m *Memory) SetAgreement(_ context.Context, source, sourceID, status string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	k := source + "|" + sourceID
	a, ok := m.Actions[k]
	if !ok {
		return fmt.Errorf("refdata: unknown action %s", k)
	}
	a.Agreement = status
	m.Actions[k] = a
	return nil
}

// ActionsFor returns the actions of an instrument (all sources), by ex-date.
func (m *Memory) ActionsFor(_ context.Context, instrumentID string) []CorporateAction {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []CorporateAction
	for _, a := range m.Actions {
		if a.InstrumentID == instrumentID {
			out = append(out, a)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].ExDate < out[j].ExDate || (out[i].ExDate == out[j].ExDate && out[i].Source < out[j].Source)
	})
	return out
}

// SetUniverse makes `members` the universe's membership from `at` on: new members get a span
// starting at `at`, dropped members' spans end at `at` (DF-006).
func (m *Memory) SetUniverse(_ context.Context, universe string, members []string, at int64) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	want := map[string]bool{}
	for _, id := range members {
		want[id] = true
	}
	open := map[string]bool{}
	for i, mb := range m.Members {
		if mb.Universe != universe || mb.ValidTo != 0 {
			continue
		}
		open[mb.InstrumentID] = true
		if !want[mb.InstrumentID] {
			m.Members[i].ValidTo = at
		}
	}
	for _, id := range members {
		if !open[id] {
			m.Members = append(m.Members, Member{Universe: universe, InstrumentID: id, ValidFrom: at})
		}
	}
	return nil
}

// UniverseAt returns members valid at `at` whose instrument is listed then.
func (m *Memory) UniverseAt(_ context.Context, universe string, at int64) []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []string
	for _, mb := range m.Members {
		inst := m.Instruments[mb.InstrumentID]
		if mb.Universe == universe && within(mb.ValidFrom, mb.ValidTo, at) && (inst.DelistedAt == 0 || at < inst.DelistedAt) {
			out = append(out, mb.InstrumentID)
		}
	}
	sort.Strings(out)
	return out
}

// Export mirrors every table into the lake's ref/ directory (ST-007).
func (m *Memory) Export(ctx context.Context, l *lake.Lake) error {
	m.mu.Lock()
	insts := make([]Instrument, 0, len(m.Instruments))
	for _, i := range m.Instruments {
		insts = append(insts, i)
	}
	sort.Slice(insts, func(i, j int) bool { return insts[i].InstrumentID < insts[j].InstrumentID })
	syms := append([]SymbolSpan(nil), m.Symbols...)
	bids := append([]BrokerID(nil), m.BrokerIDs...)
	attrs := append([]Attributes(nil), m.Attrs...)
	acts := make([]CorporateAction, 0, len(m.Actions))
	for _, a := range m.Actions {
		acts = append(acts, a)
	}
	sort.Slice(acts, func(i, j int) bool { return acts[i].Source+acts[i].SourceID < acts[j].Source+acts[j].SourceID })
	mems := append([]Member(nil), m.Members...)
	m.mu.Unlock()
	return errors.Join(
		lake.WriteRef(ctx, l, "instruments", insts),
		lake.WriteRef(ctx, l, "symbols", syms),
		lake.WriteRef(ctx, l, "broker_ids", bids),
		lake.WriteRef(ctx, l, "attributes", attrs),
		lake.WriteRef(ctx, l, "corporate_actions", acts),
		lake.WriteRef(ctx, l, "universe_members", mems),
	)
}
