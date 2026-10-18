package engine

import (
	"context"
	"fmt"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/services/ledger/internal/book"
)

const acct = "sim-paper"

type clock struct{ t atomic.Int64 }

func (c *clock) now() time.Time      { return time.Unix(0, c.t.Load()) }
func (c *clock) add(d time.Duration) { c.t.Add(int64(d)) }

func D(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: s} }

type harness struct {
	t     *testing.T
	e     *Engine
	store Store
	clk   *clock
	seq   int
	pos   map[string]decimal.Decimal
	cash  decimal.Decimal
}

func newHarness(t *testing.T, store Store) *harness {
	t.Helper()
	clk := &clock{}
	clk.t.Store(time.Date(2026, 10, 14, 14, 0, 0, 0, time.UTC).UnixNano())
	cfg := DefaultConfig()
	cfg.Now = clk.now
	e, err := New(context.Background(), cfg, store, StaticResolver{"X": "INSTX", "Y": "INSTY"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	h := &harness{t: t, e: e, store: store, clk: clk, pos: map[string]decimal.Decimal{"X": decimal.NewFromInt(100)}, cash: decimal.NewFromInt(100000)}
	h.snapshot()
	for _, s := range []struct{ owner, short string }{{"orb-1", "orbaaaa"}, {"swing-1", "swingaa"}} {
		if err := e.RegisterOwner(context.Background(), s.owner, s.short, acct, false, false, "test"); err != nil {
			t.Fatal(err)
		}
		if _, _, err := e.SetAllocation(context.Background(), s.owner, acct, decimal.NewFromInt(20000), "owner", ""); err != nil {
			t.Fatal(err)
		}
	}
	return h
}

func (h *harness) snapshot() {
	h.t.Helper()
	s := &ledgerv1.AccountSnapshot{BrokerAccountId: acct, AsOf: h.clk.now().UnixNano(), Cash: D(h.cash.String()), BuyingPower: D(h.cash.String()),
		Equity: D(h.cash.Add(h.pos["X"].Mul(decimal.NewFromInt(50))).Add(h.pos["Y"].Mul(decimal.NewFromInt(50))).String())}
	syms := make([]string, 0, len(h.pos))
	for sym := range h.pos {
		syms = append(syms, sym)
	}
	sort.Strings(syms)
	for _, sym := range syms {
		if q := h.pos[sym]; !q.IsZero() {
			s.Positions = append(s.Positions, &ledgerv1.Position{Symbol: sym, Qty: D(q.String()), MarketValue: D(q.Mul(decimal.NewFromInt(50)).String()), AvgCost: D("50")})
		}
	}
	if err := h.e.OnSnapshot(context.Background(), s); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) exec(short, sym string, side commonv1.Side, qty, px string) {
	h.t.Helper()
	h.seq++
	coid := fmt.Sprintf("app-%d", h.seq)
	if short != "" {
		coid = attribution.ID{StrategyShortID: short, IntentID: fmt.Sprintf("01J%023d", h.seq), Attempt: 1}.String()
	}
	q := decimal.RequireFromString(qty)
	if side != commonv1.Side_SIDE_BUY {
		q = q.Neg()
	}
	h.pos[sym] = h.pos[sym].Add(q)
	h.cash = h.cash.Sub(q.Mul(decimal.RequireFromString(px)))
	if err := h.e.ApplyExecution(context.Background(), &ordersv1.ExecutionReport{BrokerAccountId: acct, BrokerExecId: fmt.Sprintf("x%d", h.seq),
		ClientOrderId: coid, Symbol: sym, Side: side, Qty: D(qty), Price: D(px), EventTime: h.clk.now().UnixNano()}); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) owners(instr string) map[string]string {
	out := map[string]string{}
	for _, ib := range h.e.AccountView(acct).GetInstruments() {
		if ib.GetInstrumentId() == instr {
			for _, o := range ib.GetOwners() {
				out[o.GetOwner()] = o.GetQty().GetValue()
			}
		}
	}
	return out
}

// Section 06 same-ticker scenario end to end through the engine (VP-003, VP-004, VP-015).
func TestVP003SameTickerThroughEngine(t *testing.T) {
	h := newHarness(t, NewMemory())
	if got := h.owners("INSTX"); got["manual"] != "100" || len(got) != 1 {
		t.Fatalf("opening balance = %v, want manual 100 (VP-015)", got)
	}
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_BUY, "50", "52")
	h.snapshot()
	if c := h.e.Constraints(acct, "INSTX", "orb-1"); !c.MaxSellable.Equal(decimal.NewFromInt(50)) {
		t.Fatalf("max sellable = %s", c.MaxSellable)
	}
	if _, err := h.e.Reserve(context.Background(), book.Reservation{IntentID: "I-sell60", Account: acct, Owner: "orb-1", InstrumentID: "INSTX", Qty: decimal.NewFromInt(60), EstPrice: decimal.NewFromInt(52)}, false); err == nil {
		t.Fatal("selling 60 with 50 owned must be rejected")
	}
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_SELL, "50", "53")
	h.clk.add(15 * time.Second)
	h.snapshot()
	if got := h.owners("INSTX"); got["manual"] != "100" || got["orb-1"] != "" {
		t.Fatalf("owners = %v, want manual 100 only", got)
	}
	if r := h.e.LastRecon(acct); r.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK {
		t.Fatalf("recon = %s %v", r.GetStatus(), r.GetDiffs())
	}
}

