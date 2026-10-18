package book

import (
	"fmt"
	"math/rand/v2"
	"testing"

	"github.com/shopspring/decimal"
)

func d(s string) decimal.Decimal { return decimal.RequireFromString(s) }

type fixture struct {
	b   *Book
	brk *Breaks
	res *Reservations
	seq int
	t   *testing.T
}

func newFixture(t *testing.T) *fixture {
	f := &fixture{b: New(), brk: NewBreaks(), res: NewReservations(), t: t}
	f.apply(Event{Kind: KindOpening, Account: "acct", Source: "s0", Time: 1, Opening: &Opening{Cash: d("100000"),
		Positions: []OpeningPos{{InstrumentID: "X", Symbol: "X", Qty: d("100"), AvgCost: d("50")}}}})
	f.register("alpha-1", "alphaaa")
	f.register("beta-1", "betaaaa")
	return f
}

func (f *fixture) apply(e Event) *Effects {
	f.t.Helper()
	fx, _, err := f.b.Apply(e, f.brk, f.res)
	if err != nil {
		f.t.Fatalf("apply %s: %v", e.Kind, err)
	}
	return fx
}

func (f *fixture) register(owner, short string) {
	f.apply(Event{Kind: KindRegister, Account: "acct", Source: owner, Register: &Register{Owner: owner, ShortID: short}})
	f.apply(Event{Kind: KindAllocation, Account: "acct", Source: owner + "-alloc", Alloc: &Alloc{Owner: owner, Amount: d("20000")}})
}

func (f *fixture) fill(owner, instr string, buy bool, qty, px string) *Effects {
	f.seq++
	return f.apply(Event{Kind: KindExecution, Account: "acct", Source: fmt.Sprintf("e%d", f.seq), Time: int64(100 + f.seq),
		Exec: &Exec{ExecID: fmt.Sprintf("e%d", f.seq), InstrumentID: instr, Symbol: instr, Buy: buy, Qty: d(qty), Price: d(px), Owner: owner}})
}

func (f *fixture) qty(owner, instr string) decimal.Decimal {
	return f.b.Accounts["acct"].Owner(owner).Qty(instr)
}

func (f *fixture) constraints(owner, instr string) Constraints {
	return f.b.Constraints("acct", instr, owner, nil, f.brk, f.res)
}

// Same-ticker scenario (section 06 acceptance; VP-003, VP-004, VP-011, VP-I2).
func TestVP003SameTickerManualUntouched(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "X", true, "50", "60")
	if c := f.constraints("alpha-1", "X"); !c.MaxSellable.Equal(d("50")) {
		t.Fatalf("max sellable = %s, want 50", c.MaxSellable)
	}
	// A 60-share sell exceeds A's own quantity: rejected by the reservation check.
	if _, err := f.b.Reserve(Reservation{ID: "r1", IntentID: "i1", Account: "acct", Owner: "alpha-1", InstrumentID: "X", Qty: d("60"), EstPrice: d("60")},
		nil, DefaultCapitalConfig(), f.brk, f.res, false); err == nil {
		t.Fatal("sell of 60 with 50 owned must be rejected (VP-004)")
	}
	f.fill("alpha-1", "X", false, "50", "61")
	if !f.qty(Manual, "X").Equal(d("100")) || !f.qty("alpha-1", "X").IsZero() {
		t.Fatalf("manual=%s A=%s, want 100 / 0", f.qty(Manual, "X"), f.qty("alpha-1", "X"))
	}
	if r := f.b.Accounts["acct"].Owner("alpha-1").Realized; !r.Equal(d("50")) {
		t.Fatalf("A realized = %s, want 50", r)
	}
	// The broker FIFO shadow ledger relieved manual's oldest lot (tax-lot divergence, VP-021).
	sh := f.b.Accounts["acct"].Shadow["X"]
	total := Zero
	for _, l := range sh {
		total = total.Add(l.Qty)
	}
	if !total.Equal(d("100")) || sh[len(sh)-1].Owner != "alpha-1" {
		t.Fatalf("shadow lots = %+v", sh)
	}
}

