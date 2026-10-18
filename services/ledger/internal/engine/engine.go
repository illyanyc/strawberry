// Package engine runs the ledger (section 06, ADR-0024): it turns broker executions, snapshots,
// corporate actions, and owner actions into book events, persists every change atomically with its
// bus events (transactional outbox), reconciles, and answers the order-path queries. All mutations
// are serialized by one mutex; reads take a read lock. A failed commit reloads the state from the
// store, so memory never diverges from Postgres.
package engine

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/ledger/internal/book"
)

// Resolver maps a broker symbol or broker instrument ID to the platform instrument_id at time at
// ("" when unknown).
type Resolver interface {
	Resolve(ctx context.Context, symbol, brokerInstrumentID string, at int64) (string, error)
}

// StaticResolver is a symbol → instrument_id map (tests, development).
type StaticResolver map[string]string

// Resolve implements Resolver.
func (s StaticResolver) Resolve(_ context.Context, symbol, _ string, _ int64) (string, error) {
	return s[symbol], nil
}

// Config tunes the engine.
type Config struct {
	Recon   book.ReconConfig
	Capital book.CapitalConfig
	// EquityInterval between equity-curve points per owner (default 1 minute, VP-024).
	EquityInterval time.Duration
	Now            func() time.Time
	Calendar       *calendar.Calendar
}

// DefaultConfig returns the documented defaults.
func DefaultConfig() Config {
	return Config{Recon: book.DefaultReconConfig(), Capital: book.DefaultCapitalConfig(), EquityInterval: time.Minute, Now: time.Now, Calendar: calendar.XNYS()}
}

// Engine is the ledger.
type Engine struct {
	cfg   Config
	store Store
	res   Resolver
	log   *slog.Logger

	mu        sync.RWMutex
	b         *book.Book
	brk       *book.Breaks
	rsv       *book.Reservations
	snaps     map[string]*book.Snapshot
	rawSnaps  map[string]*ledgerv1.AccountSnapshot
	lastRecon map[string]*ledgerv1.ReconciliationResult
	equity    map[string]*equityState
	pending   map[string]*mdv1.CorporateAction
	sessions  map[string]string // last session-boundary trigger fired per kind
}

type equityState struct {
	lastBucket int64
	dayDate    string
	dayStart   decimal.Decimal
	hwm        decimal.Decimal
}

// New loads the state from store.
func New(ctx context.Context, cfg Config, store Store, res Resolver, log *slog.Logger) (*Engine, error) {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Calendar == nil {
		cfg.Calendar = calendar.XNYS()
	}
	if cfg.EquityInterval == 0 {
		cfg.EquityInterval = time.Minute
	}
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	e := &Engine{cfg: cfg, store: store, res: res, log: log}
	if err := e.load(ctx); err != nil {
		return nil, err
	}
	return e, nil
}

func (e *Engine) now() int64 { return e.cfg.Now().UnixNano() }

// load rebuilds the in-memory state: replay every event in order, then overlay the operational
// tables (breaks, reservations) which are authoritative for their current status.
func (e *Engine) load(ctx context.Context) error {
	st, err := e.store.Load(ctx)
	if err != nil {
		return err
	}
	b, brk, rsv := book.New(), book.NewBreaks(), book.NewReservations()
	for _, ev := range st.Events {
		if _, _, err := b.Apply(ev, brk, rsv); err != nil {
			return fmt.Errorf("ledger: replay %s: %w", ev.Key(), err)
		}
	}
	for _, x := range st.Breaks {
		brk.Put(x)
	}
	for _, x := range st.Reservations {
		rsv.Put(x)
	}
	e.b, e.brk, e.rsv = b, brk, rsv
	e.snaps, e.rawSnaps = map[string]*book.Snapshot{}, map[string]*ledgerv1.AccountSnapshot{}
	for _, s := range st.Snapshots {
		e.rawSnaps[s.GetBrokerAccountId()] = s
		e.snaps[s.GetBrokerAccountId()] = e.convertSnapshot(ctx, s)
		applyMarks(b.Account(s.GetBrokerAccountId()), e.snaps[s.GetBrokerAccountId()])
	}
	e.lastRecon = map[string]*ledgerv1.ReconciliationResult{}
	e.equity = map[string]*equityState{}
	for k, p := range st.EquityStart {
		e.equity[k] = &equityState{dayDate: p.Date, dayStart: p.DayStart, hwm: p.HWM}
	}
	e.pending = map[string]*mdv1.CorporateAction{}
	for k, ca := range st.PendingActions {
		e.pending[k] = ca
	}
	e.sessions = map[string]string{}
	return nil
}

