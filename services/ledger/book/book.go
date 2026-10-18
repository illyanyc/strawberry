// Package book is the ledger's pure accounting core (section 06, ADR-0024): per-account owners
// (strategies and `manual`), FIFO lots per owner, the broker-lot shadow ledger, cash, allocations,
// reservations, breaks, and reconciliation. It has no I/O; the engine persists every change it
// returns. All arithmetic is decimal (OE-007). Rules VP-* in
// docs/business-logic/virtual-portfolios-and-attribution.md.
package book

import (
	"fmt"
	"slices"
	"sort"
	"strings"

	"github.com/shopspring/decimal"
)

// Reserved owners.
const (
	// Manual owns everything not attributed to a strategy (VP-001).
	Manual = "manual"
	// Broker is the pseudo-owner of the broker-lot shadow ledger (VP-021).
	Broker = "broker"
)

// Zero is the decimal zero.
var Zero = decimal.Zero

// costScale is the precision of pro-rata cost relief (10 decimal places; residues stay in the lot
// and are released exactly when the lot closes).
const costScale = 10

// Lot is one open tax lot. Qty is signed (negative = short); Cost is the signed notional plus fees
// (long: amount paid; short: −proceeds + fees).
type Lot struct {
	ID       string          `json:"id"`
	Owner    string          `json:"owner"`
	Qty      decimal.Decimal `json:"qty"`
	Cost     decimal.Decimal `json:"cost"`
	OpenedAt int64           `json:"opened_at"`
	Source   string          `json:"source"`
}

// Position is one owner's holding in one instrument.
type Position struct {
	InstrumentID string          `json:"instrument_id"`
	Lots         []Lot           `json:"lots"`
	Realized     decimal.Decimal `json:"realized"`
}

// Qty is the signed open quantity.
func (p *Position) Qty() decimal.Decimal {
	q := Zero
	for _, l := range p.Lots {
		q = q.Add(l.Qty)
	}
	return q
}

// Cost is the total open cost basis.
func (p *Position) Cost() decimal.Decimal {
	c := Zero
	for _, l := range p.Lots {
		c = c.Add(l.Cost)
	}
	return c
}

// Owner is one owner's virtual portfolio inside an account.
type Owner struct {
	ID         string               `json:"id"`
	ShortID    string               `json:"short_id"`
	AllowShort bool                 `json:"allow_short"`
	Dedicated  bool                 `json:"dedicated"`
	Allocation decimal.Decimal      `json:"allocation"`
	Positions  map[string]*Position `json:"positions"`
	Realized   decimal.Decimal      `json:"realized"`
	Fees       decimal.Decimal      `json:"fees"`
	Dividends  decimal.Decimal      `json:"dividends"`
	// Cash flows of this owner's executions (manual's are part of the account cash only).
	CashFlow decimal.Decimal `json:"cash_flow"`
}

func newOwner(id string) *Owner {
	return &Owner{ID: id, Positions: map[string]*Position{}}
}

func (o *Owner) position(instr string) *Position {
	p := o.Positions[instr]
	if p == nil {
		p = &Position{InstrumentID: instr}
		o.Positions[instr] = p
	}
	return p
}

// Qty returns the owner's signed quantity in instr.
func (o *Owner) Qty(instr string) decimal.Decimal {
	if p := o.Positions[instr]; p != nil {
		return p.Qty()
	}
	return Zero
}

// Invested is the total open cost basis across instruments.
func (o *Owner) Invested() decimal.Decimal {
	c := Zero
	for _, p := range o.Positions {
		c = c.Add(p.Cost())
	}
	return c
}

// Reservation holds cash (buys) or own quantity (sells) for an intent until its orders are final
// (VP-020). Remaining shrinks as fills arrive.
type Reservation struct {
	ID           string          `json:"id"`
	IntentID     string          `json:"intent_id"`
	Account      string          `json:"account"`
	Owner        string          `json:"owner"`
	InstrumentID string          `json:"instrument_id"`
	Buy          bool            `json:"buy"`
	Qty          decimal.Decimal `json:"qty"`
	Remaining    decimal.Decimal `json:"remaining"`
	EstPrice     decimal.Decimal `json:"est_price"`
	CreatedAt    int64           `json:"created_at"`
}

// Notional is the cash still reserved.
func (r *Reservation) Notional() decimal.Decimal { return r.Remaining.Mul(r.EstPrice) }

