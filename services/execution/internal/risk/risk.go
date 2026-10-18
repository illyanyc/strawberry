// Package risk is the pre-trade gate (section 07, ADR-0025, RM-001..RM-016). Every check is a pure
// function of Input that returns a named RiskCheck with its value and limit; Evaluate runs all of
// them (so the audit record shows every value) and approves only if all pass. Inputs that are
// missing or stale fail their check (RM-004, fail closed).
package risk

import (
	"fmt"
	"slices"
	"strings"

	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// Limits are the effective (inherited, tightest-wins) limits for one intent (RM-003).
type Limits struct {
	MaxOrderNotional, MaxOrderQty, MaxPositionNotional, MaxPositionPctAllocation     decimal.Decimal
	MaxGrossExposure, MaxNetExposure, MaxConcentrationPct, MaxDailyLoss, MaxDrawdown decimal.Decimal
	PriceCollarPct, FatFingerMultiple                                                decimal.Decimal
	MaxPositions, MaxOrdersPerMinute, MaxCancelsPerMinute, MaxConsecutiveLosses      int
	DuplicateWindowSeconds, MaxOpenOrders                                            int
}

// Account is the broker-account state the gate needs.
type Account struct {
	ID          string
	Live        bool
	Known       bool
	Session     string
	Ready       bool
	ReconStatus string
	// ReconFresh: a reconciliation newer than the session's last transition to READY (OE-008).
	ReconFresh     bool
	Halted         []string
	IMD            bool
	MarginCall     bool
	TradingBlocked bool
	Equity         decimal.Decimal
	BuyingPower    decimal.Decimal
	LongMV         decimal.Decimal
	ShortMV        decimal.Decimal
	SnapshotKnown  bool
	DayTradeCount  int
	PDTGuard       string
	PDTMax         int
	MarginMultiple decimal.Decimal
	MarginUtilCap  decimal.Decimal
	Segregation    bool
	// Strategy totals on this account for the margin guard (D30).
	StrategiesGross     decimal.Decimal
	StrategiesOpenOrder decimal.Decimal
	AccountOpenOrder    decimal.Decimal
	StrategyAllocSum    decimal.Decimal
}

// Portfolio is the owner's virtual portfolio (from the ledger).
type Portfolio struct {
	Known             bool
	Allocation        decimal.Decimal
	Equity            decimal.Decimal
	HighWaterMark     decimal.Decimal
	DayPnL            decimal.Decimal
	Gross, Net        decimal.Decimal
	Positions         int
	InstrumentValue   decimal.Decimal
	HoldsInstrument   bool
	ConsecutiveLosses int
}

// Ownership mirrors tb.ledger.v1.OwnershipConstraints.
type Ownership struct {
	Known        bool
	StrategyQty  decimal.Decimal
	OtherQty     decimal.Decimal
	ManualQty    decimal.Decimal
	MaxSellable  decimal.Decimal
	ShortAllowed bool
	Reason       string
	Halted       bool
}

// KillSwitch is an active latched switch.
type KillSwitch struct {
	ID, Scope, Target, Action string
}

// Input is everything one decision needs.
type Input struct {
	Intent   *ordersv1.OrderIntent
	Strategy *ordersv1.Strategy // nil for manual (owner) intents
	Manual   bool
	Close    bool
	Buy      bool
	Short    bool
	Qty      decimal.Decimal
	Price    decimal.Decimal
	Notional decimal.Decimal

	Account   Account
	Routing   []string // accounts routed for the strategy's horizon
	Kills     []KillSwitch
	Halts     []string // active OMS halts matching strategy/instrument
	Portfolio Portfolio
	Owner     Ownership

	Now                int64
	ClockOK            bool
	MarketOpen         bool
	ExtendedOpen       bool
	LastPrice          decimal.Decimal
	LastBarClose       int64
	MaxBarAge          int64
	InstrumentKnown    bool
	Tradable           bool
	Shortable          bool
	OwnOppositeLegs    bool
	OtherOppositeOpen  []string
	OrdersLastMinute   int
	OpenOrders         int
	Duplicate          bool
	MedianOrderQty     decimal.Decimal
	SizeHistory        int
	SupportsBracket    bool
	SegregationAllowed bool

	Limits Limits
}

func pass(name, value, limit, detail string) *ordersv1.RiskCheck {
	return &ordersv1.RiskCheck{Name: name, Passed: true, Value: value, Limit: limit, Detail: detail}
}

func fail(name, value, limit, detail string) *ordersv1.RiskCheck {
	return &ordersv1.RiskCheck{Name: name, Passed: false, Value: value, Limit: limit, Detail: detail}
}

func check(ok bool, name, value, limit, detail string) *ordersv1.RiskCheck {
	if ok {
		return pass(name, value, limit, "")
	}
	return fail(name, value, limit, detail)
}

// Check is one named pre-trade check.
type Check struct {
	Name string
	Fn   func(in *Input) *ordersv1.RiskCheck
}

// Checks is the ordered gate (RM-003). Capital (ledger reservation) runs after these in the OMS
// because it has a side effect; its result is appended to the same decision.
var Checks = []Check{
	{"intent_valid", intentValid},
	{"mode", mode},
	{"kill_switch", killSwitch},
	{"halt", halt},
	{"routing", routing},
	{"session_ready", sessionReady},
	{"reconciliation", reconciliation},
	{"margin_events", marginEvents},
	{"clock", clock},
	{"data_fresh", dataFresh},
	{"market_session", marketSession},
	{"tradable", tradable},
	{"symbol_segregation", segregation},
	{"ownership", ownership},
	{"self_cross", selfCross},
	{"protective_order", protective},
	{"price_collar", priceCollar},
	{"max_order_notional", maxOrderNotional},
	{"max_order_qty", maxOrderQty},
	{"max_position", maxPosition},
	{"max_exposure", maxExposure},
	{"max_positions", maxPositions},
	{"concentration", concentration},
	{"daily_loss", dailyLoss},
	{"drawdown", drawdown},
	{"consecutive_losses", consecutiveLosses},
	{"order_rate", orderRate},
	{"open_orders", openOrders},
	{"duplicate", duplicate},
	{"fat_finger", fatFinger},
	{"margin_guard", marginGuard},
	{"pdt_guard", pdtGuard},
}

// Evaluate runs every check and returns them with the approval (RM-001, RM-002).
func Evaluate(in *Input) (bool, []*ordersv1.RiskCheck) {
	out := make([]*ordersv1.RiskCheck, 0, len(Checks))
	ok := true
	for _, c := range Checks {
		r := c.Fn(in)
		r.Name = c.Name
		out = append(out, r)
		ok = ok && r.Passed
	}
	return ok, out
}

func s(d decimal.Decimal) string { return d.String() }

func intentValid(in *Input) *ordersv1.RiskCheck {
	it := in.Intent
	switch {
	case it.GetIntentId() == "" || it.GetInstrumentId() == "" || it.GetBrokerAccountId() == "":
		return fail("", "", "", "intent_id, instrument_id, and broker_account_id are required")
	case it.GetSide() == commonv1.Side_SIDE_UNSPECIFIED:
		return fail("", "", "", "side is required")
	case !in.Qty.IsPositive():
		return fail("", s(in.Qty), "> 0", "quantity must be positive")
	case !in.Manual && in.Strategy == nil:
		return fail("", it.GetStrategyId(), "registered", "unknown strategy")
	case !in.Manual && it.GetHorizon() != in.Strategy.GetHorizon():
		return fail("", it.GetHorizon().String(), in.Strategy.GetHorizon().String(), "intent horizon differs from the registry (RM-011)")
	}
	return pass("", "", "", "")
}

// mode: paper strategies trade paper accounts only, live strategies live accounts only (SM-012).
func mode(in *Input) *ordersv1.RiskCheck {
	if in.Manual {
		return check(in.Account.Known, "", "manual", "", "unknown account")
	}
	m := in.Strategy.GetMode()
	switch {
	case !in.Account.Known:
		return fail("", in.Intent.GetBrokerAccountId(), "known account", "account not served by any broker adapter")
	case m == ordersv1.StrategyMode_STRATEGY_MODE_PAPER && in.Account.Live:
		return fail("", "paper strategy", "paper account", "paper strategy sent to a live account")
	case m == ordersv1.StrategyMode_STRATEGY_MODE_LIVE && !in.Account.Live:
		return fail("", "live strategy", "live account", "live strategy sent to a paper account")
	case m == ordersv1.StrategyMode_STRATEGY_MODE_PAPER, m == ordersv1.StrategyMode_STRATEGY_MODE_LIVE, m == ordersv1.StrategyMode_STRATEGY_MODE_SHADOW:
		if in.Intent.GetBrokerAccountId() != in.Strategy.GetBrokerAccountId() {
			return fail("", in.Intent.GetBrokerAccountId(), in.Strategy.GetBrokerAccountId(), "strategy is registered to a different account")
		}
		return pass("", m.String(), "", "")
	default:
		return fail("", m.String(), "paper|live|shadow", "strategy mode does not allow orders")
	}
}

// killSwitch: an active switch in any matching scope freezes new orders (RM-006, RM-I2). Exits
// generated by a flatten switch are the one exception.
func killSwitch(in *Input) *ordersv1.RiskCheck {
	if in.Intent.GetSource() == ordersv1.IntentSource_INTENT_SOURCE_KILL_SWITCH {
		return pass("", "flatten exit", "", "")
	}
	for _, k := range in.Kills {
		if KillMatches(k, in.Intent.GetBrokerAccountId(), in.Intent.GetStrategyId(), in.Intent.GetInstrumentId()) {
			return fail("", k.Scope+":"+k.Target, "none active", "kill switch "+k.ID+" ("+k.Action+") is engaged")
		}
	}
	return pass("", "none", "", "")
}

// KillMatches reports whether a switch covers (account, strategy, instrument).
func KillMatches(k KillSwitch, acct, strategy, instr string) bool {
	switch k.Scope {
	case "global":
		return true
	case "broker_account":
		return k.Target == acct
	case "strategy":
		return k.Target == strategy
	case "instrument":
		return k.Target == instr
	}
	return false
}

func halt(in *Input) *ordersv1.RiskCheck {
	if len(in.Halts) > 0 {
		return fail("", strings.Join(in.Halts, "; "), "none", "strategy/instrument halted: "+in.Halts[0])
	}
	return pass("", "none", "", "")
}

// routing: the strategy's horizon must be routed to the target account (RM-011, D30).
func routing(in *Input) *ordersv1.RiskCheck {
	if in.Manual {
		return pass("", "manual", "", "")
	}
	acct := in.Intent.GetBrokerAccountId()
	if slices.Contains(in.Routing, acct) {
		return pass("", acct, strings.Join(in.Routing, ","), "")
	}
	return fail("", acct, strings.Join(in.Routing, ","), fmt.Sprintf("%s strategies are not routed to %s", in.Strategy.GetHorizon(), acct))
}

func sessionReady(in *Input) *ordersv1.RiskCheck {
	return check(in.Account.Ready, "", in.Account.Session, "SESSION_STATE_READY", "broker session is not ready (OE-004)")
}

// reconciliation: account not broken and reconciled after the last reconnect; the strategy and
// instrument not in a halted pair (VP-008, RM-009).
func reconciliation(in *Input) *ordersv1.RiskCheck {
	a := in.Account
	switch {
	case a.ReconStatus == "" || a.ReconStatus == "RECONCILIATION_STATUS_STALE" || a.ReconStatus == "RECONCILIATION_STATUS_UNSPECIFIED":
		return fail("", a.ReconStatus, "OK", "no fresh reconciliation for the account")
	case !a.ReconFresh:
		return fail("", "stale after reconnect", "OK after READY", "waiting for a clean reconciliation after the session became ready (OE-008)")
	}
	owner := in.Intent.GetStrategyId()
	if in.Manual {
		owner = "manual"
	}
	for _, p := range a.Halted {
		o, i, _ := strings.Cut(p, "|")
		if (o == "*" || o == owner) && (i == "*" || i == in.Intent.GetInstrumentId()) {
			return fail("", p, "not halted", "blocked by an open reconciliation break (VP-008)")
		}
	}
	if in.Owner.Halted {
		return fail("", "halted", "not halted", "ledger reports the owner halted for this instrument")
	}
	return pass("", a.ReconStatus, "OK", "")
}

func marginEvents(in *Input) *ordersv1.RiskCheck {
	a := in.Account
	switch {
	case !a.SnapshotKnown:
		return fail("", "no snapshot", "snapshot", "no account snapshot yet")
	case a.IMD, a.MarginCall, a.TradingBlocked:
		return fail("", fmt.Sprintf("imd=%t margin_call=%t blocked=%t", a.IMD, a.MarginCall, a.TradingBlocked), "none", "broker margin event or restriction halts the account (RM-013)")
	}
	return pass("", "none", "", "")
}

func clock(in *Input) *ordersv1.RiskCheck {
	return check(in.ClockOK, "", fmt.Sprintf("%t", in.ClockOK), "skew within threshold", "clock skew above threshold (OP-020)")
}

// dataFresh: the latest final bar must be younger than MaxBarAge (two bar intervals plus the
// finality window, RM-004, MD-007); no price fails closed.
func dataFresh(in *Input) *ordersv1.RiskCheck {
	age := in.Now - in.LastBarClose
	if in.LastBarClose == 0 || !in.LastPrice.IsPositive() {
		return fail("", "no bar", "fresh bar", "no market data for the instrument")
	}
	return check(age <= in.MaxBarAge, "", fmt.Sprintf("%ds", age/1e9), fmt.Sprintf("%ds", in.MaxBarAge/1e9), "market data is stale")
}

func marketSession(in *Input) *ordersv1.RiskCheck {
	if in.MarketOpen || (in.Intent.GetExtendedHours() && in.ExtendedOpen && in.Manual) {
		return pass("", "open", "", "")
	}
	return fail("", "closed", "regular session", "market is closed for this order")
}

func tradable(in *Input) *ordersv1.RiskCheck {
	switch {
	case !in.InstrumentKnown:
		return fail("", "unknown", "known", "instrument not in the instrument master")
	case !in.Tradable:
		return fail("", "not tradable", "tradable", "instrument not tradable at the broker")
	case in.Short && !in.Shortable:
		return fail("", "not shortable", "shortable", "instrument not shortable")
	}
	return pass("", "tradable", "", "")
}

// segregation: at accounts with symbol segregation, strategies may not trade instruments the owner
// holds manually unless allowlisted (RM-014, N3).
func segregation(in *Input) *ordersv1.RiskCheck {
	if in.Manual || !in.Account.Segregation {
		return pass("", "n/a", "", "")
	}
	if in.Owner.ManualQty.IsZero() || in.SegregationAllowed {
		return pass("", "manual "+s(in.Owner.ManualQty), "", "")
	}
	return fail("", "manual holds "+s(in.Owner.ManualQty), "0 or allowlisted", "instrument is held manually in this account; add it to the strategy's segregation allowlist to trade it")
}

// ownership: sells never exceed own sellable quantity; shorts only where allowed (VP-003..VP-005).
// Manual (owner) sells never exceed manual quantity.
func ownership(in *Input) *ordersv1.RiskCheck {
	o := in.Owner
	if !o.Known {
		return fail("", "unknown", "ledger answer", "ledger ownership constraints unavailable")
	}
	switch {
	case in.Buy:
		return pass("", "buy", "", "")
	case in.Short:
		return check(o.ShortAllowed, "", "short", "short allowed", "short not allowed: "+o.Reason)
	case in.Manual:
		return check(in.Qty.LessThanOrEqual(o.ManualQty), "", s(in.Qty), s(o.ManualQty), "manual sell exceeds manual quantity; would sell strategy shares")
	default:
		return check(in.Qty.LessThanOrEqual(o.MaxSellable), "", s(in.Qty), s(o.MaxSellable), "sell exceeds the strategy's own sellable quantity (VP-004)")
	}
}

// selfCross: never leave opposite-side resting orders across owners (N3; Alpaca self-cross 403).
func selfCross(in *Input) *ordersv1.RiskCheck {
	if len(in.OtherOppositeOpen) > 0 {
		return fail("", strings.Join(in.OtherOppositeOpen, ","), "none", "another owner has an opposite-side order resting in this instrument (OE-018)")
	}
	if in.OwnOppositeLegs && !in.Close {
		return fail("", "own protective legs", "none", "own opposite-side protective legs rest in this instrument; close first")
	}
	return pass("", "none", "", "")
}

// protective: every algorithmic entry carries a broker-side stop on the correct side; swing stops
// are GTC (N5, OE-014, RM-011).
func protective(in *Input) *ordersv1.RiskCheck {
	if in.Manual || in.Close {
		return pass("", "n/a", "", "")
	}
	p := in.Intent.GetProtective()
	stop, err := decimal.NewFromString(p.GetStopLossPrice().GetValue())
	if err != nil || !stop.IsPositive() {
		return fail("", "none", "stop_loss_price", "algorithmic entries need a broker-side stop (N5)")
	}
	if in.Buy && !stop.LessThan(in.Price) || !in.Buy && !stop.GreaterThan(in.Price) {
		return fail("", s(stop), "beyond entry "+s(in.Price), "stop is on the wrong side of the entry")
	}
	if in.Strategy.GetHorizon() == commonv1.Horizon_HORIZON_SWING && p.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_GTC {
		return fail("", p.GetTif().String(), "GTC", "swing positions need GTC protective stops (RM-011)")
	}
	if tp := p.GetTakeProfitPrice().GetValue(); tp != "" {
		t, err := decimal.NewFromString(tp)
		if err != nil || (in.Buy && !t.GreaterThan(in.Price)) || (!in.Buy && !t.LessThan(in.Price)) {
			return fail("", tp, "beyond entry", "take-profit is on the wrong side of the entry")
		}
	}
	return pass("", s(stop), "", "")
}

func priceCollar(in *Input) *ordersv1.RiskCheck {
	if !in.LastPrice.IsPositive() || !in.Price.IsPositive() {
		return fail("", s(in.Price), "priced", "no limit price or last price")
	}
	dev := in.Price.Sub(in.LastPrice).Abs().Div(in.LastPrice)
	return check(dev.LessThanOrEqual(in.Limits.PriceCollarPct), "", dev.StringFixed(4), s(in.Limits.PriceCollarPct), "limit price outside the collar around the last price")
}

func le(name string, v, lim decimal.Decimal, detail string) *ordersv1.RiskCheck {
	if lim.IsZero() {
		return pass(name, s(v), "unset", "")
	}
	return check(v.LessThanOrEqual(lim), name, s(v), s(lim), detail)
}

func maxOrderNotional(in *Input) *ordersv1.RiskCheck {
	return le("", in.Notional, in.Limits.MaxOrderNotional, "order notional above the limit")
}

func maxOrderQty(in *Input) *ordersv1.RiskCheck {
	return le("", in.Qty, in.Limits.MaxOrderQty, "order quantity above the limit")
}

func maxPosition(in *Input) *ordersv1.RiskCheck {
	if in.Close || in.Manual {
		return pass("", "n/a", "", "")
	}
	after := in.Portfolio.InstrumentValue.Abs().Add(in.Notional)
	if r := le("", after, in.Limits.MaxPositionNotional, "position notional above the limit"); !r.Passed {
		return r
	}
	if pct := in.Limits.MaxPositionPctAllocation; pct.IsPositive() && in.Portfolio.Allocation.IsPositive() {
		return le("", after, in.Portfolio.Allocation.Mul(pct), "position above its share of the allocation")
	}
	return pass("", s(after), "", "")
}

func maxExposure(in *Input) *ordersv1.RiskCheck {
	if in.Close || in.Manual {
		return pass("", "n/a", "", "")
	}
	gross := in.Portfolio.Gross.Add(in.Notional)
	if r := le("", gross, in.Limits.MaxGrossExposure, "gross exposure above the limit"); !r.Passed {
		return r
	}
	net := in.Portfolio.Net.Add(in.Notional)
	if !in.Buy {
		net = in.Portfolio.Net.Sub(in.Notional)
	}
	return le("", net.Abs(), in.Limits.MaxNetExposure, "net exposure above the limit")
}

func maxPositions(in *Input) *ordersv1.RiskCheck {
	if in.Close || in.Manual || in.Limits.MaxPositions == 0 || in.Portfolio.HoldsInstrument {
		return pass("", fmt.Sprint(in.Portfolio.Positions), fmt.Sprint(in.Limits.MaxPositions), "")
	}
	return check(in.Portfolio.Positions+1 <= in.Limits.MaxPositions, "", fmt.Sprint(in.Portfolio.Positions+1), fmt.Sprint(in.Limits.MaxPositions), "too many open positions")
}

func concentration(in *Input) *ordersv1.RiskCheck {
	pct := in.Limits.MaxConcentrationPct
	if in.Close || in.Manual || !pct.IsPositive() || pct.GreaterThanOrEqual(decimal.NewFromInt(1)) {
		return pass("", "n/a", "", "")
	}
	gross := in.Portfolio.Gross.Add(in.Notional)
	share := in.Portfolio.InstrumentValue.Abs().Add(in.Notional).Div(gross)
	return check(share.LessThanOrEqual(pct), "", share.StringFixed(3), s(pct), "instrument concentration above the limit")
}

// dailyLoss: realized + unrealized − fees for the session; breaching blocks risk-increasing intents
// for the rest of the session; closes stay allowed (RM-005).
func dailyLoss(in *Input) *ordersv1.RiskCheck {
	if in.Close || in.Manual || in.Limits.MaxDailyLoss.IsZero() {
		return pass("", s(in.Portfolio.DayPnL), s(in.Limits.MaxDailyLoss), "")
	}
	if !in.Portfolio.Known {
		return fail("", "unknown", "ledger portfolio", "portfolio unavailable")
	}
	return check(in.Portfolio.DayPnL.GreaterThan(in.Limits.MaxDailyLoss.Neg()), "", s(in.Portfolio.DayPnL), "-"+s(in.Limits.MaxDailyLoss), "daily loss limit reached (RM-005)")
}

func drawdown(in *Input) *ordersv1.RiskCheck {
	if in.Close || in.Manual || in.Limits.MaxDrawdown.IsZero() || !in.Portfolio.Known {
		return pass("", "n/a", "", "")
	}
	dd := in.Portfolio.HighWaterMark.Sub(in.Portfolio.Equity)
	return check(dd.LessThan(in.Limits.MaxDrawdown), "", s(dd), s(in.Limits.MaxDrawdown), "drawdown from the high-water mark reached the limit")
}

func consecutiveLosses(in *Input) *ordersv1.RiskCheck {
	if in.Close || in.Manual || in.Limits.MaxConsecutiveLosses == 0 {
		return pass("", "n/a", "", "")
	}
	return check(in.Portfolio.ConsecutiveLosses < in.Limits.MaxConsecutiveLosses, "", fmt.Sprint(in.Portfolio.ConsecutiveLosses),
		fmt.Sprint(in.Limits.MaxConsecutiveLosses), "consecutive losing trades reached the limit")
}

func orderRate(in *Input) *ordersv1.RiskCheck {
	if in.Limits.MaxOrdersPerMinute == 0 {
		return pass("", fmt.Sprint(in.OrdersLastMinute), "unset", "")
	}
	return check(in.OrdersLastMinute < in.Limits.MaxOrdersPerMinute, "", fmt.Sprint(in.OrdersLastMinute+1), fmt.Sprint(in.Limits.MaxOrdersPerMinute), "order rate limit")
}

func openOrders(in *Input) *ordersv1.RiskCheck {
	if in.Limits.MaxOpenOrders == 0 {
		return pass("", fmt.Sprint(in.OpenOrders), "unset", "")
	}
	return check(in.OpenOrders < in.Limits.MaxOpenOrders, "", fmt.Sprint(in.OpenOrders+1), fmt.Sprint(in.Limits.MaxOpenOrders), "too many open orders")
}

func duplicate(in *Input) *ordersv1.RiskCheck {
	return check(!in.Duplicate, "", fmt.Sprintf("%t", in.Duplicate), fmt.Sprintf("none within %ds", in.Limits.DuplicateWindowSeconds), "identical intent within the duplicate window")
}

func fatFinger(in *Input) *ordersv1.RiskCheck {
	m := in.Limits.FatFingerMultiple
	if !m.IsPositive() || in.SizeHistory < 5 || !in.MedianOrderQty.IsPositive() {
		return pass("", s(in.Qty), "insufficient history", "")
	}
	lim := in.MedianOrderQty.Mul(m)
	return check(in.Qty.LessThanOrEqual(lim), "", s(in.Qty), s(lim), "quantity far above the strategy's typical size")
}

// marginGuard (D30, RM-012): strategies' projected gross ≤ min(Σ allocations, equity × multiplier
// × utilization cap); the whole account's projected gross ≤ equity × multiplier (no borrowing
// beyond the multiplier, which protects the manual book). Closes reduce exposure and pass.
func marginGuard(in *Input) *ordersv1.RiskCheck {
	a := in.Account
	if in.Close || !in.Buy && !in.Short {
		return pass("", "risk-reducing", "", "")
	}
	if !a.SnapshotKnown || !a.Equity.IsPositive() {
		return fail("", "no equity", "snapshot", "no account equity for the margin guard")
	}
	capacity := a.Equity.Mul(a.MarginMultiple)
	acctAfter := a.LongMV.Add(a.ShortMV.Abs()).Add(a.AccountOpenOrder).Add(in.Notional)
	if acctAfter.GreaterThan(capacity) {
		return fail("", acctAfter.StringFixed(2), capacity.StringFixed(2), "account gross would exceed equity × max margin multiplier")
	}
	if in.Manual {
		return pass("", acctAfter.StringFixed(2), capacity.StringFixed(2), "")
	}
	cap := decimal.Min(a.StrategyAllocSum, capacity.Mul(a.MarginUtilCap))
	after := a.StrategiesGross.Add(a.StrategiesOpenOrder).Add(in.Notional)
	return check(after.LessThanOrEqual(cap), "", after.StringFixed(2), cap.StringFixed(2), "strategies' projected exposure above the intraday-margin guard (D30)")
}

// pdtGuard (RM-008): off by default; `legacy` rejects intraday opening orders once the rolling
// day-trade count reached the allowance.
func pdtGuard(in *Input) *ordersv1.RiskCheck {
	a := in.Account
	if a.PDTGuard != "legacy" {
		return pass("", fmt.Sprintf("%d day trades (guard off)", a.DayTradeCount), "off", "")
	}
	if in.Close || in.Manual || in.Strategy.GetHorizon() != commonv1.Horizon_HORIZON_INTRADAY {
		return pass("", fmt.Sprint(a.DayTradeCount), fmt.Sprint(a.PDTMax), "")
	}
	return check(a.DayTradeCount < a.PDTMax, "", fmt.Sprint(a.DayTradeCount+1), fmt.Sprint(a.PDTMax), "would exceed the pattern-day-trade allowance (legacy guard)")
}

// First returns the first failed check name ("" if approved).
func First(checks []*ordersv1.RiskCheck) string {
	for _, c := range checks {
		if !c.GetPassed() {
			return c.GetName()
		}
	}
	return ""
}