// RM-005, VP-024: allocation changes are capital flows, not P&L. Day P&L and the high-water mark
// move only with trading results, including when the session start was captured before the first
// allocation, and the adjusted session start survives a reload from the store.
func TestRM005AllocationChangesAreNotDayPnL(t *testing.T) {
	store := NewMemory()
	h := newHarness(t, store)
	ctx := context.Background()
	if err := h.e.RegisterOwner(ctx, "late-1", "lateaaa", acct, false, false, "test"); err != nil {
		t.Fatal(err)
	}
	h.clk.add(time.Minute)
	h.snapshot()
	dayPnL := func(owner string) string {
		for _, o := range h.e.AccountView(acct).GetOwners() {
			if o.GetOwner() == owner {
				return o.GetDayPnl().GetValue()
			}
		}
		return "missing"
	}
	if got := dayPnL("late-1"); got != "0" {
		t.Fatalf("late-1 before allocation: day P&L %s", got)
	}
	steps := []struct{ owner, amount string }{{"late-1", "10000"}, {"orb-1", "25000"}, {"orb-1", "15000"}}
	for _, s := range steps {
		if _, _, err := h.e.SetAllocation(ctx, s.owner, acct, decimal.RequireFromString(s.amount), "owner", ""); err != nil {
			t.Fatal(err)
		}
		h.clk.add(time.Minute)
		h.snapshot()
		if got := dayPnL(s.owner); got != "0" {
			t.Fatalf("%s after allocation %s: day P&L %s, want 0", s.owner, s.amount, got)
		}
	}
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_BUY, "10", "50")
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_SELL, "10", "51")
	h.clk.add(time.Minute)
	h.snapshot()
	if got := dayPnL("orb-1"); got != "10" {
		t.Fatalf("orb-1 day P&L after a 10 gain: %s", got)
	}
	reloaded, err := New(ctx, h.e.cfg, store, StaticResolver{"X": "INSTX", "Y": "INSTY"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	h.e = reloaded
	if got, late := dayPnL("orb-1"), dayPnL("late-1"); got != "10" || late != "0" {
		t.Fatalf("after reload: orb-1 %s, late-1 %s", got, late)
	}
}

// Encroachment within one snapshot cycle, halt, and audited resolution (VP-016, VP-019).
func TestVP016EncroachmentThroughEngine(t *testing.T) {
	mem := NewMemory()
	h := newHarness(t, mem)
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_BUY, "50", "52")
	h.exec("", "X", commonv1.Side_SIDE_SELL, "120", "53")
	h.snapshot()
	breaks := h.e.Breaks(acct, true)
	if len(breaks) != 1 || breaks[0].GetType() != ledgerv1.BreakType_BREAK_TYPE_ENCROACHMENT {
		t.Fatalf("breaks = %v", breaks)
	}
	r := h.e.LastRecon(acct)
	if r.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_BREAK || !HaltedMatch(r.GetHalted(), "orb-1", "INSTX") || HaltedMatch(r.GetHalted(), "swing-1", "INSTX") {
		t.Fatalf("recon %s halted %v", r.GetStatus(), r.GetHalted())
	}
	if n := len(mem.Messages("alert.critical")); n != 1 {
		t.Fatalf("critical alerts = %d", n)
	}
	if _, err := h.e.ResolveBreak(context.Background(), breaks[0].GetBreakId(), book.ResolutionTransferToManual, "orb-1", decimal.NewFromInt(20), "owner", "sold in app", ""); err != nil {
		t.Fatal(err)
	}
	h.snapshot()
	if r := h.e.LastRecon(acct); r.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK {
		t.Fatalf("after resolution %s", r.GetStatus())
	}
	found := false
	for _, a := range mem.Audits {
		found = found || a.Action == "ledger.break.resolve"
	}
	if !found {
		t.Fatal("resolution not audited")
	}
}