// Account is one real broker account.
type Account struct {
	ID      string            `json:"id"`
	Owners  map[string]*Owner `json:"owners"`
	Shadow  map[string][]Lot  `json:"shadow"`
	Symbols map[string]string `json:"symbols"`
	// Cash the ledger expects at the broker: baseline + every execution and activity since.
	Cash         decimal.Decimal `json:"cash"`
	CashBaseline bool            `json:"cash_baseline"`
	Opened       bool            `json:"opened"`
	// Marks are the latest prices (broker snapshot market value / qty, or the last fill).
	Marks map[string]decimal.Decimal `json:"marks"`
}

func newAccount(id string) *Account {
	a := &Account{ID: id, Owners: map[string]*Owner{}, Shadow: map[string][]Lot{}, Symbols: map[string]string{},
		Marks: map[string]decimal.Decimal{}}
	a.Owners[Manual] = newOwner(Manual)
	a.Owners[Manual].ShortID = Manual
	return a
}

// Owner returns the owner, or nil.
func (a *Account) Owner(id string) *Owner { return a.Owners[id] }

// OwnerIDs returns owners in a stable order (manual first).
func (a *Account) OwnerIDs() []string {
	ids := make([]string, 0, len(a.Owners))
	for id := range a.Owners {
		if id != Manual {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return append([]string{Manual}, ids...)
}

// LedgerQty is Σ owner quantities in instr (VP-I1 right-hand side).
func (a *Account) LedgerQty(instr string) decimal.Decimal {
	q := Zero
	for _, o := range a.Owners {
		q = q.Add(o.Qty(instr))
	}
	return q
}

// Instruments returns every instrument any owner holds or held, sorted.
func (a *Account) Instruments() []string {
	set := map[string]bool{}
	for _, o := range a.Owners {
		for id, p := range o.Positions {
			if !p.Qty().IsZero() {
				set[id] = true
			}
		}
	}
	out := make([]string, 0, len(set))
	for id := range set {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

// Book is the whole ledger state.
type Book struct {
	Accounts map[string]*Account `json:"accounts"`
	// ShortIDs maps attribution short IDs to owner IDs (VP-012).
	ShortIDs map[string]string `json:"short_ids"`
	// Applied holds every source ID already applied (idempotency, OE-006).
	Applied map[string]bool `json:"applied"`
	// Buys per instrument for the wash-sale window (VP-022).
	Buys   map[string][]BuyRecord `json:"buys"`
	Washes []WashSale             `json:"washes"`
	Seq    int64                  `json:"seq"`
}

// BuyRecord is a purchase remembered for wash-sale flags.
type BuyRecord struct {
	Account string `json:"account"`
	Owner   string `json:"owner"`
	Source  string `json:"source"`
	Time    int64  `json:"time"`
}

// WashSale is an informational flag (VP-022): a loss-realizing sale within 30 days of a purchase of
// the same instrument by any owner in any account.
type WashSale struct {
	ID           string          `json:"id"`
	InstrumentID string          `json:"instrument_id"`
	SellAccount  string          `json:"sell_account"`
	SellOwner    string          `json:"sell_owner"`
	SellSource   string          `json:"sell_source"`
	SellTime     int64           `json:"sell_time"`
	Loss         decimal.Decimal `json:"loss"`
	BuyAccount   string          `json:"buy_account"`
	BuyOwner     string          `json:"buy_owner"`
	BuySource    string          `json:"buy_source"`
	BuyTime      int64           `json:"buy_time"`
}

// New returns an empty book.
func New() *Book {
	return &Book{Accounts: map[string]*Account{}, ShortIDs: map[string]string{}, Applied: map[string]bool{}, Buys: map[string][]BuyRecord{}}
}

// Account returns the account, creating it.
func (b *Book) Account(id string) *Account {
	a := b.Accounts[id]
	if a == nil {
		a = newAccount(id)
		b.Accounts[id] = a
	}
	return a
}

// AccountIDs returns account IDs sorted.
func (b *Book) AccountIDs() []string {
	ids := make([]string, 0, len(b.Accounts))
	for id := range b.Accounts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// OwnerOf maps a short ID to its owner ("" when unknown).
func (b *Book) OwnerOf(shortID string) string {
	if shortID == Manual {
		return Manual
	}
	return b.ShortIDs[shortID]
}

func (b *Book) nextID(prefix string) string {
	b.Seq++
	return fmt.Sprintf("%s-%08d", prefix, b.Seq)
}

// FillResult describes what one fill did to one owner.
type FillResult struct {
	Realized decimal.Decimal
	// Losses realized per relieved lot (for wash-sale flags).
	LossLots []Lot
	// Owner quantity before and after.
	Before, After decimal.Decimal
}

// applyFill books a signed quantity q at price p with fee f into the owner's lots (VP-011: FIFO
// within the owner). Opposite-signed fills relieve the oldest lots first; any excess opens a new lot
// on the other side (a flip), which the caller classifies (VP-004, VP-016).
func (b *Book) applyFill(o *Owner, instr string, q, p, f decimal.Decimal, at int64, source string) FillResult {
	pos := o.position(instr)
	res := FillResult{Before: pos.Qty(), Realized: Zero}
	remaining := q
	feeLeft := f
	for !remaining.IsZero() && len(pos.Lots) > 0 && pos.Lots[0].Qty.Sign() != remaining.Sign() {
		lot := &pos.Lots[0]
		lotAbs := lot.Qty.Abs()
		m := decimal.Min(lotAbs, remaining.Abs())
		var relieved decimal.Decimal
		if m.Equal(lotAbs) {
			relieved = lot.Cost
		} else {
			relieved = lot.Cost.Mul(m).DivRound(lotAbs, costScale)
		}
		fee := f.Mul(m).DivRound(q.Abs(), costScale)
		if m.Equal(remaining.Abs()) {
			fee = feeLeft
		}
		feeLeft = feeLeft.Sub(fee)
		sign := decimal.NewFromInt(int64(lot.Qty.Sign()))
		realized := sign.Mul(m).Mul(p).Sub(relieved).Sub(fee)
		res.Realized = res.Realized.Add(realized)
		if realized.IsNegative() {
			res.LossLots = append(res.LossLots, Lot{ID: lot.ID, Owner: o.ID, Qty: sign.Mul(m), Cost: realized, OpenedAt: lot.OpenedAt, Source: lot.Source})
		}
		lot.Cost = lot.Cost.Sub(relieved)
		lot.Qty = lot.Qty.Sub(sign.Mul(m))
		remaining = remaining.Add(sign.Mul(m))
		if lot.Qty.IsZero() {
			pos.Lots = pos.Lots[1:]
		}
	}
	if !remaining.IsZero() {
		pos.Lots = append(pos.Lots, Lot{ID: b.nextID("lot"), Owner: o.ID, Qty: remaining, Cost: remaining.Mul(p).Add(feeLeft), OpenedAt: at, Source: source})
	}
	pos.Realized = pos.Realized.Add(res.Realized)
	o.Realized = o.Realized.Add(res.Realized)
	o.Fees = o.Fees.Add(f)
	res.After = pos.Qty()
	return res
}

// applyShadow books the fill into the broker-lot shadow ledger: one FIFO queue per instrument across
// every owner, which is how a broker with FIFO tax-lot relief sees the account (VP-021). It returns
// the owners whose lots the broker relieved.
func (a *Account) applyShadow(instr, owner string, q, p decimal.Decimal, at int64, source, lotID string) []string {
	lots := a.Shadow[instr]
	remaining := q
	var relievedOwners []string
	for !remaining.IsZero() && len(lots) > 0 && lots[0].Qty.Sign() != remaining.Sign() {
		lot := &lots[0]
		m := decimal.Min(lot.Qty.Abs(), remaining.Abs())
		sign := decimal.NewFromInt(int64(lot.Qty.Sign()))
		if lot.Qty.Abs().Equal(m) {
			lot.Cost = Zero
		} else {
			lot.Cost = lot.Cost.Sub(lot.Cost.Mul(m).DivRound(lot.Qty.Abs(), costScale))
		}
		if !slices.Contains(relievedOwners, lot.Owner) {
			relievedOwners = append(relievedOwners, lot.Owner)
		}
		lot.Qty = lot.Qty.Sub(sign.Mul(m))
		remaining = remaining.Add(sign.Mul(m))
		if lot.Qty.IsZero() {
			lots = lots[1:]
		}
	}
	if !remaining.IsZero() {
		lots = append(lots, Lot{ID: lotID, Owner: owner, Qty: remaining, Cost: remaining.Mul(p), OpenedAt: at, Source: source})
	}
	a.Shadow[instr] = lots
	return relievedOwners
}

// ParseOwnerRef splits "strategy|instrument" halt keys.
func ParseOwnerRef(s string) (owner, instr string) {
	owner, instr, _ = strings.Cut(s, "|")
	return owner, instr
}