// Short block (VP-005): manual long X in a shared account.
func TestVP005ShortBlockedWhileManualLong(t *testing.T) {
	f := newFixture(t)
	a := f.b.Accounts["acct"].Owner("alpha-1")
	a.AllowShort = true
	c := f.constraints("alpha-1", "X")
	if c.ShortAllowed || c.Reason == "" {
		t.Fatalf("short allowed = %v (%q), want false with reason", c.ShortAllowed, c.Reason)
	}
	a.Dedicated = true
	if c := f.constraints("alpha-1", "X"); c.ShortAllowed {
		t.Fatalf("short allowed with manual holding 100: %+v", c)
	}
	if c := f.constraints("alpha-1", "Y"); !c.ShortAllowed {
		t.Fatalf("dedicated short with no other owners must be allowed: %+v", c)
	}
}

// Two strategies, one symbol (VP-011): selling A's lots never reduces B's quantity.
func TestVP011TwoStrategiesOneSymbol(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "X", true, "30", "60")
	f.fill("beta-1", "X", true, "20", "61")
	f.fill("alpha-1", "X", false, "30", "62")
	if !f.qty("beta-1", "X").Equal(d("20")) || !f.qty("alpha-1", "X").IsZero() || !f.qty(Manual, "X").Equal(d("100")) {
		t.Fatalf("A=%s B=%s manual=%s", f.qty("alpha-1", "X"), f.qty("beta-1", "X"), f.qty(Manual, "X"))
	}
}

// Encroachment (VP-016): owner sells 120 X in the broker app while manual = 100 and A = 50.
func TestVP016Encroachment(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "X", true, "50", "60")
	fx := f.fill(Manual, "X", false, "120", "61")
	if len(fx.Breaks) != 1 || fx.Breaks[0].Type != BreakEncroachment || fx.Breaks[0].Affected[0] != "alpha-1" {
		t.Fatalf("breaks = %+v", fx.Breaks)
	}
	if !f.brk.Halted("acct", "alpha-1", "X") || f.brk.Halted("acct", "beta-1", "X") {
		t.Fatal("A must be halted for X, B not")
	}
	// Invariant 1 still holds: Σ owners = 30 = broker.
	if q := f.b.Accounts["acct"].LedgerQty("X"); !q.Equal(d("30")) {
		t.Fatalf("ledger qty = %s", q)
	}
	// Owner resolution: transfer 20 from A to manual, audited as an event (VP-019).
	f.apply(Event{Kind: KindTransfer, Account: "acct", Source: "res1", Transfer: &Transfer{From: "alpha-1", To: Manual, InstrumentID: "X", Qty: d("20")}})
	f.apply(Event{Kind: KindResolve, Account: "acct", Source: "res1", Actor: "owner", Resolve: &Resolve{BreakID: fx.Breaks[0].ID, Resolution: ResolutionTransferToManual}})
	if !f.qty(Manual, "X").IsZero() || !f.qty("alpha-1", "X").Equal(d("30")) || f.brk.Halted("acct", "alpha-1", "X") {
		t.Fatalf("after resolution manual=%s A=%s halted=%v", f.qty(Manual, "X"), f.qty("alpha-1", "X"), f.brk.Halted("acct", "alpha-1", "X"))
	}
}

// Split (VP-010): a 4:1 split adjusts every owner's quantity and cost basis; invariant 1 holds.
func TestVP010Split(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "X", true, "50", "60")
	f.fill("beta-1", "X", true, "10", "64")
	costA := f.b.Accounts["acct"].Owner("alpha-1").Positions["X"].Cost()
	f.apply(Event{Kind: KindSplit, Account: "acct", Source: "ca1", Split: &Split{InstrumentID: "X", Ratio: d("4")}})
	if !f.qty(Manual, "X").Equal(d("400")) || !f.qty("alpha-1", "X").Equal(d("200")) || !f.qty("beta-1", "X").Equal(d("40")) {
		t.Fatalf("after split manual=%s A=%s B=%s", f.qty(Manual, "X"), f.qty("alpha-1", "X"), f.qty("beta-1", "X"))
	}
	p := f.b.Accounts["acct"].Owner("alpha-1").Positions["X"]
	if !p.Cost().Equal(costA) || !p.Cost().Div(p.Qty()).Equal(d("15")) {
		t.Fatalf("A cost %s avg %s", p.Cost(), p.Cost().Div(p.Qty()))
	}
	snap := &Snapshot{AsOf: 1000, Cash: f.b.Accounts["acct"].Cash, Positions: []SnapPos{{InstrumentID: "X", Qty: d("640")}}}
	if r := Reconcile(f.b.Accounts["acct"], snap, f.brk, 1000, DefaultReconConfig(), "test"); r.Status != StatusOK {
		t.Fatalf("recon after split = %s %+v", r.Status, r.Diffs)
	}
}