// Split through the corporate-action path, applied on the ex-date (VP-010).
func TestVP010SplitOnExDate(t *testing.T) {
	h := newHarness(t, NewMemory())
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_BUY, "50", "52")
	ex := h.clk.now().Add(time.Hour).UnixNano()
	if err := h.e.OnCorporateAction(context.Background(), &mdv1.CorporateAction{InstrumentId: "INSTX", Type: mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPLIT,
		ExDate: ex, Ratio: D("4"), SourceId: "split-1", Agreement: mdv1.AgreementStatus_AGREEMENT_STATUS_AGREED}); err != nil {
		t.Fatal(err)
	}
	if got := h.owners("INSTX"); got["orb-1"] != "50" {
		t.Fatalf("split applied before the ex-date: %v", got)
	}
	h.clk.add(time.Hour)
	if err := h.e.Tick(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := h.owners("INSTX"); got["orb-1"] != "200" || got["manual"] != "400" {
		t.Fatalf("after split %v", got)
	}
	h.pos["X"] = decimal.NewFromInt(600)
	h.snapshot()
	if r := h.e.LastRecon(acct); r.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK {
		t.Fatalf("recon %s %v", r.GetStatus(), r.GetDiffs())
	}
}

// Concurrent reservations from two strategies cannot exceed available cash (section 06, VP-020).
func TestVP020ConcurrentReservations(t *testing.T) {
	h := newHarness(t, NewMemory())
	h.cash = decimal.NewFromInt(30000) // broker buying power below the 40k of allocations
	h.snapshot()
	var wg sync.WaitGroup
	var ok atomic.Int64
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			owner := []string{"orb-1", "swing-1"}[i%2]
			if _, err := h.e.Reserve(context.Background(), book.Reservation{IntentID: fmt.Sprintf("I%03d", i), Account: acct, Owner: owner, InstrumentID: "INSTY",
				Buy: true, Qty: decimal.NewFromInt(10), EstPrice: decimal.NewFromInt(100)}, false); err == nil {
				ok.Add(1)
			}
		}(i)
	}
	wg.Wait()
	if ok.Load() != 30 {
		t.Fatalf("accepted %d reservations of 1,000; want exactly 30 within 30,000 buying power", ok.Load())
	}
}