// commit persists batch; on failure memory is rebuilt from the store (fail closed).
func (e *Engine) commit(ctx context.Context, batch *Batch) error {
	if err := e.store.Commit(ctx, batch); err != nil {
		e.log.ErrorContext(ctx, "ledger commit failed; reloading state", "err", err)
		if lerr := e.load(ctx); lerr != nil {
			return errors.Join(err, lerr)
		}
		return err
	}
	return nil
}

func newULID() string { return ulid.MustNew(ulid.Now(), rand.Reader).String() }

// ---- ingest

func (e *Engine) resolve(ctx context.Context, instr, symbol, brokerID string, at int64) string {
	if instr != "" {
		return instr
	}
	if e.res != nil {
		if id, err := e.res.Resolve(ctx, symbol, brokerID, at); err == nil && id != "" {
			return id
		}
	}
	return book.UnmappedKey(symbol)
}

// ownerFor attributes an execution (VP-001, VP-017).
func (e *Engine) ownerFor(clientOrderID string) (owner string, platformManual bool, unknownShort, intent string) {
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
	if o := e.b.OwnerOf(id.StrategyShortID); o != "" {
		return o, false, "", id.IntentID
	}
	return book.Manual, false, id.StrategyShortID, id.IntentID
}

// ApplyExecution books one broker execution (idempotent on broker_exec_id, OE-006).
func (e *Engine) ApplyExecution(ctx context.Context, x *ordersv1.ExecutionReport) error {
	qty, err := decimal.NewFromString(x.GetQty().GetValue())
	if err != nil {
		return fmt.Errorf("%w: qty", errBadInput)
	}
	px, err := decimal.NewFromString(x.GetPrice().GetValue())
	if err != nil {
		return fmt.Errorf("%w: price", errBadInput)
	}
	fees := book.Zero
	if v := x.GetFees().GetValue(); v != "" {
		if fees, err = decimal.NewFromString(v); err != nil {
			return fmt.Errorf("%w: fees", errBadInput)
		}
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	owner, pm, unknown, intent := e.ownerFor(x.GetClientOrderId())
	ev := book.Event{Kind: book.KindExecution, Account: x.GetBrokerAccountId(), Source: x.GetBrokerExecId(), Time: x.GetEventTime(),
		Exec: &book.Exec{ExecID: x.GetBrokerExecId(), ClientOrderID: x.GetClientOrderId(), IntentID: intent,
			InstrumentID: e.resolve(ctx, x.GetInstrumentId(), x.GetSymbol(), "", x.GetEventTime()), Symbol: x.GetSymbol(),
			Buy: x.GetSide() == commonv1.Side_SIDE_BUY, Qty: qty, Price: px, Fees: fees, Owner: owner, PlatformManual: pm, UnknownShortID: unknown}}
	return e.applyAndCommit(ctx, ev, x.GetCorrelationId(), nil)
}

var errBadInput = errors.New("ledger: invalid input")

// IsBadInput reports whether err is a permanent input error (poison message).
func IsBadInput(err error) bool { return errors.Is(err, errBadInput) }

func (e *Engine) applyAndCommit(ctx context.Context, ev book.Event, corr string, extra func(*Batch)) error {
	if e.b.IsApplied(ev) {
		return nil
	}
	fx, applied, err := e.b.Apply(ev, e.brk, e.rsv)
	if err != nil {
		if lerr := e.load(ctx); lerr != nil {
			return errors.Join(err, lerr)
		}
		return err
	}
	if !applied {
		return nil
	}
	batch := &Batch{Events: []book.Event{ev}}
	e.effectsToBatch(ev, fx, corr, batch)
	if extra != nil {
		extra(batch)
	}
	return e.commit(ctx, batch)
}

func (e *Engine) effectsToBatch(ev book.Event, fx *book.Effects, corr string, batch *Batch) {
	upd := &ledgerv1.LedgerUpdate{BrokerAccountId: ev.Account, EventId: ev.Key(), SourceId: ev.Source, AsOf: ev.Time, CorrelationId: corr}
	for _, k := range fx.Changed {
		a := e.b.Accounts[k.Account]
		o := a.Owner(k.Owner)
		p := o.Positions[k.InstrumentID]
		row := PositionRow{Account: k.Account, Owner: k.Owner, InstrumentID: k.InstrumentID, Qty: book.Zero, Cost: book.Zero, Realized: book.Zero}
		if p != nil {
			row.Qty, row.Cost, row.Realized, row.Lots = p.Qty(), p.Cost(), p.Realized, append([]book.Lot(nil), p.Lots...)
		}
		batch.Positions = append(batch.Positions, row)
		upd.Positions = append(upd.Positions, &ledgerv1.StrategyPosition{BrokerAccountId: k.Account, Owner: k.Owner, InstrumentId: k.InstrumentID,
			Qty: dec(row.Qty), CostBasis: dec(row.Cost), RealizedPnl: dec(row.Realized), AsOf: ev.Time})
	}
	if len(upd.Positions) > 0 {
		batch.Outbox = append(batch.Outbox, msg("ledger.update."+ev.Account, "ledger:"+ev.Key(), upd, corr))
	}
	batch.Reservations = append(batch.Reservations, fx.Reservations...)
	batch.Washes = append(batch.Washes, fx.Washes...)
	for _, b := range fx.Breaks {
		batch.Breaks = append(batch.Breaks, b)
		if b.IsOpen() && b.Severity == book.SeverityCritical && !b.Alerted {
			b.Alerted = true
			batch.Outbox = append(batch.Outbox, e.breakAlert(b, corr))
		}
	}
}

func (e *Engine) breakAlert(b *book.Break, corr string) outbox.Message {
	a := &alertsv1.Alert{AlertId: newULID(), Severity: alertsv1.Severity_SEVERITY_CRITICAL, Source: "ledger",
		Subject: "reconciliation break: " + b.Type, Message: fmt.Sprintf("%s %s: %s", b.Account, b.Symbol, b.Detail),
		DedupeKey: "ledger-break-" + b.ID, RunbookUrl: "docs/runbooks/reconciliation-break.md", OccurredAt: e.now(), CorrelationId: corr}
	return msg("alert.critical", "alert:"+a.GetAlertId(), a, corr)
}

func msg(subject, id string, m proto.Message, corr string) outbox.Message {
	b, _ := proto.Marshal(m)
	return outbox.Message{Subject: subject, MsgID: id, Payload: b, CorrelationID: corr}
}

func dec(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }

func parseDec(d *commonv1.Decimal) decimal.Decimal {
	if d == nil || d.GetValue() == "" {
		return book.Zero
	}
	v, err := decimal.NewFromString(d.GetValue())
	if err != nil {
		return book.Zero
	}
	return v
}

func (e *Engine) convertSnapshot(ctx context.Context, s *ledgerv1.AccountSnapshot) *book.Snapshot {
	out := &book.Snapshot{AsOf: s.GetAsOf(), Cash: parseDec(s.GetCash()), BuyingPower: parseDec(s.GetBuyingPower()), Equity: parseDec(s.GetEquity())}
	for _, p := range s.GetPositions() {
		out.Positions = append(out.Positions, book.SnapPos{InstrumentID: e.resolve(ctx, p.GetInstrumentId(), p.GetSymbol(), p.GetBrokerInstrumentId(), s.GetAsOf()),
			Symbol: p.GetSymbol(), Qty: parseDec(p.GetQty()), MarketValue: parseDec(p.GetMarketValue()), AvgCost: parseDec(p.GetAvgCost())})
	}
	return out
}

// OnSnapshot records a broker snapshot: the first one for an account books opening balances to
// `manual` (VP-015); every one updates marks, the equity curve, and reconciles (VP-007).
func (e *Engine) OnSnapshot(ctx context.Context, s *ledgerv1.AccountSnapshot) error {
	acct := s.GetBrokerAccountId()
	if acct == "" {
		return fmt.Errorf("%w: snapshot without account", errBadInput)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	if prev := e.rawSnaps[acct]; prev != nil && prev.GetAsOf() > s.GetAsOf() {
		return nil // out of order: keep the newer snapshot
	}
	snap := e.convertSnapshot(ctx, s)
	a := e.b.Account(acct)
	if !a.Opened {
		op := &book.Opening{Cash: snap.Cash}
		for _, p := range snap.Positions {
			op.Positions = append(op.Positions, book.OpeningPos{InstrumentID: p.InstrumentID, Symbol: p.Symbol, Qty: p.Qty, AvgCost: p.AvgCost})
		}
		ev := book.Event{Kind: book.KindOpening, Account: acct, Source: fmt.Sprintf("snapshot:%d", s.GetAsOf()), Time: s.GetAsOf(), Opening: op}
		if err := e.applyAndCommit(ctx, ev, "", func(b *Batch) {
			b.Audit = append(b.Audit, audit.Event{Actor: "service:ledger", Action: "ledger.opening_balance", Target: acct,
				Details: map[string]any{"positions": len(op.Positions), "cash": op.Cash.String()}})
		}); err != nil {
			return err
		}
	}
	applyMarks(a, snap)
	e.snaps[acct], e.rawSnaps[acct] = snap, s
	batch := &Batch{Snapshot: s}
	e.equityPoints(acct, snap.AsOf, batch)
	e.reconcileLocked(acct, "snapshot", "", batch)
	return e.commit(ctx, batch)
}

// applyMarks marks positions at the broker's price (market value / qty) and records symbols.
func applyMarks(a *book.Account, snap *book.Snapshot) {
	for _, p := range snap.Positions {
		if !p.Qty.IsZero() && !p.MarketValue.IsZero() {
			a.Marks[p.InstrumentID] = p.MarketValue.DivRound(p.Qty, 8)
		}
		if p.Symbol != "" {
			a.Symbols[p.InstrumentID] = p.Symbol
		}
	}
}

// Reconcile runs reconciliation now (on demand, reconnect, session boundaries).
func (e *Engine) Reconcile(ctx context.Context, acct, trigger, corr string) (*ledgerv1.ReconciliationResult, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	batch := &Batch{}
	r := e.reconcileLocked(acct, trigger, corr, batch)
	return r, e.commit(ctx, batch)
}

func (e *Engine) reconcileLocked(acct, trigger, corr string, batch *Batch) *ledgerv1.ReconciliationResult {
	a := e.b.Account(acct)
	r := book.Reconcile(a, e.snaps[acct], e.brk, e.now(), e.cfg.Recon, trigger)
	out := e.reconProto(r, corr)
	prev := e.lastRecon[acct]
	e.lastRecon[acct] = out
	batch.Breaks = append(batch.Breaks, r.Changed...)
	for _, b := range r.Changed {
		if b.IsOpen() && b.Severity == book.SeverityCritical && b.Type != book.BreakTiming && !b.Alerted {
			b.Alerted = true
			batch.Outbox = append(batch.Outbox, e.breakAlert(b, corr))
		}
	}
	batch.Outbox = append(batch.Outbox, msg("recon.result."+acct, fmt.Sprintf("recon:%s:%d:%s", acct, r.AsOf, trigger), out, corr))
	if prev == nil || prev.GetStatus() != out.GetStatus() || trigger != "snapshot" {
		batch.Reports = append(batch.Reports, out)
	}
	return out
}

func (e *Engine) reconProto(r book.ReconResult, corr string) *ledgerv1.ReconciliationResult {
	out := &ledgerv1.ReconciliationResult{BrokerAccountId: r.Account, AsOf: r.AsOf, SnapshotAsOf: r.SnapshotAsOf, Status: statusProto(r.Status),
		CorrelationId: corr, BrokerCash: dec(r.BrokerCash), LedgerCash: dec(r.LedgerCash), Halted: r.Halted, PositionsCompared: int32(r.Compared), Trigger: r.Trigger}
	for _, d := range r.Diffs {
		diff := &ledgerv1.ReconciliationDiff{InstrumentId: d.InstrumentID, BrokerQty: dec(d.BrokerQty), LedgerQty: dec(d.LedgerQty)}
		for _, o := range sortedKeys(d.Owners) {
			diff.OwnerBreakdown = append(diff.OwnerBreakdown, &ledgerv1.OwnerQty{Owner: o, Qty: dec(d.Owners[o])})
		}
		out.Diffs = append(out.Diffs, diff)
	}
	for _, b := range r.Open {
		out.Breaks = append(out.Breaks, breakProto(b))
	}
	return out
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func statusProto(s string) ledgerv1.ReconciliationStatus {
	switch s {
	case book.StatusOK:
		return ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK
	case book.StatusBreak:
		return ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_BREAK
	case book.StatusPending:
		return ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_PENDING
	default:
		return ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_STALE
	}
}

func (e *Engine) equityPoints(acct string, ts int64, batch *Batch) {
	a := e.b.Accounts[acct]
	bucket := ts / int64(e.cfg.EquityInterval)
	date := ""
	if s, _, ok := e.cfg.Calendar.SessionAt(time.Unix(0, ts)); ok {
		date = s.Date
	}
	for _, id := range a.OwnerIDs() {
		o := a.Owners[id]
		key := acct + "|" + id
		st := e.equity[key]
		if st == nil {
			st = &equityState{lastBucket: -1}
			e.equity[key] = st
		}
		v := a.Value(o)
		if date != "" && st.dayDate != date {
			st.dayDate, st.dayStart = date, v.Equity
		}
		if v.Equity.GreaterThan(st.hwm) {
			st.hwm = v.Equity
		}
		if bucket == st.lastBucket {
			continue
		}
		st.lastBucket = bucket
		cash := book.Zero
		if id != book.Manual {
			cash = o.Allocation.Add(o.Realized).Add(o.Dividends).Sub(o.Invested())
		}
		batch.Equity = append(batch.Equity, EquityRow{Account: acct, Owner: id, TS: ts, Date: st.dayDate, Equity: v.Equity, Cash: cash,
			MarketValue: v.MarketValue, Realized: o.Realized, Unrealized: v.Unrealized, Gross: v.Gross, DayStart: st.dayStart, HWM: st.hwm})
	}
}

// OnCorporateAction queues an action; Tick applies it on its ex-date (VP-010, VP-023).
func (e *Engine) OnCorporateAction(ctx context.Context, ca *mdv1.CorporateAction) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	key := ca.GetSourceId()
	if key == "" {
		key = fmt.Sprintf("%s:%s:%d", ca.GetInstrumentId(), ca.GetType(), ca.GetExDate())
	}
	if ca.GetAgreement() == mdv1.AgreementStatus_AGREEMENT_STATUS_DISAGREED {
		return nil // disputed: wait for the owner's resolution (MD-012) which re-publishes it
	}
	e.pending[key] = ca
	if err := e.commit(ctx, &Batch{PendingActions: map[string]*mdv1.CorporateAction{key: ca}}); err != nil {
		return err
	}
	return e.applyDueActionsLocked(ctx)
}

func (e *Engine) applyDueActionsLocked(ctx context.Context) error {
	now := e.now()
	for _, key := range sortedKeys(e.pending) {
		ca := e.pending[key]
		if ca.GetExDate() > now {
			continue
		}
		for _, acct := range e.b.AccountIDs() {
			a := e.b.Accounts[acct]
			held := !a.LedgerQty(ca.GetInstrumentId()).IsZero()
			var ev *book.Event
			switch ca.GetType() {
			case mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPLIT:
				ev = &book.Event{Kind: book.KindSplit, Split: &book.Split{InstrumentID: ca.GetInstrumentId(), Ratio: parseDec(ca.GetRatio())}}
			case mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DIVIDEND:
				ev = &book.Event{Kind: book.KindDividend, Dividend: &book.Dividend{InstrumentID: ca.GetInstrumentId(), Amount: parseDec(ca.GetAmount())}}
			case mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SYMBOL_CHANGE:
				ev = &book.Event{Kind: book.KindSymbolChange, Symbol: &book.SymbolChange{InstrumentID: ca.GetInstrumentId(), NewSymbol: ca.GetNewSymbol()}}
			default:
				if held {
					bk := e.brk.Open(book.Break{ID: "brk-ca-" + acct + "-" + key, Account: acct, Type: book.BreakCorporateAction, InstrumentID: ca.GetInstrumentId(),
						Symbol: a.Symbols[ca.GetInstrumentId()], Severity: book.SeverityCritical, Detected: now, Source: key, InstrumentWide: true,
						Detail: fmt.Sprintf("%s needs a manual resolution (no automatic rule)", ca.GetType()), Suggestions: []string{book.ResolutionAccept, book.ResolutionBookToManual}})
					if err := e.commit(ctx, &Batch{Breaks: []*book.Break{bk}, Outbox: []outbox.Message{e.breakAlert(bk, "")}}); err != nil {
						return err
					}
				}
			}
			if ev == nil || (!held && ev.Kind != book.KindSymbolChange) {
				continue
			}
			ev.Account, ev.Source, ev.Time = acct, key, now
			if err := e.applyAndCommit(ctx, *ev, "", nil); err != nil {
				return err
			}
		}
		delete(e.pending, key)
		if err := e.commit(ctx, &Batch{AppliedActions: []string{key}}); err != nil {
			return err
		}
	}
	return nil
}

// Tick runs time-driven work: due corporate actions, and reconciliation at the session open and
// close (the close run is the stored daily report, VP-025).
func (e *Engine) Tick(ctx context.Context) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if err := e.applyDueActionsLocked(ctx); err != nil {
		return err
	}
	now := e.cfg.Now()
	s, part, ok := e.cfg.Calendar.SessionAt(now)
	if !ok {
		if prev, ok := e.cfg.Calendar.PreviousSession(now); ok {
			s, part = prev, calendar.PartPost
		} else {
			return nil
		}
	}
	kind, boundary := "", time.Time{}
	switch {
	case part == calendar.PartRegular:
		kind, boundary = "open", s.Open
	case part == calendar.PartPost && !now.Before(s.Close):
		kind, boundary = "close", s.Close
	}
	if kind == "" {
		return nil
	}
	batch := &Batch{}
	for _, acct := range e.b.AccountIDs() {
		key := kind + "|" + acct
		// The boundary report reconciles against the first snapshot taken at or after the
		// boundary (VP-025), so it never compares against pre-close positions.
		if e.sessions[key] == s.Date || e.snaps[acct] == nil || e.snaps[acct].AsOf < boundary.UnixNano() {
			continue
		}
		e.sessions[key] = s.Date
		e.reconcileLocked(acct, "session_"+kind, "", batch)
	}
	return e.commit(ctx, batch)
}

// ---- owner actions

// RegisterOwner registers a strategy owner in an account (VP-012).
func (e *Engine) RegisterOwner(ctx context.Context, owner, shortID, acct string, allowShort, dedicated bool, actor string) error {
	if _, err := attribution.Parse(attribution.ID{StrategyShortID: shortID, IntentID: "00000000000000000000000000", Attempt: 1}.String(), 0); err != nil || shortID == attribution.ManualShortID {
		return fmt.Errorf("%w: short ID %q must be 6–8 lowercase alphanumerics and not %q", errBadInput, shortID, attribution.ManualShortID)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	reg := &book.Register{Owner: owner, ShortID: shortID, AllowShort: allowShort, Dedicated: dedicated}
	if a := e.b.Accounts[acct]; a != nil {
		if o := a.Owner(owner); o != nil && o.ShortID == shortID && o.AllowShort == allowShort && o.Dedicated == dedicated {
			return nil
		}
	}
	ev := book.Event{Kind: book.KindRegister, Account: acct, Source: fmt.Sprintf("%s:%s:%t:%t", owner, shortID, allowShort, dedicated), Time: e.now(), Actor: actor, Register: reg}
	return e.applyAndCommit(ctx, ev, "", func(b *Batch) {
		b.Audit = append(b.Audit, audit.Event{Actor: actor, Action: "ledger.owner.register", Target: acct + "/" + owner,
			Details: map[string]any{"short_id": shortID, "allow_short": allowShort, "dedicated": dedicated}})
	})
}

// SetAllocation changes an owner's allocation (VP-009). Step-up for increases is enforced by the
// gateway (RM-007); every change is audited here.
func (e *Engine) SetAllocation(ctx context.Context, owner, acct string, amount decimal.Decimal, actor, corr string) (prev, unallocated decimal.Decimal, err error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if err := e.b.CheckAllocation(acct, owner, amount, e.snaps[acct], e.cfg.Capital, e.rsv); err != nil {
		return book.Zero, book.Zero, fmt.Errorf("%w: %w", ErrRejected, err)
	}
	prev = e.b.Accounts[acct].Owner(owner).Allocation
	ev := book.Event{Kind: book.KindAllocation, Account: acct, Source: newULID(), Time: e.now(), Actor: actor, Alloc: &book.Alloc{Owner: owner, Amount: amount}}
	if err := e.applyAndCommit(ctx, ev, corr, func(b *Batch) {
		b.Audit = append(b.Audit, audit.Event{Actor: actor, Action: "ledger.allocation.set", Target: acct + "/" + owner, CorrelationID: corr,
			Details: map[string]any{"previous": prev.String(), "amount": amount.String()}})
		e.shiftDayStart(acct, owner, amount.Sub(prev), ev.Time, b)
	}); err != nil {
		return book.Zero, book.Zero, err
	}
	return prev, book.Allocatable(e.snaps[acct], e.cfg.Capital).Sub(e.b.TotalAllocated(acct, "")), nil
}

// shiftDayStart moves an owner's session-start equity and high-water mark by an allocation change,
// so capital flows never count as day P&L or drawdown (RM-005, VP-024). The adjusted values are
// written in the allocation's own transaction, so a restart restores them.
func (e *Engine) shiftDayStart(acct, owner string, delta decimal.Decimal, ts int64, b *Batch) {
	st := e.equity[acct+"|"+owner]
	if st == nil || delta.IsZero() {
		return
	}
	st.dayStart, st.hwm = st.dayStart.Add(delta), st.hwm.Add(delta)
	a := e.b.Accounts[acct]
	o := a.Owner(owner)
	v := a.Value(o)
	b.Equity = append(b.Equity, EquityRow{Account: acct, Owner: owner, TS: ts, Date: st.dayDate, Equity: v.Equity,
		Cash: o.Allocation.Add(o.Realized).Add(o.Dividends).Sub(o.Invested()), MarketValue: v.MarketValue, Realized: o.Realized,
		Unrealized: v.Unrealized, Gross: v.Gross, DayStart: st.dayStart, HWM: st.hwm})
}

// ErrRejected marks a business rejection (not an infrastructure failure).
var ErrRejected = errors.New("ledger: rejected")

// Reserve reserves capital or own quantity for an intent (VP-020). Idempotent on intent_id.
func (e *Engine) Reserve(ctx context.Context, r book.Reservation, short bool) (*book.Reservation, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if x := e.rsv.ByIntent(r.IntentID); x != nil {
		return x, nil
	}
	r.ID, r.CreatedAt = newULID(), e.now()
	x, err := e.b.Reserve(r, e.snaps[r.Account], e.cfg.Capital, e.brk, e.rsv, short)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrRejected, err)
	}
	e.rsv.Put(x)
	if err := e.commit(ctx, &Batch{Reservations: []*book.Reservation{x}}); err != nil {
		return nil, err
	}
	return x, nil
}