// Over-allocation (VP-009): allocations above allocatable capital are rejected.
func TestVP009OverAllocation(t *testing.T) {
	f := newFixture(t)
	snap := &Snapshot{Equity: d("105000"), BuyingPower: d("100000")}
	cfg := DefaultCapitalConfig() // 10% reserve: allocatable 94,500; B holds 20,000
	if err := f.b.CheckAllocation("acct", "alpha-1", d("74500"), snap, cfg, f.res); err != nil {
		t.Fatalf("allocation up to the limit must pass: %v", err)
	}
	if err := f.b.CheckAllocation("acct", "alpha-1", d("74500.01"), snap, cfg, f.res); err == nil {
		t.Fatal("allocation above allocatable capital must be rejected")
	}
	f.fill("alpha-1", "X", true, "100", "100")
	if err := f.b.CheckAllocation("acct", "alpha-1", d("5000"), snap, cfg, f.res); err == nil {
		t.Fatal("decrease below invested capital must be blocked by default")
	}
}

// Reservations (VP-020): buys reserve cash; fills release it; manual reservations use account BP.
func TestVP020ReservationsShrinkOnFill(t *testing.T) {
	f := newFixture(t)
	snap := &Snapshot{Equity: d("105000"), BuyingPower: d("100000")}
	cfg := DefaultCapitalConfig()
	r, err := f.b.Reserve(Reservation{ID: "r1", IntentID: "I1", Account: "acct", Owner: "alpha-1", InstrumentID: "X", Buy: true, Qty: d("100"), EstPrice: d("150")},
		snap, cfg, f.brk, f.res, false)
	if err != nil {
		t.Fatal(err)
	}
	f.res.Put(r)
	if c := f.b.Capital("acct", "alpha-1", snap, cfg, f.res); !c.CashAvailable.Equal(d("5000")) {
		t.Fatalf("cash available = %s", c.CashAvailable)
	}
	if _, err := f.b.Reserve(Reservation{ID: "r2", IntentID: "I2", Account: "acct", Owner: "alpha-1", InstrumentID: "X", Buy: true, Qty: d("40"), EstPrice: d("150")},
		snap, cfg, f.brk, f.res, false); err == nil {
		t.Fatal("second reservation beyond allocation must fail")
	}
	f.seq++
	f.apply(Event{Kind: KindExecution, Account: "acct", Source: "ex", Time: 5, Exec: &Exec{ExecID: "ex", IntentID: "I1", InstrumentID: "X", Buy: true,
		Qty: d("60"), Price: d("150"), Owner: "alpha-1"}})
	if !r.Remaining.Equal(d("40")) {
		t.Fatalf("remaining = %s", r.Remaining)
	}
	if c := f.b.Capital("acct", "alpha-1", snap, cfg, f.res); !c.CashAvailable.Equal(d("5000")) {
		t.Fatalf("cash available after partial fill = %s", c.CashAvailable)
	}
}

// Reconciliation timing window and break escalation (VP-013, VP-018).
func TestVP013TimingThenMissingExecution(t *testing.T) {
	f := newFixture(t)
	a := f.b.Accounts["acct"]
	cfg := DefaultReconConfig()
	s := func(at int64, q string) *Snapshot {
		return &Snapshot{AsOf: at, Cash: a.Cash, Positions: []SnapPos{{InstrumentID: "X", Qty: d(q)}}}
	}
	sec := int64(1e9)
	if r := Reconcile(a, s(10*sec, "100"), f.brk, 10*sec, cfg, "t"); r.Status != StatusOK {
		t.Fatalf("clean = %s", r.Status)
	}
	if r := Reconcile(a, s(20*sec, "90"), f.brk, 20*sec, cfg, "t"); r.Status != StatusPending || r.Halted[0] != "*|X" {
		t.Fatalf("timing = %s %v", r.Status, r.Halted)
	}
	// The difference resolves itself (a late fill): auto-resolved, back to OK.
	if r := Reconcile(a, s(30*sec, "100"), f.brk, 30*sec, cfg, "t"); r.Status != StatusOK {
		t.Fatalf("auto-resolve = %s", r.Status)
	}
	f.fill("alpha-1", "X", true, "10", "60")
	Reconcile(a, s(40*sec, "100"), f.brk, 40*sec, cfg, "t")
	r := Reconcile(a, s(110*sec, "100"), f.brk, 110*sec, cfg, "t")
	if r.Status != StatusBreak || len(r.Open) != 1 || r.Open[0].Type != BreakMissingExecution || r.Halted[0] != "*|X" {
		t.Fatalf("escalation = %s %+v %v", r.Status, r.Open, r.Halted)
	}
	if r := Reconcile(a, nil, f.brk, 120*sec, cfg, "t"); r.Status != StatusStale {
		t.Fatalf("no snapshot = %s", r.Status)
	}
}

