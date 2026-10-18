"""Event-driven backtester (BT-001..BT-008, BT-015, BT-020): live strategy host + tb-simcore."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal

import polars as pl
import pytest

from tbcore.backtest.config import ManualPosition
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.event import LeakageError, run_event_backtest
from tbcore.backtest.simcore import SimCore
from tbcore.data.calendar import xnys

from .btkit import NS, gate_bars, iid, orb_config, random_walk_bars

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
    print(
        f"BT-003 perf: {bars.height:,} bars, {r.stats['simcore_requests']:,} simcore requests, {r.trades.height:,} trades in {elapsed:.1f} s"
    )
    assert elapsed < 60