// Release drops a reservation by reservation or intent ID (terminal order states).
func (e *Engine) Release(ctx context.Context, reservationID, intentID string) (bool, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	x := e.rsv.Get(reservationID)
	if x == nil {
		x = e.rsv.ByIntent(intentID)
	}
	if x == nil {
		return false, nil
	}
	e.rsv.Delete(x)
	return true, e.commit(ctx, &Batch{DeletedReservations: []string{x.ID}})
}

// ResolveBreak applies an owner resolution (VP-019): accept, transfer_to_manual (moves quantity with
// its cost basis from a strategy to manual), book_to_manual (books the unexplained difference to
// manual), or rebaseline_cash. Every resolution is an event plus an audit record.
func (e *Engine) ResolveBreak(ctx context.Context, breakID, resolution, fromOwner string, qty decimal.Decimal, actor, note, corr string) (*book.Break, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	bk := e.brk.Get(breakID)
	if bk == nil {
		return nil, fmt.Errorf("%w: break %q not found", ErrNotFound, breakID)
	}
	if !bk.IsOpen() {
		return nil, fmt.Errorf("%w: break %q is %s", ErrRejected, breakID, bk.Status)
	}
	src := newULID()
	var pre *book.Event
	switch resolution {
	case book.ResolutionAccept:
	case book.ResolutionTransferToManual:
		if fromOwner == "" || !qty.IsPositive() {
			return nil, fmt.Errorf("%w: transfer needs from_owner and a positive qty", ErrRejected)
		}
		pre = &book.Event{Kind: book.KindTransfer, Transfer: &book.Transfer{From: fromOwner, To: book.Manual, InstrumentID: bk.InstrumentID, Qty: qty}}
	case book.ResolutionBookToManual:
		diff := bk.BrokerQty.Sub(bk.LedgerQty)
		if diff.IsZero() || bk.InstrumentID == "" {
			return nil, fmt.Errorf("%w: nothing to book", ErrRejected)
		}
		mark := e.b.Accounts[bk.Account].Marks[bk.InstrumentID]
		pre = &book.Event{Kind: book.KindManualAdjust, Adjust: &book.ManualAdjust{InstrumentID: bk.InstrumentID, Symbol: bk.Symbol, Qty: diff, Price: mark}}
	case book.ResolutionRebaselineCash:
		s := e.snaps[bk.Account]
		if s == nil {
			return nil, fmt.Errorf("%w: no snapshot to rebaseline from", ErrRejected)
		}
		pre = &book.Event{Kind: book.KindCashRebaseline, Cash: &book.CashRebaseline{Cash: s.Cash}}
	default:
		return nil, fmt.Errorf("%w: unknown resolution %q", ErrRejected, resolution)
	}
	if pre != nil {
		pre.Account, pre.Source, pre.Time, pre.Actor = bk.Account, src, e.now(), actor
		if err := e.applyAndCommit(ctx, *pre, corr, nil); err != nil {
			return nil, fmt.Errorf("%w: %w", ErrRejected, err)
		}
	}
	ev := book.Event{Kind: book.KindResolve, Account: bk.Account, Source: src, Time: e.now(), Actor: actor,
		Resolve: &book.Resolve{BreakID: breakID, Resolution: resolution, Note: note}}
	err := e.applyAndCommit(ctx, ev, corr, func(b *Batch) {
		b.Audit = append(b.Audit, audit.Event{Actor: actor, Action: "ledger.break.resolve", Target: bk.Account + "/" + breakID, CorrelationID: corr,
			Details: map[string]any{"type": bk.Type, "resolution": resolution, "from_owner": fromOwner, "qty": qty.String(), "note": note}})
	})
	if err != nil {
		return nil, err
	}
	return bk, nil
}