// VP-017: an execution with an unregistered short ID is booked to manual and halts the instrument.
func TestVP017UnknownExecution(t *testing.T) {
	f := newFixture(t)
	fx := f.apply(Event{Kind: KindExecution, Account: "acct", Source: "u1", Time: 9, Exec: &Exec{ExecID: "u1", InstrumentID: "X", Buy: true, Qty: d("5"),
		Price: d("60"), Owner: Manual, UnknownShortID: "zzzzzz"}})
	if len(fx.Breaks) != 1 || fx.Breaks[0].Type != BreakUnknownExecution || !f.brk.Halted("acct", "beta-1", "X") {
		t.Fatalf("breaks = %+v", fx.Breaks)
	}
}

// VP-022: a loss within 30 days of a buy by another owner is flagged.
func TestVP022WashSaleAcrossOwners(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "X", true, "10", "60")
	f.fill("beta-1", "X", true, "5", "55")
	fx := f.fill("alpha-1", "X", false, "10", "50")
	if len(fx.Washes) != 1 || fx.Washes[0].BuyOwner != "beta-1" || !fx.Washes[0].Loss.Equal(d("100")) {
		t.Fatalf("washes = %+v", fx.Washes)
	}
}

// Idempotency (OE-006): applying the same execution twice changes nothing.
func TestOE006DuplicateExecutionIgnored(t *testing.T) {
	f := newFixture(t)
	e := Event{Kind: KindExecution, Account: "acct", Source: "dup", Time: 3, Exec: &Exec{ExecID: "dup", InstrumentID: "X", Buy: true, Qty: d("7"), Price: d("60"), Owner: "alpha-1"}}
	f.apply(e)
	if _, applied, _ := f.b.Apply(e, f.brk, f.res); applied || !f.qty("alpha-1", "X").Equal(d("7")) {
		t.Fatalf("duplicate applied=%v qty=%s", applied, f.qty("alpha-1", "X"))
	}
}

// Property test over random sequences of strategy fills, manual trades, splits, and dividends
// (VP-I1, VP-I2, VP-I3): Σ owner qty equals the broker's net of all fills; automated (strategy)
// actions never change manual quantity; every open lot has a source; cash equals the cash flows.
func TestVPInvariantsProperty(t *testing.T) {
	for seed := uint64(1); seed <= 200; seed++ {
		rng := rand.New(rand.NewPCG(seed, 7))
		f := newFixture(t)
		broker := map[string]decimal.Decimal{"X": d("100")}
		cash := d("100000")
		instrs := []string{"X", "Y", "Z"}
		for step := 0; step < 120; step++ {
			instr := instrs[rng.IntN(len(instrs))]
			manualBefore := f.qty(Manual, instr)
			switch k := rng.IntN(10); {
			case k < 6: // strategy trade within its own quantity (what the gate allows)
				owner := []string{"alpha-1", "beta-1"}[rng.IntN(2)]
				own := f.qty(owner, instr)
				buy := rng.IntN(2) == 0 || !own.IsPositive()
				q := decimal.NewFromInt(int64(1 + rng.IntN(20)))
				if !buy {
					q = decimal.Min(q, own)
				}
				px := decimal.NewFromInt(int64(20 + rng.IntN(80)))
				f.fill(owner, instr, buy, q.String(), px.String())
				sq := q
				if !buy {
					sq = q.Neg()
				}
				broker[instr] = broker[instr].Add(sq)
				cash = cash.Sub(sq.Mul(px))
				if !f.qty(Manual, instr).Equal(manualBefore) {
					t.Fatalf("seed %d step %d: strategy trade changed manual %s → %s (VP-I2)", seed, step, manualBefore, f.qty(Manual, instr))
				}
			case k < 8: // manual trade in the broker app
				buy := rng.IntN(2) == 0
				q := decimal.NewFromInt(int64(1 + rng.IntN(30)))
				px := decimal.NewFromInt(int64(20 + rng.IntN(80)))
				f.fill(Manual, instr, buy, q.String(), px.String())
				sq := q
				if !buy {
					sq = q.Neg()
				}
				broker[instr] = broker[instr].Add(sq)
				cash = cash.Sub(sq.Mul(px))
			case k == 8:
				f.seq++
				f.apply(Event{Kind: KindSplit, Account: "acct", Source: fmt.Sprintf("sp%d", f.seq), Split: &Split{InstrumentID: instr, Ratio: d("2")}})
				broker[instr] = broker[instr].Mul(d("2"))
			default:
				f.seq++
				f.apply(Event{Kind: KindDividend, Account: "acct", Source: fmt.Sprintf("dv%d", f.seq), Dividend: &Dividend{InstrumentID: instr, Amount: d("0.25")}})
				cash = cash.Add(broker[instr].Mul(d("0.25")))
			}
			a := f.b.Accounts["acct"]
			for _, id := range instrs {
				if !a.LedgerQty(id).Equal(broker[id]) {
					t.Fatalf("seed %d step %d: ledger %s %s != broker %s (VP-I1)", seed, step, id, a.LedgerQty(id), broker[id])
				}
				shadow := Zero
				for _, l := range a.Shadow[id] {
					shadow = shadow.Add(l.Qty)
				}
				if !shadow.Equal(broker[id]) {
					t.Fatalf("seed %d step %d: shadow %s %s != broker %s (VP-021)", seed, step, id, shadow, broker[id])
				}
			}
			if !a.Cash.Equal(cash) {
				t.Fatalf("seed %d step %d: cash %s != %s", seed, step, a.Cash, cash)
			}
			for _, o := range a.Owners {
				for _, p := range o.Positions {
					for _, l := range p.Lots {
						if l.Source == "" || l.ID == "" {
							t.Fatalf("seed %d: lot without source %+v (VP-I3)", seed, l)
						}
					}
				}
			}
		}
	}
}

