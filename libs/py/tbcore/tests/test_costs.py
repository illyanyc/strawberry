"""Cost model tests (BT-010..BT-016)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest

from tbcore.backtest.costs import (
    CostModelConfig,
    apply_slippage,
    borrow_cost,
    edge_half_spread_bps,
    fees,
    half_spread_bps,
    impact_bps,
    slippage_bps,
    to_fee,
    to_price,
)


def ns(y: int, m: int, d: int, h: int = 15) -> int:
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp()) * 1_000_000_000


def test_bt011_open_close_spread_regime() -> None:
    cfg = CostModelConfig(half_spread_bps=1.5, open_close_half_spread_bps=5.0, open_close_minutes=30)
    got = half_spread_bps(cfg, [0, 29, 30, 200, 359, 360, 389])
    assert got.tolist() == [5.0, 5.0, 1.5, 1.5, 1.5, 5.0, 5.0]


def test_bt012_square_root_impact_and_fallback() -> None:
    cfg = CostModelConfig(impact_k=0.1, fallback_impact_bps=10.0)
    # 0.1 * 2% daily vol * sqrt(10_000 / 1_000_000) = 0.0002 = 2 bps
    assert impact_bps(cfg, 10_000, 1_000_000, 0.02).item() == pytest.approx(2.0)
    assert impact_bps(cfg, 10_000, np.nan, 0.02).item() == 10.0
    assert impact_bps(cfg, 10_000, 0, 0.02).item() == 10.0


def test_bt015_cost_multiplier_scales_every_component() -> None:
    base = CostModelConfig()
    stress = base.stressed(2.0)
    assert stress.cost_multiplier == 2.0
    s1 = slippage_bps(base, 100, 200, 1e6, 0.02)
    s2 = slippage_bps(stress, 100, 200, 1e6, 0.02)
    assert s2.item() == pytest.approx(2 * s1.item())
    f1 = fees(base, -1, 100, 50.0, ns(2026, 5, 1))
    f2 = fees(stress, -1, 100, 50.0, ns(2026, 5, 1))
    assert f2.item() == pytest.approx(2 * f1.item())
    assert borrow_cost(stress, 10_000, 30).item() == pytest.approx(2 * borrow_cost(base, 10_000, 30).item())


def test_bt013_alpaca_dated_regulatory_fees() -> None:
    cfg = CostModelConfig(broker="alpaca")
    # Buy: CAT only (100 x 0.000003 rounds up to a cent).
    assert fees(cfg, 1, 100, 50.0, ns(2026, 5, 1)).item() == pytest.approx(0.01)
    # Sell 2026-05-01: SEC 5000 x 20.60/1e6 = 0.103 -> 0.11; TAF 0.0195 -> 0.02; CAT 0.01.
    assert fees(cfg, -1, 100, 50.0, ns(2026, 5, 1)).item() == pytest.approx(0.14)
    # TAF holiday (Oct-Dec 2026).
    assert fees(cfg, -1, 100, 50.0, ns(2026, 10, 15)).item() == pytest.approx(0.12)
    # SEC fee was 0 from 2025-05-14 to 2026-04-03.
    assert fees(cfg, -1, 100, 50.0, ns(2025, 6, 2)).item() == pytest.approx(0.03)
    # TAF cap: 100k shares sold.
    assert fees(cfg, -1, 100_000, 1.0, ns(2026, 2, 2)).item() == pytest.approx(9.79 + 0.30)


def test_bt013_ibkr_commissions() -> None:
    fixed = CostModelConfig(broker="ibkr_fixed")
    assert fees(fixed, 1, 100, 50.0, ns(2026, 5, 1)).item() == pytest.approx(1.01)
    # 1% of trade value caps the commission: 10 shares at $0.50.
    assert fees(fixed, 1, 10, 0.5, ns(2026, 5, 1)).item() == pytest.approx(0.05 + 0.01)
    tiered = CostModelConfig(broker="ibkr_tiered")
    # 0.35 commission + 0.02 clearing + 0.30 exchange + pass-through -> 0.67, + CAT 0.01.
    assert fees(tiered, 1, 100, 50.0, ns(2026, 5, 1)).item() == pytest.approx(0.68)


def test_bt013_vectorized_matches_scalar() -> None:
    cfg = CostModelConfig(broker="ibkr_tiered")
    side = np.array([1, -1, -1, 1])
    qty = np.array([100, 250, 3, 10_000])
    px = np.array([50.0, 12.5, 400.0, 3.2])
    ts = np.array([ns(2024, 6, 3), ns(2025, 6, 2), ns(2026, 5, 1), ns(2026, 11, 2)])
    vec = fees(cfg, side, qty, px, ts)
    for i in range(4):
        assert vec[i] == pytest.approx(fees(cfg, side[i], qty[i], px[i], ts[i]).item())


def test_bt016_decimal_boundary() -> None:
    assert to_price(101.23456) == Decimal("101.2346")
    assert to_fee(0.0201) == Decimal("0.03")
    assert to_fee(0.02) == Decimal("0.02")
    assert apply_slippage(100.0, 1, 10).item() == pytest.approx(100.1)
    assert apply_slippage(100.0, -1, 10).item() == pytest.approx(99.9)


def test_bt011_edge_estimator_recovers_a_known_spread() -> None:
    rng = np.random.default_rng(7)
    half = 0.0010  # 10 bps half-spread
    bars, ticks = 3000, 40
    mid = np.exp(np.cumsum(rng.normal(0, 0.0008, bars * ticks)))
    side = rng.choice([-1.0, 1.0], bars * ticks)
    px = (mid * (1 + side * half)).reshape(bars, ticks)
    est = edge_half_spread_bps(px[:, 0], px.max(1), px.min(1), px[:, -1])
    assert est == pytest.approx(10.0, rel=0.3)
