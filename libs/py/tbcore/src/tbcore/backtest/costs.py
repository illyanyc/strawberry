"""Transaction cost model shared by both backtest tiers (section 08, ADR-0029, rules BT-010..BT-016).

One implementation, written over NumPy arrays so the vectorized tier can call it on whole columns
and the event-driven tier on scalars. Components:

* half-spread per side: a regular-session value and a wider value for the first and last
  ``open_close_minutes`` of the regular session (BT-011), optionally the OHLC "EDGE" estimator of
  Ardia, Guidotti and Kroencke (JFE 2024) computed on trailing bars;
* square-root impact ``k * sigma_daily * sqrt(qty / ADV)`` (BT-012);
* broker commissions and the dated regulatory fee schedule (SEC Section 31, FINRA TAF, CAT) (BT-013);
* borrow fees on short notional per calendar day held (BT-014).

``cost_multiplier`` scales every component; 2.0 is the cost stress every report shows (BT-015).
Prices and fees come out as floats; the event-driven tier rounds them to decimals at the simulated
broker boundary (``to_price``/``to_fee``) before they reach the ledger (BT-016, OE-007).
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pydantic import BaseModel, ConfigDict, Field

F64 = NDArray[np.float64]


class RegulatoryFees(BaseModel):
    """One row of the dated pass-through fee schedule (applies to trades on or after ``effective_from``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effective_from: date
    sec_per_million: float  # SEC Section 31, on sale proceeds
    taf_per_share: float  # FINRA Trading Activity Fee, shares sold
    taf_max: float  # TAF cap per trade
    cat_per_share: float  # FINRA Consolidated Audit Trail, buys and sells


# Verified 2026-10-18 against SEC Fee Rate Advisory FY2026 (2026-02-27), FINRA fee adjustment schedule
# (SR-FINRA-2024-019), SR-FINRA-2026-021 (TAF holiday Oct-Dec 2026), and the Alpaca and IBKR fee
# schedules (CAT $0.000003/share). Rows before 2024-05-22 are approximations; see backtesting.md.
DEFAULT_REGULATORY_FEES: tuple[RegulatoryFees, ...] = (
    RegulatoryFees(
        effective_from=date(2015, 1, 1), sec_per_million=22.90, taf_per_share=0.000166, taf_max=8.30, cat_per_share=0.0
    ),
    RegulatoryFees(
        effective_from=date(2023, 5, 22), sec_per_million=8.00, taf_per_share=0.000166, taf_max=8.30, cat_per_share=0.0
    ),
    RegulatoryFees(
        effective_from=date(2024, 5, 22), sec_per_million=27.80, taf_per_share=0.000166, taf_max=8.30, cat_per_share=0.0
    ),
    RegulatoryFees(
        effective_from=date(2025, 5, 14),
        sec_per_million=0.0,
        taf_per_share=0.000166,
        taf_max=8.30,
        cat_per_share=0.000003,
    ),
    RegulatoryFees(
        effective_from=date(2026, 1, 1),
        sec_per_million=0.0,
        taf_per_share=0.000195,
        taf_max=9.79,
        cat_per_share=0.000003,
    ),
    RegulatoryFees(
        effective_from=date(2026, 4, 4),
        sec_per_million=20.60,
        taf_per_share=0.000195,
        taf_max=9.79,
        cat_per_share=0.000003,
    ),
    RegulatoryFees(
        effective_from=date(2026, 10, 1), sec_per_million=20.60, taf_per_share=0.0, taf_max=0.0, cat_per_share=0.000003
    ),
    RegulatoryFees(
        effective_from=date(2027, 1, 1),
        sec_per_million=20.60,
        taf_per_share=0.000195,
        taf_max=9.79,
        cat_per_share=0.000003,
    ),
)

Broker = Literal["alpaca", "ibkr_tiered", "ibkr_fixed", "none"]


class CostModelConfig(BaseModel):
    """Cost model parameters (part of ``BacktestConfig``; its hash is part of the run identity)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    broker: Broker = "alpaca"
    half_spread_bps: float = Field(default=1.5, ge=0)
    open_close_half_spread_bps: float = Field(default=5.0, ge=0)
    open_close_minutes: int = Field(default=30, ge=0, le=195)
    spread_estimator: Literal["fixed", "edge"] = "fixed"
    # Floor for the EDGE estimate (an estimator can return ~0 on quiet windows).
    min_half_spread_bps: float = Field(default=0.5, ge=0)
    impact_k: float = Field(default=0.1, ge=0)
    # Used when ADV or daily volatility is unknown at decision time (fail pessimistic, BT-012).
    fallback_impact_bps: float = Field(default=10.0, ge=0)
    borrow_fee_bps_annual: float = Field(default=30.0, ge=0)
    # IBKR tiered pass-throughs (per share) and pass-through rates on the commission.
    ibkr_exchange_per_share: float = Field(default=0.0030, ge=0)
    ibkr_clearing_per_share: float = Field(default=0.00020, ge=0)
    ibkr_passthrough_rate: float = Field(default=0.000175 + 0.00056, ge=0)
    cost_multiplier: float = Field(default=1.0, gt=0)
    regulatory_fees: tuple[RegulatoryFees, ...] = DEFAULT_REGULATORY_FEES

    def stressed(self, multiplier: float = 2.0) -> CostModelConfig:
        """The same model with every cost component scaled (BT-015)."""
        return self.model_copy(update={"cost_multiplier": self.cost_multiplier * multiplier})


def _arr(x: ArrayLike) -> F64:
    return np.asarray(x, dtype=np.float64)


def half_spread_bps(cfg: CostModelConfig, minute_of_session: ArrayLike, estimate_bps: ArrayLike | None = None) -> F64:
    """Half-spread per side in bps for trades at ``minute_of_session`` (0 = 09:30 ET) (BT-011).

    The open/close regime covers the first and the last ``open_close_minutes`` of the 390-minute
    regular session. ``estimate_bps`` (EDGE half-spread from trailing bars) replaces the regular
    value when ``spread_estimator="edge"``; the open/close regime keeps the larger of the two.
    """
    m = _arr(minute_of_session)
    base = np.full_like(m, cfg.half_spread_bps)
    if cfg.spread_estimator == "edge" and estimate_bps is not None:
        est = _arr(estimate_bps)
        base = np.where(np.isfinite(est), np.maximum(est, cfg.min_half_spread_bps), cfg.half_spread_bps)
    wide = (m < cfg.open_close_minutes) | (m >= 390 - cfg.open_close_minutes)
    return np.where(wide, np.maximum(base, cfg.open_close_half_spread_bps), base)


def impact_bps(cfg: CostModelConfig, qty: ArrayLike, adv_shares: ArrayLike, daily_vol: ArrayLike) -> F64:
    """Square-root impact ``k * sigma * sqrt(qty / ADV)`` in bps; the fallback when ADV/vol is unknown."""
    q, adv, vol = _arr(qty), _arr(adv_shares), _arr(daily_vol)
    known = np.isfinite(adv) & (adv > 0) & np.isfinite(vol) & (vol >= 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = cfg.impact_k * vol * np.sqrt(np.abs(q) / adv) * 1e4
    return np.where(known, raw, cfg.fallback_impact_bps)


def slippage_bps(
    cfg: CostModelConfig,
    qty: ArrayLike,
    minute_of_session: ArrayLike,
    adv_shares: ArrayLike,
    daily_vol: ArrayLike,
    estimate_bps: ArrayLike | None = None,
) -> F64:
    """Total price concession per side in bps: (half-spread + impact) x cost multiplier."""
    hs = half_spread_bps(cfg, minute_of_session, estimate_bps)
    return (hs + impact_bps(cfg, qty, adv_shares, daily_vol)) * cfg.cost_multiplier


def apply_slippage(price: ArrayLike, side: ArrayLike, bps: ArrayLike) -> F64:
    """Execution price: buys (side=+1) pay up, sells (side=-1) receive less."""
    return _arr(price) * (1.0 + _arr(side) * _arr(bps) / 1e4)


def regulatory_row(cfg: CostModelConfig, d: date) -> RegulatoryFees:
    row = cfg.regulatory_fees[0]
    for r in cfg.regulatory_fees:
        if r.effective_from <= d:
            row = r
    return row


def _midnight_ns(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) * 1_000_000_000


def fees(cfg: CostModelConfig, side: ArrayLike, qty: ArrayLike, price: ArrayLike, ts_ns: ArrayLike) -> F64:
    """Commission plus pass-through fees per fill in dollars (BT-013), times the cost multiplier.

    ``side`` is +1 for buys, -1 for sells (including short sales). Each component is rounded up to
    the cent per fill (Alpaca aggregates per day before rounding; per-fill rounding is conservative
    by at most one cent per component and fill).
    """
    s, q, p = _arr(side), np.abs(_arr(qty)), _arr(price)
    ts = np.asarray(ts_ns, dtype=np.int64)
    s, q, p, ts = np.broadcast_arrays(s, q, p, ts)
    notional = q * p
    sell = s < 0
    out = np.zeros(q.shape, dtype=np.float64)
    if cfg.broker == "none":
        return out
    table = cfg.regulatory_fees
    bounds = np.array([_midnight_ns(r.effective_from) for r in table], dtype=np.int64)
    idx = np.clip(np.searchsorted(bounds, ts, side="right") - 1, 0, len(table) - 1)
    sec_rate = np.array([r.sec_per_million / 1e6 for r in table])[idx]
    taf = np.array([r.taf_per_share for r in table])[idx]
    taf_max = np.array([r.taf_max for r in table])[idx]
    cat = np.array([r.cat_per_share for r in table])[idx]
    sec = np.where(sell, _ceil_cents(sec_rate * notional), 0.0)
    taf_fee = np.where(sell, _ceil_cents(np.minimum(taf * q, taf_max)), 0.0)
    cat_fee = _ceil_cents(cat * q)
    reg = sec + taf_fee + cat_fee
    if cfg.broker == "alpaca":
        out = reg
    elif cfg.broker == "ibkr_fixed":
        comm = np.minimum(np.maximum(q * 0.005, 1.00), 0.01 * notional)
        out = _round_cents(comm) + reg
    else:  # ibkr_tiered
        comm = np.minimum(np.maximum(q * 0.0035, 0.35), 0.01 * notional)
        clearing = np.minimum(q * cfg.ibkr_clearing_per_share, 0.005 * notional)
        exch = q * cfg.ibkr_exchange_per_share
        passthrough = comm * cfg.ibkr_passthrough_rate
        out = _round_cents(comm + clearing + exch + passthrough) + reg
    return out * cfg.cost_multiplier


def borrow_cost(
    cfg: CostModelConfig, short_notional: ArrayLike, days_held: ArrayLike, fee_bps_annual: ArrayLike | None = None
) -> F64:
    """Borrow fee in dollars on short notional for calendar days held (BT-014)."""
    rate = _arr(cfg.borrow_fee_bps_annual if fee_bps_annual is None else fee_bps_annual)
    return np.abs(_arr(short_notional)) * rate / 1e4 * _arr(days_held) / 360.0 * cfg.cost_multiplier


def _ceil_cents(x: F64) -> F64:
    return np.ceil(np.round(x * 100.0, 9)) / 100.0


def _round_cents(x: F64) -> F64:
    return np.round(x * 100.0) / 100.0


def edge_half_spread_bps(open_: ArrayLike, high: ArrayLike, low: ArrayLike, close: ArrayLike) -> float:
    """EDGE bid-ask spread estimator (Ardia, Guidotti, Kroencke, JFE 2024) over one window of bars,
    returned as a half-spread in bps (NaN when the window carries too little information)."""
    o, h, lo, c = (np.log(_arr(v)) for v in (open_, high, low, close))
    if o.size < 3:
        return math.nan
    m = (h + lo) / 2.0
    h1, l1, c1, m1 = h[:-1], lo[:-1], c[:-1], m[:-1]
    o, h, lo, c, m = o[1:], h[1:], lo[1:], c[1:], m[1:]
    r1, r2, r3, r4, r5 = m - o, o - m1, m - c1, c1 - m1, o - c1
    tau = ((h != lo) | (lo != c1)).astype(np.float64)
    po1 = tau * (o != h)
    po2 = tau * (o != lo)
    pc1 = tau * (c1 != h1)
    pc2 = tau * (c1 != l1)
    pt = float(np.mean(tau))
    po = float(np.mean(po1) + np.mean(po2))
    pc = float(np.mean(pc1) + np.mean(pc2))
    if np.sum(tau) < 2 or po == 0 or pc == 0 or pt == 0:
        return math.nan
    d1 = r1 - np.mean(r1) / pt * tau
    d3 = r3 - np.mean(r3) / pt * tau
    d5 = r5 - np.mean(r5) / pt * tau
    x1 = -4.0 / po * d1 * r2 - 4.0 / pc * d3 * r4
    x2 = -4.0 / po * d1 * r5 - 4.0 / pc * d5 * r4
    e1, e2 = float(np.mean(x1)), float(np.mean(x2))
    v1 = float(np.mean(x1**2) - e1**2)
    v2 = float(np.mean(x2**2) - e2**2)
    vt = v1 + v2
    s2 = (v2 * e1 + v1 * e2) / vt if vt > 0 else (e1 + e2) / 2.0
    return math.sqrt(abs(s2)) / 2.0 * 1e4


def to_price(x: float) -> Decimal:
    """Simulated execution price as a decimal with four places (BT-016)."""
    return Decimal(repr(float(x))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def to_fee(x: float) -> Decimal:
    """Simulated fee as a decimal in cents, rounded up (BT-016)."""
    return Decimal(repr(round(float(x), 9))).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
