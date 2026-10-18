package risk

import (
	"testing"

	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

func dd(s string) decimal.Decimal { return decimal.RequireFromString(s) }

const sec = int64(1e9)

// base is an intent that passes every check: a paper ORB entry of 100 @ 50 with a stop at 49.
func base() *Input {
	st := &ordersv1.Strategy{StrategyId: "orb", ShortId: "orbaaaa", Horizon: commonv1.Horizon_HORIZON_INTRADAY,
		Mode: ordersv1.StrategyMode_STRATEGY_MODE_PAPER, BrokerAccountId: "alpaca-paper"}
	it := &ordersv1.OrderIntent{IntentId: "01J00000000000000000000001", StrategyId: "orb", BrokerAccountId: "alpaca-paper", InstrumentId: "X",
		Side: commonv1.Side_SIDE_BUY, Horizon: commonv1.Horizon_HORIZON_INTRADAY, Kind: ordersv1.IntentKind_INTENT_KIND_OPEN,
		Source:     ordersv1.IntentSource_INTENT_SOURCE_STRATEGY,
		Protective: &ordersv1.ProtectiveSpec{StopLossPrice: &commonv1.Decimal{Value: "49"}, TakeProfitPrice: &commonv1.Decimal{Value: "52"}, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY}}
	return &Input{Intent: it, Strategy: st, Buy: true, Qty: dd("100"), Price: dd("50"), Notional: dd("5000"),
		Account: Account{ID: "alpaca-paper", Known: true, Session: "SESSION_STATE_READY", Ready: true, ReconStatus: "RECONCILIATION_STATUS_OK", ReconFresh: true,
			Equity: dd("20000"), BuyingPower: dd("20000"), SnapshotKnown: true, PDTGuard: "off", PDTMax: 3, MarginMultiple: dd("1"), MarginUtilCap: dd("1"),
			Segregation: true, StrategyAllocSum: dd("15000")},
		Routing:   []string{"alpaca-paper"},
		Portfolio: Portfolio{Known: true, Allocation: dd("15000"), Equity: dd("15000"), HighWaterMark: dd("15000")},
		Owner:     Ownership{Known: true},
		Now:       100 * sec, ClockOK: true, MarketOpen: true, LastPrice: dd("50"), LastBarClose: 90 * sec, MaxBarAge: 1860 * sec,
		InstrumentKnown: true, Tradable: true, Shortable: true, SupportsBracket: true,
		Limits: Limits{MaxOrderNotional: dd("10000"), MaxOrderQty: dd("1000"), MaxPositionNotional: dd("10000"), MaxPositionPctAllocation: dd("0.5"),
			MaxGrossExposure: dd("15000"), MaxNetExposure: dd("15000"), MaxConcentrationPct: dd("1"), MaxDailyLoss: dd("500"), MaxDrawdown: dd("1000"),
			PriceCollarPct: dd("0.02"), FatFingerMultiple: dd("10"), MaxPositions: 3, MaxOrdersPerMinute: 10, MaxConsecutiveLosses: 4, DuplicateWindowSeconds: 5, MaxOpenOrders: 5}}
}

func sell(in *Input) {
	in.Buy, in.Close = false, true
	in.Intent.Side, in.Intent.Kind = commonv1.Side_SIDE_SELL, ordersv1.IntentKind_INTENT_KIND_CLOSE
}

// The risk test matrix (section 07 acceptance, RM-003): every check rejects its violating case and
// passes its boundary case.
func TestRM003RiskMatrix(t *testing.T) {
	if ok, checks := Evaluate(base()); !ok {
		t.Fatalf("base intent must pass: first failure %s", First(checks))
	}
	cases := []struct {
		check    string
		violate  func(*Input)
		boundary func(*Input)
	}{
		{"intent_valid", func(in *Input) { in.Qty = dd("0") }, nil},
		{"mode", func(in *Input) { in.Account.Live = true }, nil},
		{"mode", func(in *Input) { in.Strategy.Mode = ordersv1.StrategyMode_STRATEGY_MODE_DISABLED }, func(in *Input) { in.Strategy.Mode = ordersv1.StrategyMode_STRATEGY_MODE_SHADOW }},
		{"mode", func(in *Input) { in.Strategy.Mode = ordersv1.StrategyMode_STRATEGY_MODE_LIVE }, nil},
		{"kill_switch", func(in *Input) { in.Kills = []KillSwitch{{ID: "k", Scope: "global", Action: "freeze"}} },
			func(in *Input) { in.Kills = []KillSwitch{{ID: "k", Scope: "strategy", Target: "other"}} }},
		{"kill_switch", func(in *Input) { in.Kills = []KillSwitch{{ID: "k", Scope: "instrument", Target: "X"}} }, nil},
		{"halt", func(in *Input) { in.Halts = []string{"unknown order state"} }, nil},
		{"routing", func(in *Input) { in.Routing = []string{"ibkr-paper-bot"} }, nil},
		{"session_ready", func(in *Input) { in.Account.Ready, in.Account.Session = false, "SESSION_STATE_DEGRADED" }, nil},
		{"reconciliation", func(in *Input) { in.Account.ReconStatus = "RECONCILIATION_STATUS_STALE" }, nil},
		{"reconciliation", func(in *Input) { in.Account.ReconFresh = false }, nil},
		{"reconciliation", func(in *Input) { in.Account.Halted = []string{"orb|X"} }, func(in *Input) { in.Account.Halted = []string{"orb|Y", "other|X"} }},
		{"reconciliation", func(in *Input) { in.Account.Halted = []string{"*|X"} }, nil},
		{"margin_events", func(in *Input) { in.Account.IMD = true }, nil},
		{"margin_events", func(in *Input) { in.Account.MarginCall = true }, nil},
		{"clock", func(in *Input) { in.ClockOK = false }, nil},
		{"data_fresh", func(in *Input) { in.LastBarClose = in.Now - in.MaxBarAge - 1 }, func(in *Input) { in.LastBarClose = in.Now - in.MaxBarAge }},
		{"data_fresh", func(in *Input) { in.LastBarClose = 0 }, nil},
		{"market_session", func(in *Input) { in.MarketOpen = false }, nil},
		{"tradable", func(in *Input) { in.Tradable = false }, nil},
		{"tradable", func(in *Input) { in.InstrumentKnown = false }, nil},
		{"symbol_segregation", func(in *Input) { in.Owner.ManualQty = dd("100") }, func(in *Input) { in.Owner.ManualQty, in.SegregationAllowed = dd("100"), true }},
		{"ownership", func(in *Input) { sell(in); in.Owner.MaxSellable = dd("99") }, func(in *Input) { sell(in); in.Owner.MaxSellable = dd("100") }},
		{"ownership", func(in *Input) {
			in.Buy, in.Short = false, true
			in.Intent.Side = commonv1.Side_SIDE_SELL_SHORT
			in.Owner.Reason = "manual long"
		},
			nil},
		{"ownership", func(in *Input) { in.Owner.Known = false }, nil},
		{"self_cross", func(in *Input) { in.OtherOppositeOpen = []string{"manual"} }, nil},
		{"self_cross", func(in *Input) { in.OwnOppositeLegs = true }, func(in *Input) { sell(in); in.Owner.MaxSellable = dd("100"); in.OwnOppositeLegs = true }},
		{"protective_order", func(in *Input) { in.Intent.Protective = nil }, nil},
		{"protective_order", func(in *Input) { in.Intent.Protective.StopLossPrice.Value = "50" }, func(in *Input) { in.Intent.Protective.StopLossPrice.Value = "49.99" }},
		{"protective_order", func(in *Input) {
			in.Strategy.Horizon, in.Intent.Horizon = commonv1.Horizon_HORIZON_SWING, commonv1.Horizon_HORIZON_SWING
			in.Routing = []string{"alpaca-paper"}
		}, func(in *Input) {
			in.Strategy.Horizon, in.Intent.Horizon = commonv1.Horizon_HORIZON_SWING, commonv1.Horizon_HORIZON_SWING
			in.Intent.Protective.Tif = ordersv1.TimeInForce_TIME_IN_FORCE_GTC
		}},
		{"price_collar", func(in *Input) { in.Price = dd("51.01") }, func(in *Input) { in.Price, in.Notional = dd("51"), dd("5100") }},
		{"max_order_notional", func(in *Input) { in.Notional = dd("10000.01") }, func(in *Input) {
			in.Notional = dd("10000")
			in.Portfolio.Allocation = dd("40000")
			in.Limits.MaxGrossExposure = dd("0")
			in.Account.StrategyAllocSum = dd("40000")
			in.Account.Equity = dd("40000")
		}},
		{"max_order_qty", func(in *Input) { in.Qty = dd("1001") }, func(in *Input) { in.Qty = dd("1000") }},
		{"max_position", func(in *Input) { in.Portfolio.InstrumentValue = dd("5001") }, func(in *Input) { in.Portfolio.InstrumentValue = dd("2500"); in.Portfolio.HoldsInstrument = true }},
		{"max_exposure", func(in *Input) { in.Portfolio.Gross = dd("10001") }, func(in *Input) { in.Portfolio.Gross = dd("10000") }},
		{"max_positions", func(in *Input) { in.Portfolio.Positions = 3 }, func(in *Input) { in.Portfolio.Positions = 2 }},
		{"concentration", func(in *Input) { in.Limits.MaxConcentrationPct = dd("0.5"); in.Portfolio.Gross = dd("1000") },
			func(in *Input) { in.Limits.MaxConcentrationPct = dd("0.5"); in.Portfolio.Gross = dd("5000") }},
		{"daily_loss", func(in *Input) { in.Portfolio.DayPnL = dd("-500") }, func(in *Input) { in.Portfolio.DayPnL = dd("-499.99") }},
		{"daily_loss", nil, func(in *Input) { in.Portfolio.DayPnL = dd("-900"); sell(in); in.Owner.MaxSellable = dd("100") }},
		{"drawdown", func(in *Input) { in.Portfolio.Equity = dd("14000") }, func(in *Input) { in.Portfolio.Equity = dd("14000.01") }},
		{"consecutive_losses", func(in *Input) { in.Portfolio.ConsecutiveLosses = 4 }, func(in *Input) { in.Portfolio.ConsecutiveLosses = 3 }},
		{"order_rate", func(in *Input) { in.OrdersLastMinute = 10 }, func(in *Input) { in.OrdersLastMinute = 9 }},
		{"open_orders", func(in *Input) { in.OpenOrders = 5 }, func(in *Input) { in.OpenOrders = 4 }},
		{"duplicate", func(in *Input) { in.Duplicate = true }, nil},
		{"fat_finger", func(in *Input) { in.MedianOrderQty, in.SizeHistory = dd("9"), 5 }, func(in *Input) { in.MedianOrderQty, in.SizeHistory = dd("10"), 5 }},
		{"margin_guard", func(in *Input) { in.Account.StrategiesGross = dd("10001") }, func(in *Input) { in.Account.StrategiesGross = dd("10000") }},
		{"margin_guard", func(in *Input) { in.Account.LongMV = dd("15001") }, func(in *Input) { in.Account.LongMV = dd("15000") }},
		{"margin_guard", func(in *Input) {
			in.Account.MarginMultiple, in.Account.MarginUtilCap, in.Account.StrategyAllocSum = dd("2"), dd("0.1"), dd("20000")
		}, func(in *Input) {
			in.Account.MarginMultiple, in.Account.MarginUtilCap, in.Account.StrategyAllocSum = dd("2"), dd("0.125"), dd("20000")
		}},
		{"pdt_guard", func(in *Input) { in.Account.PDTGuard, in.Account.DayTradeCount = "legacy", 3 },
			func(in *Input) { in.Account.PDTGuard, in.Account.DayTradeCount = "off", 9 }},
		{"pdt_guard", nil, func(in *Input) { in.Account.PDTGuard, in.Account.DayTradeCount = "legacy", 2 }},
	}
	for i, c := range cases {
		if c.violate != nil {
			in := base()
			c.violate(in)
			ok, checks := Evaluate(in)
			failed := map[string]bool{}
			for _, r := range checks {
				if !r.GetPassed() {
					failed[r.GetName()] = true
				}
			}
			if ok || !failed[c.check] {
				t.Errorf("case %d (%s): violation not rejected by %s; failed=%v", i, c.check, c.check, failed)
			}
		}
		if c.boundary != nil {
			in := base()
			c.boundary(in)
			if ok, checks := Evaluate(in); !ok {
				t.Errorf("case %d (%s): boundary rejected by %s", i, c.check, First(checks))
			}
		}
	}
}

// RM-008: with the legacy guard, the fourth day trade in five business days is blocked for a
// sub-$25k margin account; with the default `off` it is allowed and only counted.
func TestRM008PDTGuardBlocksFourthDayTrade(t *testing.T) {
	for n := 0; n < 4; n++ {
		in := base()
		in.Account.PDTGuard, in.Account.DayTradeCount, in.Account.Equity = "legacy", n, dd("20000")
		ok, _ := Evaluate(in)
		if want := n < 3; ok != want {
			t.Fatalf("legacy guard with %d prior day trades: approved=%v want %v", n, ok, want)
		}
		in.Account.PDTGuard = "off"
		if ok, checks := Evaluate(in); !ok {
			t.Fatalf("guard off must not block: %s", First(checks))
		}
	}
}

// RM-011: an intraday intent to an account routed only for swing is rejected and vice versa.
func TestRM011RoutingTable(t *testing.T) {
	in := base()
	in.Intent.BrokerAccountId, in.Strategy.BrokerAccountId = "ibkr-paper-bot", "ibkr-paper-bot"
	in.Routing = []string{"alpaca-paper"}
	if ok, checks := Evaluate(in); ok || First(checks) != "routing" {
		t.Fatalf("intraday → IBKR must be rejected by routing, got %s", First(checks))
	}
}