// BT-014: a fee (short borrow in the backtest simcore) is realized: owner equity and account cash go
// down by the amount, the owner's fees go up, and a repeated source is a no-op.
func TestBT014FeeIsRealizedAndIdempotent(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "Y", true, "10", "20")
	a := f.b.Accounts["acct"]
	cash, realized := a.Cash, a.Owner("alpha-1").Realized
	ev := Event{Kind: KindFee, Account: "acct", Source: "borrow:alpha-1:Y:2026-10-13", Time: 500,
		Fee: &Fee{Owner: "alpha-1", InstrumentID: "Y", Amount: d("1.25")}}
	f.apply(ev)
	f.apply(ev)
	o := a.Owner("alpha-1")
	if !a.Cash.Equal(cash.Sub(d("1.25"))) || !o.Realized.Equal(realized.Sub(d("1.25"))) || !o.Fees.Equal(d("1.25")) {
		t.Fatalf("cash %s realized %s fees %s", a.Cash, o.Realized, o.Fees)
	}
	if !f.qty("alpha-1", "Y").Equal(d("10")) {
		t.Fatal("a fee must not change quantity")
	}
	if _, _, err := f.b.Apply(Event{Kind: KindFee, Account: "acct", Source: "x", Fee: &Fee{Owner: "nobody", Amount: d("1")}}, f.brk, f.res); err == nil {
		t.Fatal("fee for an unknown owner must fail")
	}
}

// BT-008: a delisting closes every owner's position at the delisting price with the cash flow of a
// closing trade; the broker-lot shadow ledger is flat too.
func TestBT008DelistingClosesEveryOwner(t *testing.T) {
	f := newFixture(t)
	f.fill("alpha-1", "X", true, "30", "60")
	a := f.b.Accounts["acct"]
	cash := a.Cash
	f.apply(Event{Kind: KindDelisting, Account: "acct", Source: "ca:X:1", Time: 600, Delist: &Delisting{InstrumentID: "X", Price: d("10")}})
	for _, o := range []string{"alpha-1", Manual} {
		if !f.qty(o, "X").IsZero() {
			t.Fatalf("%s still holds %s X", o, f.qty(o, "X"))
		}
	}
	if !a.Cash.Equal(cash.Add(d("1300"))) {
		t.Fatalf("cash %s, want %s (130 shares x 10)", a.Cash, cash.Add(d("1300")))
	}
	if r := a.Owner("alpha-1").Realized; !r.Equal(d("-1500")) {
		t.Fatalf("alpha realized %s, want -1500 (30 x (10 - 60))", r)
	}
	if !a.LedgerQty("X").IsZero() || len(a.Shadow["X"]) != 0 {
		t.Fatalf("ledger qty %s shadow %v", a.LedgerQty("X"), a.Shadow["X"])
	}
}