// Rebuild (section 06): the event log replayed from scratch equals the materialized state, and a
// restarted engine has the same book.
func TestVPRebuildFromEventsMatchesMaterialized(t *testing.T) {
	store := NewMemory()
	h := newHarness(t, store)
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_BUY, "50", "52")
	h.exec("swingaa", "Y", commonv1.Side_SIDE_BUY, "30", "48")
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_SELL, "20", "55")
	h.exec("", "Y", commonv1.Side_SIDE_BUY, "7", "49")
	h.snapshot()
	diffs, err := h.e.VerifyMaterialized(context.Background())
	if err != nil || len(diffs) > 0 {
		t.Fatalf("materialized vs replay: %v %v", diffs, err)
	}
	before, _ := h.e.State()
	cfg := DefaultConfig()
	cfg.Now = h.clk.now
	e2, err := New(context.Background(), cfg, store, StaticResolver{"X": "INSTX", "Y": "INSTY"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	after, _ := e2.State()
	if string(before) != string(after) {
		t.Fatal("restarted engine state differs from the original")
	}
}

// A failed commit leaves memory equal to the store (fail closed).
func TestLedgerCommitFailureReloads(t *testing.T) {
	store := NewMemory()
	h := newHarness(t, store)
	store.FailNext = fmt.Errorf("disk full")
	if err := h.e.ApplyExecution(context.Background(), &ordersv1.ExecutionReport{BrokerAccountId: acct, BrokerExecId: "fail1", Symbol: "X",
		Side: commonv1.Side_SIDE_BUY, Qty: D("5"), Price: D("50"), EventTime: 1}); err == nil {
		t.Fatal("commit failure must surface")
	}
	if got := h.owners("INSTX"); got["manual"] != "100" {
		t.Fatalf("memory diverged after failed commit: %v", got)
	}
}

// Performance targets (section 06): ownership query p99 < 5 ms; reconciling 500 positions < 1 s.
func TestVPPerformanceTargets(t *testing.T) {
	h := newHarness(t, NewMemory())
	for i := 0; i < 500; i++ {
		sym := fmt.Sprintf("S%03d", i)
		h.pos[sym] = decimal.NewFromInt(int64(10 + i))
	}
	h.e.mu.Lock()
	a := h.e.b.Account(acct)
	for i := 0; i < 500; i++ {
		sym := fmt.Sprintf("S%03d", i)
		p := book.UnmappedKey(sym)
		h.e.b.Accounts[acct].Owners["manual"].Positions[p] = &book.Position{InstrumentID: p, Lots: []book.Lot{{ID: "l" + sym, Owner: "manual", Qty: decimal.NewFromInt(int64(10 + i)), Cost: decimal.NewFromInt(500), Source: "test"}}}
	}
	_ = a
	h.e.mu.Unlock()
	start := time.Now()
	h.snapshot()
	if el := time.Since(start); el > time.Second {
		t.Fatalf("snapshot + reconcile of 500 positions took %s", el)
	}
	var lat []time.Duration
	for i := 0; i < 2000; i++ {
		s := time.Now()
		h.e.Constraints(acct, "INSTX", "orb-1")
		lat = append(lat, time.Since(s))
	}
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	if p99 := lat[len(lat)*99/100]; p99 > 5*time.Millisecond {
		t.Fatalf("GetOwnershipConstraints p99 = %s", p99)
	}
}

// Postgres-backed store (integration; TB_TEST_PG_SUPER_DSN): the same scenario, rebuild check,
// and restart against the real schema and svc_ledger privileges.
func TestVPPostgresStoreRebuild(t *testing.T) {
	pool := pgPool(t)
	store := &Postgres{Pool: pool}
	h := newHarness(t, store)
	h.exec("orbaaaa", "X", commonv1.Side_SIDE_BUY, "50", "52")
	h.exec("", "X", commonv1.Side_SIDE_SELL, "120", "53")
	h.snapshot()
	if _, err := h.e.Reserve(context.Background(), book.Reservation{IntentID: "IPG", Account: acct, Owner: "swing-1", InstrumentID: "INSTY", Buy: true,
		Qty: decimal.NewFromInt(5), EstPrice: decimal.NewFromInt(10)}, false); err != nil {
		t.Fatal(err)
	}
	diffs, err := h.e.VerifyMaterialized(context.Background())
	if err != nil || len(diffs) > 0 {
		t.Fatalf("materialized vs replay: %v %v", diffs, err)
	}
	cfg := DefaultConfig()
	cfg.Now = h.clk.now
	e2, err := New(context.Background(), cfg, store, StaticResolver{"X": "INSTX", "Y": "INSTY"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	a, _ := h.e.State()
	b, _ := e2.State()
	if string(a) != string(b) || len(e2.Breaks(acct, true)) != 1 || e2.Capital(acct, "swing-1").Reserved.IsZero() {
		t.Fatal("restart from Postgres lost state")
	}
	var n int
	if err := pool.QueryRow(context.Background(), `SELECT count(*) FROM ledger.outbox`).Scan(&n); err != nil || n == 0 {
		t.Fatalf("outbox rows = %d %v", n, err)
	}
	if _, err := pool.Exec(context.Background(), `UPDATE ledger.events SET actor='x'`); err == nil {
		t.Fatal("ledger.events must be append-only for svc_ledger (VP-I3)")
	}
}

func pgPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	super := os.Getenv("TB_TEST_PG_SUPER_DSN")
	if super == "" {
		t.Skip("TB_TEST_PG_SUPER_DSN not set")
	}
	ctx := context.Background()
	const name = "ledger_test"
	admin, err := pgx.Connect(ctx, super)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = admin.Exec(ctx, "DROP DATABASE IF EXISTS "+name+" WITH (FORCE)")
	admin.Close(ctx)
	pw := map[string]string{}
	for _, r := range append(append([]string{}, db.LoginRoles...), db.AppDatabases...) {
		pw[r] = "test-pw-" + r
	}
	if err := db.Bootstrap(ctx, super, name, pw); err != nil {
		t.Fatal(err)
	}
	cfg, _ := pgx.ParseConfig(super)
	c := db.Config{Host: cfg.Host, Port: int(cfg.Port), Name: name, User: "svc_ledger", SSLMode: "disable"}
	pool, err := pgxpool.New(ctx, c.DSN(pw["svc_ledger"]))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}
