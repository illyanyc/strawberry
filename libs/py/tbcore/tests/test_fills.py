"""Fill model edge cases (BT-020..BT-024)."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from tbcore.backtest.config import FillModelConfig
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.fills import FillBar, WorkingOrder, eligible, match_bar

FREE = CostModelConfig(
    broker="none", half_spread_bps=0, open_close_half_spread_bps=0, impact_k=0, fallback_impact_bps=0
)
FILL = FillModelConfig()
T0 = 1_000_000_000_000


def bar(
    o: float, h: float, lo: float, c: float, v: float = 100_000, ts: int = T0, m: int = 100, halted: bool = False
) -> FillBar:
    return FillBar("X", ts, ts + 900_000_000_000, o, h, lo, c, v, m, 1e6, 0.02, halted=halted)


def order(
    kind: str,
    side: int,
    qty: int = 100,
    limit: float | None = None,
    stop: float | None = None,
    role: str = "entry",
    arrival: int = T0 - 1,
) -> WorkingOrder:
    return WorkingOrder("o1", "tb1-x-y-1", "a", "X", side, Decimal(qty), kind, "day", arrival, role, limit, stop)


def test_bt020_never_fills_before_arrival_or_in_the_signal_bar() -> None:
    o = order("market", 1, arrival=T0 + 900_000_000_000)  # arrives after this bar closed
    assert not eligible(o, bar(10, 10, 10, 10))
    o2 = order("market", 1, arrival=T0 + 60_000_000_000)  # arrives inside the bar: fills at its open
    (d,) = match_bar([o2], bar(10, 11, 9, 10.5), FILL, FREE)
    assert d.price == 10 and d.reason == "market_open"


def test_bt021_marketable_limit_and_touch_vs_through() -> None:
    (d,) = match_bar([order("limit", 1, limit=10.10)], bar(10.0, 10.2, 9.9, 10.1), FILL, FREE)
    assert d.price == 10.0 and d.reason == "limit_open"
    # Open above a buy limit; the low touches the limit but does not trade through it: no fill.
    assert match_bar([order("limit", 1, limit=9.90)], bar(10.0, 10.2, 9.90, 10.1), FILL, FREE) == []
    # Through by a tick: fills at the limit, haircut to half of the capped volume.
    (d,) = match_bar([order("limit", 1, qty=10_000, limit=9.90)], bar(10.0, 10.2, 9.89, 10.1, v=100_000), FILL, FREE)
    assert d.price == 9.90 and d.reason == "limit_through" and d.qty == Decimal(2500)


def test_bt022_gap_through_stop_fills_at_the_open() -> None:
    (d,) = match_bar([order("stop", -1, stop=98.0, role="stop")], bar(96.5, 97.0, 96.0, 96.8), FILL, FREE)
    assert d.price == 96.5 and d.reason == "stop_gap"
    (d,) = match_bar([order("stop", -1, stop=98.0, role="stop")], bar(99.0, 99.5, 97.5, 98.2), FILL, FREE)
    assert d.price == 98.0 and d.reason == "stop"


def test_bt022_stop_first_when_both_legs_trigger_unless_open_beyond_target() -> None:
    stop = order("stop", -1, stop=98.0, role="stop")
    tp = WorkingOrder("o2", "tb1-x-y-1", "a", "X", -1, Decimal(100), "limit", "day", T0 - 1, "take_profit", 102.0, None)
    stop.siblings, tp.siblings = ["o2"], ["o1"]
    (d,) = match_bar([tp, stop], bar(100, 102.5, 97.5, 100), FILL, FREE)
    assert d.order is stop
    (d,) = match_bar([tp, stop], bar(103, 103.5, 97.5, 100), FILL, FREE)
    assert d.order is tp and d.price == 103


def test_bt023_participation_cap_partial_fill() -> None:
    o = order("market", 1, qty=10_000)
    (d,) = match_bar([o], bar(10, 10, 10, 10, v=50_000), FILL, FREE)
    assert d.qty == Decimal(2500)  # 5 % of 50,000


def test_bt024_halts_zero_volume_and_closed_market() -> None:
    assert match_bar([order("market", 1)], bar(10, 10, 10, 10, halted=True), FILL, FREE) == []
    assert match_bar([order("market", 1)], bar(10, 10, 10, 10, v=0), FILL, FREE) == []
    assert match_bar([order("market", 1)], bar(10, 10, 10, 10, m=-1), FILL, FREE) == []


def test_bt010_slippage_moves_price_against_the_order() -> None:
    cost = CostModelConfig(broker="none", half_spread_bps=5, impact_k=0)
    (d,) = match_bar([order("market", 1)], bar(100, 100, 100, 100), FILL, cost)
    assert d.price == pytest.approx(100.05)
    (d,) = match_bar([order("market", -1)], bar(100, 100, 100, 100), FILL, cost)
    assert d.price == pytest.approx(99.95)
    assert not math.isnan(d.slippage_bps)


def test_bt021_after_open_legs_cannot_use_the_open() -> None:
    (d,) = match_bar([order("stop", -1, stop=98.0, role="stop")], bar(96.5, 97, 96, 96.8), FILL, FREE, after_open=True)
    assert d.price == 98.0
    assert match_bar([order("market", 1)], bar(10, 10, 10, 10), FILL, FREE, after_open=True) == []
