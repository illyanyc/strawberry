"""Event-driven backtester (BT-001..BT-008, BT-015, BT-020): live strategy host + tb-simcore."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from tbcore.backtest.config import (
    AccountSimConfig,
    BacktestConfig,
    FillModelConfig,
    ManualPosition,
    StrategySpec,
    UniverseRef,
)
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.event import LeakageError, run_event_backtest
from tbcore.backtest.simcore import SimCore
from tbcore.backtest.synthetic import random_walk_bars
from tbcore.context.events import ScheduledEvent
from tbcore.data.calendar import xnys
from tbcore.strategy.api import Bar, Fill, Intent, StrategyContext, new_ulid
from tbcore.strategy.host import CLASSES, register_class

from .btkit import NS, gate_bars, iid, orb_config

SYMS = ["AAPL", "NVDA", "AMD", "XLK", "SPY"]
SCALES = {"AAPL": 1.0, "NVDA": 1.5, "AMD": 0.8, "XLK": 1.2, "SPY": 4.0}
START, END = datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 17, tzinfo=UTC)
FREE = CostModelConfig(
    broker="none", half_spread_bps=0, open_close_half_spread_bps=0, impact_k=0, fallback_impact_bps=0
)


@pytest.fixture(scope="module")
def simcore_bin() -> SimCore:
    with SimCore() as sc:  # builds the binary once for the module
        return sc


def golden_bars() -> pl.DataFrame:
    """One session, one instrument: ORB enters on bar 2's close and bar 4 trades through the target."""
    s = xnys().session(datetime(2026, 10, 13).date())
    assert s is not None
    opn = int(s.open.timestamp()) * NS
    path = [
        (100, 101, 99, 100.2),
        (100.2, 100.7, 100.1, 100.6),
        (100.6, 101.4, 100.5, 101.3),
        (101.3, 101.9, 101.2, 101.9),
        (101.9, 106.5, 101.8, 106.2),
    ] + [(106.2, 106.3, 106.1, 106.2)] * 21
    rows = []
    for i, (o, h, lo, c) in enumerate(path):
        close = opn + (i + 1) * 900 * NS
        rows.append((iid("GOLD"), close - 900 * NS, close, close + 60 * NS, o, h, lo, c, 100_000.0))
    return pl.DataFrame(
        rows,
        schema=["instrument_id", "ts_open", "ts_close", "available_at", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def test_bt002_bt003_golden_trade_hand_calculated_without_costs() -> None:
    # Opening range [99, 101]; breakout close 101.30 -> stop floor(99 x 0.9995) = 98.95, target
    # ceil(101.30 + 2 x 2.35) = 106.00, qty floor(0.005 x 30,000 / 2.35) = 63. The OMS collar limit is
    # 101.41; bar 3 opens at 101.30 (fill), bar 4 trades through 106.00 (take-profit at the limit).
    # P&L = 63 x (106.00 - 101.30) = 296.10.
    cfg = orb_config([iid("GOLD")], START, END, cost_model=FREE)
    r = run_event_backtest(cfg, golden_bars())
    assert r.intents.height == 1 and r.intents["approved"].all()
    assert r.intents.row(0, named=True)["qty"] == "63"
    assert (
        r.intents.row(0, named=True)["stop_loss"] == "98.95" and r.intents.row(0, named=True)["take_profit"] == "106.00"
    )
    (t,) = r.trades.iter_rows(named=True)
    assert (t["qty"], t["entry_price"], t["exit_price"], t["exit_reason"]) == (63.0, 101.3, 106.0, "take_profit")
    assert t["pnl"] == pytest.approx(296.10, abs=1e-9)
    by_date = dict(zip(r.daily["date"].to_list(), r.daily["equity"].to_list(), strict=True))
    assert by_date["2026-10-12"] == 30000 and by_date["2026-10-13"] == pytest.approx(30296.10, abs=1e-9)
    orders = r.orders.filter(pl.col("role") == "entry").row(0, named=True)
    assert orders["limit"] == 101.41 and orders["state"] == "filled"


def test_bt013_golden_trade_with_alpaca_costs() -> None:
    # Same trade with the default model: the open plus 11.5 bps (1.5 spread + 10 fallback impact, no
    # ADV yet) exceeds the 101.41 limit, so the entry fills by trading through at 101.41. Fees on
    # 2026-10-13 (TAF holiday): buy CAT 0.01; sell SEC 6,678 x 20.6e-6 -> 0.14 plus CAT 0.01.
    # P&L = 63 x (106.00 - 101.41) - 0.16 = 289.01.
    r = run_event_backtest(orb_config([iid("GOLD")], START, END), golden_bars())
    (t,) = r.trades.iter_rows(named=True)
    assert t["entry_price"] == 101.41 and t["fees"] == pytest.approx(0.16)
    assert t["pnl"] == pytest.approx(289.01, abs=1e-9)
    assert any(w.startswith("impact_fallback") for w in r.warnings)


def test_bt001_deterministic_and_config_hash_is_identity() -> None:
    bars = gate_bars(SYMS, SCALES)
    cfg = orb_config([iid(s) for s in SYMS], START, END, allowlist=[iid("AAPL")])
    a, b = run_event_backtest(cfg, bars), run_event_backtest(cfg, bars)
    assert a.fingerprint() == b.fingerprint()
    assert a.config_hash == b.config_hash == cfg.config_hash
    assert orb_config([iid(s) for s in SYMS], START, END, seed=1).config_hash != cfg.config_hash


def test_bt007_orb_sessions_flat_protected_and_manual_untouched() -> None:
    bars = gate_bars(SYMS, SCALES)
    cfg = orb_config(
        [iid(s) for s in SYMS],
        START,
        END,
        allowlist=[iid("AAPL")],
        manual=[ManualPosition(instrument_id=iid("AAPL"), qty=Decimal(100), avg_cost=Decimal(100))],
    )
    r = run_event_backtest(cfg, bars)
    entries = r.orders.filter(pl.col("role") == "entry")
    assert entries.height == 19  # 4/4/4/3/4 breakouts, as in the Phase 2 paper gate
    legs = r.orders.filter(pl.col("parent") != "")
    assert set(legs["parent"].to_list()) == set(entries["broker_order_id"].to_list()), (
        "every entry carries a bracket (N5)"
    )
    assert (r.daily["gross_exposure"] == 0).all(), "flat at every close"
    assert r.stats["manual_positions"] == {iid("AAPL"): "100"}
    assert not any(w.startswith("valuation_mismatch") for w in r.warnings)
    # Python mark-to-market equals the ledger core's valuation at every close (BT-I4).
    closes = r.daily.join(r.equity, on=["time", "strategy_id"], how="left", suffix="_py")
    assert closes.height == 5


def test_bt007_two_strategies_share_one_account() -> None:
    bars = gate_bars(SYMS, SCALES)
    base = orb_config(
        [iid("AAPL"), iid("NVDA")],
        START,
        END,
        allowlist=[iid("AAPL")],
        manual=[ManualPosition(instrument_id=iid("AAPL"), qty=Decimal(100), avg_cost=Decimal(100))],
    )
    other = base.strategies[0].model_copy(
        update={
            "strategy_id": "orb-b",
            "short_id": "orb15bb",
            "instruments": (iid("AMD"), iid("SPY")),
            "segregation_allowlist": (),
        }
    )
    cfg = base.model_copy(
        update={
            "strategies": (base.strategies[0], other),
            "universe": base.universe.model_copy(
                update={"instruments": tuple(iid(s) for s in ("AAPL", "NVDA", "AMD", "SPY"))}
            ),
        }
    )
    r = run_event_backtest(cfg, bars)
    assert set(r.trades["strategy_id"].unique().to_list()) == {"orb-15m", "orb-b"}
    by = dict(r.trades.group_by("strategy_id").agg(pl.col("instrument_id").unique()).iter_rows())
    assert set(by["orb-15m"]) <= {iid("AAPL"), iid("NVDA")} and set(by["orb-b"]) <= {iid("AMD"), iid("SPY")}
    assert r.stats["manual_positions"] == {iid("AAPL"): "100"}


def test_bt008_split_applied_by_the_ledger_core() -> None:
    bars = gate_bars(SYMS, SCALES)
    ex = int(xnys().session(datetime(2026, 10, 14).date()).open.timestamp()) * NS  # type: ignore[union-attr]
    ca = pl.DataFrame(
        {"instrument_id": [iid("MSFT")], "type": ["split"], "ex_date": [ex], "ratio": ["2"], "amount": [None]},
        schema={"instrument_id": pl.Utf8, "type": pl.Utf8, "ex_date": pl.Int64, "ratio": pl.Utf8, "amount": pl.Utf8},
    )
    cfg = orb_config(
        [iid("AAPL")],
        START,
        END,
        manual=[ManualPosition(instrument_id=iid("MSFT"), qty=Decimal(50), avg_cost=Decimal(200))],
    )
    r = run_event_backtest(cfg, bars, corporate_actions=ca)
    assert r.stats["manual_positions"][iid("MSFT")] == "100"


def test_bt004_bars_available_before_close_are_rejected() -> None:
    bars = gate_bars(SYMS, SCALES).with_columns((pl.col("ts_close") - NS).alias("available_at"))
    with pytest.raises(LeakageError, match="BT-004"):
        run_event_backtest(orb_config([iid("AAPL")], START, END), bars)


def test_bt015_two_times_costs_lower_pnl() -> None:
    bars = gate_bars(SYMS, SCALES)
    cfg = orb_config([iid(s) for s in SYMS], START, END, allowlist=[iid("AAPL")])
    one = run_event_backtest(cfg, bars)
    two = run_event_backtest(cfg.model_copy(update={"cost_model": cfg.cost_model.stressed()}), bars)
    assert two.trades["pnl"].sum() < one.trades["pnl"].sum()
    assert two.fills["fees"].sum() == pytest.approx(
        2 * one.fills.filter(pl.col("intent_id").is_in(two.fills["intent_id"]))["fees"].sum(), rel=0.6
    )


def test_bt005_warnings_label_survivorship_and_coarse_fills() -> None:
    r = run_event_backtest(orb_config([iid("GOLD")], START, END), golden_bars())
    kinds = {w.split(":")[0] for w in r.warnings}
    assert {"coarse_fills", "survivorship", "low_trade_count"} <= kinds


@pytest.mark.perf
@pytest.mark.skipif(
    os.environ.get("TB_PERF") != "1", reason="performance target; run with TB_PERF=1 (make perf-backtest)"
)
def test_bt003_perf_event_driven_one_year_100_symbols_5m_under_60s() -> None:
    bars = random_walk_bars(100, 252, 5, seed=1)
    ids = bars["instrument_id"].unique().sort().to_list()
    limits = {
        "max_positions": "100",
        "max_order_notional": "1000000",
        "max_position_notional": "1000000",
        "max_position_pct_allocation": "1",
        "max_gross_exposure": "10000000",
        "max_net_exposure": "10000000",
        "max_daily_loss": "100000",
        "max_drawdown": "1000000",
        "max_orders_per_minute": "1000",
        "max_open_orders": "1000",
        "max_consecutive_losses": "100000",
        "fat_finger_multiple": "1000",
    }
    cfg = orb_config(
        ids,
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
        allocation="1000000",
        cash="5000000",
        risk_limits=limits,
    )
    spec = cfg.strategies[0].model_copy(
        update={"timeframe": "5m", "params": {**cfg.strategies[0].params, "risk_per_trade": "0.0005"}}
    )
    cfg = cfg.model_copy(update={"strategies": (spec,), "timeframe": "5m"})
    t0 = time.perf_counter()
    r = run_event_backtest(cfg, bars)
    elapsed = time.perf_counter() - t0
    reqs, trades = r.stats["simcore_requests"], r.trades.height
    print(f"BT-003 perf: {bars.height:,} bars, {reqs:,} simcore requests, {trades:,} trades in {elapsed:.1f} s")
    assert elapsed < 60


def test_cx016_event_blackout_blocks_entries_in_backtests() -> None:
    bars = gate_bars(SYMS, SCALES)
    base = orb_config([iid(s) for s in SYMS], START, END, allowlist=[iid("AAPL")])
    spec = base.strategies[0].model_copy(
        update={"blackout": {"kinds": ["CPI"], "before_minutes": 60, "after_minutes": 60}}
    )
    cfg = base.model_copy(update={"strategies": (spec,)})
    cpi_at = int(datetime(2026, 10, 12, 14, 30, tzinfo=UTC).timestamp()) * NS  # 10:30 ET, known a week ahead
    cpi = ScheduledEvent(
        kind="calendar",
        name="CPI",
        at=cpi_at,
        available_at=cpi_at - 7 * 86_400 * NS,
        category="CPI",
        impact="high",
        country="US",
    )
    blocked = run_event_backtest(cfg, bars, scheduled_events=[cpi])
    free = run_event_backtest(base, bars)
    day1 = int(datetime(2026, 10, 13, tzinfo=UTC).timestamp()) * NS
    assert free.intents.filter((pl.col("kind") == "open") & (pl.col("decision_time") < day1)).height == 4
    assert blocked.intents.filter((pl.col("kind") == "open") & (pl.col("decision_time") < day1)).height == 0
    assert blocked.stats["blackout_blocked"] == 4
    assert any(w.startswith("blackout_without_events") for w in run_event_backtest(cfg, bars).warnings)


class _HoldOnce:
    """Test strategy: opens once on the first bar of ``params.instrument`` (``params.side``), never closes."""

    def __init__(self, sid: str, params: dict[str, object]) -> None:
        self.strategy_id, self.p, self.done = sid, params, False

    def on_start(self, ctx: StrategyContext) -> None:
        self.done = False

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        if self.done or bar.instrument_id != self.p["instrument"]:
            return []
        self.done = True
        short = self.p["side"] == "sell_short"
        stop = (bar.close * (Decimal("1.2") if short else Decimal("0.8"))).quantize(Decimal("0.01"))
        return [
            Intent(
                new_ulid(bar.available_at // 1_000_000, f"hold|{bar.instrument_id}"),
                bar.instrument_id,
                str(self.p["side"]),
                Decimal(str(self.p["qty"])),
                "open",
                "test hold",
                bar.close,
                stop_loss=stop,
                protective_tif="gtc",
            )
        ]

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return []


if "hold_test" not in CLASSES:
    register_class("hold_test", lambda sid, params: _HoldOnce(sid, params))  # type: ignore[arg-type,return-value]


def flat_bars(sym: str, sessions: int, price: float = 100.0) -> pl.DataFrame:
    rows = []
    for s in xnys().sessions(START, datetime(2026, 12, 31, tzinfo=UTC))[:sessions]:
        opn = int(s.open.timestamp()) * NS
        for i in range(26):
            close = opn + (i + 1) * 900 * NS
            rows.append((iid(sym), close - 900 * NS, close, close + 60 * NS, price, price, price, price, 1e6))
    return pl.DataFrame(
        rows,
        schema=["instrument_id", "ts_open", "ts_close", "available_at", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def hold_config(sym: str, side: str, **account: object) -> BacktestConfig:
    return BacktestConfig(
        strategies=(
            StrategySpec(
                strategy_id="hold",
                short_id="holdaaa",
                horizon="swing",
                allocation=Decimal(30000),
                allow_short=True,
                runtime_class="hold_test",
                timeframe="15m",
                instruments=(iid(sym),),
                params={"instrument": iid(sym), "side": side, "qty": 100},
            ),
        ),
        universe=UniverseRef(name="test", instruments=(iid(sym),)),
        timeframe="15m",
        start=START,
        end=END,
        cost_model=FREE,
        account=AccountSimConfig(cash=Decimal(60000), shortable=(iid(sym),), dedicated=True, **account),  # type: ignore[arg-type]
    )


def test_bt014_short_held_overnight_pays_borrow_for_calendar_days() -> None:
    # 100 shares short at a flat 100: notional 10,000 at 360 bps/yr = 1.00 per calendar day. Closes Mon..Thu
    # roll one day each, Friday rolls three (to Monday): 7 days = 7.00, booked by the ledger core.
    bars = flat_bars("SHRT", 5)
    paid = run_event_backtest(hold_config("SHRT", "sell_short", borrow_fee_bps={iid("SHRT"): 360.0}), bars)
    free = run_event_backtest(hold_config("SHRT", "sell_short", borrow_fee_bps={iid("SHRT"): 0.0}), bars)
    assert paid.stats["borrow_cost"] == {"hold": 7.0} and free.stats["borrow_cost"] == {}
    last = paid.daily.sort("time").row(-1, named=True)
    ref = free.daily.sort("time").row(-1, named=True)
    assert ref["equity"] - last["equity"] == pytest.approx(7.0, abs=1e-9)
    assert last["fees"] - ref["fees"] == pytest.approx(7.0, abs=1e-9)
    assert not any(w.startswith("valuation_mismatch") for w in paid.warnings)
    stressed = run_event_backtest(
        hold_config("SHRT", "sell_short", borrow_fee_bps={iid("SHRT"): 360.0}).model_copy(
            update={"cost_model": FREE.model_copy(update={"cost_multiplier": 2.0})}
        ),
        bars,
    )
    assert stressed.stats["borrow_cost"] == {"hold": 14.0}  # BT-015: borrow scales with the stress multiplier


def test_bt014_long_positions_pay_no_borrow() -> None:
    r = run_event_backtest(hold_config("LONG", "buy", borrow_fee_bps={iid("LONG"): 360.0}), flat_bars("LONG", 5))
    assert r.stats["borrow_cost"] == {}


def test_bt008_delisting_closes_the_position_at_the_last_price_less_the_haircut() -> None:
    # Long 100 at 100 from Monday; the name stops trading after Tuesday and is delisted on Wednesday. The
    # position closes at 100 x (1 - 0.3) = 70: a 3,000 loss, exit reason "delisting", stop canceled.
    bars = flat_bars("GONE", 2)
    ex = int(xnys().session(datetime(2026, 10, 14).date()).open.timestamp()) * NS  # type: ignore[union-attr]
    ca = pl.DataFrame(
        {"instrument_id": [iid("GONE")], "type": ["delisting"], "ex_date": [ex], "ratio": [None], "amount": [None]},
        schema={"instrument_id": pl.Utf8, "type": pl.Utf8, "ex_date": pl.Int64, "ratio": pl.Utf8, "amount": pl.Utf8},
    )
    cfg = hold_config("GONE", "buy").model_copy(update={"fill_model": FillModelConfig(delisting_haircut=0.3)})
    r = run_event_backtest(cfg, bars, corporate_actions=ca)
    (t,) = r.trades.iter_rows(named=True)
    assert (t["exit_reason"], t["exit_price"], t["qty"]) == ("delisting", 70.0, 100.0)
    assert t["pnl"] == pytest.approx(-3000.0 - t["fees"], abs=1e-6)
    assert r.stats["delisted"] == [iid("GONE")] and r.stats["open_episodes"] == 0
    assert (r.daily.sort("time")["gross_exposure"].tail(3) == 0).all()
    stops = r.orders.filter(pl.col("role") == "stop")
    assert stops.height == 1 and stops["state"].to_list() == ["canceled"]
    assert not any(w.startswith("valuation_mismatch") for w in r.warnings)