// ErrNotFound marks unknown IDs.
var ErrNotFound = errors.New("ledger: not found")

// ---- reads

// Snapshot returns the latest raw broker snapshot.
func (e *Engine) Snapshot(acct string) *ledgerv1.AccountSnapshot {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.rawSnaps[acct]
}

// LastRecon returns the latest reconciliation result.
func (e *Engine) LastRecon(acct string) *ledgerv1.ReconciliationResult {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.lastRecon[acct]
}

// Constraints answers the order-path ownership query (VP-003..VP-005).
func (e *Engine) Constraints(acct, instr, owner string) book.Constraints {
	e.mu.RLock()
	defer e.mu.RUnlock()
	var bq *decimal.Decimal
	if s := e.snaps[acct]; s != nil {
		q := s.Qty(instr)
		bq = &q
	}
	return e.b.Constraints(acct, instr, owner, bq, e.brk, e.rsv)
}

// Capital answers the capital query.
func (e *Engine) Capital(acct, owner string) book.Capital {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return e.b.Capital(acct, owner, e.snaps[acct], e.cfg.Capital, e.rsv)
}

// AccountForOwner returns the account a strategy owner is registered in ("" if none).
func (e *Engine) AccountForOwner(owner string) string {
	e.mu.RLock()
	defer e.mu.RUnlock()
	for _, id := range e.b.AccountIDs() {
		if e.b.Accounts[id].Owner(owner) != nil {
			return id
		}
	}
	return ""
}

// State returns a JSON dump of the book (rebuild test).
func (e *Engine) State() ([]byte, error) {
	e.mu.RLock()
	defer e.mu.RUnlock()
	return json.Marshal(e.b)
}
