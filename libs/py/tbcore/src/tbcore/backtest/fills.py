"""Fill model of the event-driven tier (ADR-0029, BT-020..BT-024).

Pure functions over a mirror of the simulated broker's working orders and one fill bar. The simcore
decides which orders exist; this module decides only whether, when, and at what price they fill.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from tbcore.backtest.config import FillModelConfig
from tbcore.backtest.costs import CostModelConfig, apply_slippage, slippage_bps

ZERO = Decimal(0)


@dataclass(slots=True)
class WorkingOrder:
    """The backtester's view of one broker-side order (from simcore ``BrokerOrder`` messages)."""

    broker_order_id: str
    client_order_id: str
    account: str
    instrument_id: str
    side: int  # +1 buy, -1 sell (short sales included)
    qty: Decimal
    kind: str  # market | limit | stop
    tif: str  # day | gtc
    arrival: int  # first instant the order can work (ns)
    role: str = "entry"  # entry | exit | stop | take_profit
    limit: float | None = None
    stop: float | None = None
    filled: Decimal = ZERO
    held: bool = False
    parent: str = ""
    intent_id: str = ""
    extended: bool = False
    siblings: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.qty - self.filled


@dataclass(frozen=True, slots=True)
class FillBar:
    instrument_id: str
    ts_open: int
    ts_close: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    minute_of_session: int  # minutes since 09:30 ET; outside [0, 390) = not regular hours
    adv: float  # average daily share volume known before the session (nan = unknown)
    daily_vol: float  # daily return volatility known before the session (nan = unknown)
    spread_est_bps: float = math.nan
    halted: bool = False

    @property
    def regular(self) -> bool:
        return 0 <= self.minute_of_session < 390


@dataclass(frozen=True, slots=True)
class FillDecision:
    order: WorkingOrder
    qty: Decimal
    price: float
    reference: float  # price before slippage (open, limit, or stop)
    slippage_bps: float
    reason: str
    impact_fallback: bool


def _shares(x: float) -> Decimal:
    return Decimal(max(math.floor(x), 0))


def eligible(o: WorkingOrder, bar: FillBar) -> bool:
    """An order works in the bar containing or following its arrival, never earlier (BT-020)."""
    return not o.held and o.remaining > 0 and bar.ts_close > o.arrival and bar.instrument_id == o.instrument_id


def _priority(o: WorkingOrder, bar: FillBar) -> tuple[int, str]:
    # Marketable orders first; then protective legs with the stop first unless the open is already
    # beyond the target (BT-022).
    if o.role == "take_profit" and o.limit is not None:
        beyond = (o.side < 0 and bar.open >= o.limit) or (o.side > 0 and bar.open <= o.limit)
        return (1 if beyond else 3, o.broker_order_id)
    if o.kind == "stop":
        return (2, o.broker_order_id)
    return (0, o.broker_order_id)


def match_bar(
    orders: list[WorkingOrder],
    bar: FillBar,
    fill: FillModelConfig,
    cost: CostModelConfig,
    after_open: bool = False,
) -> list[FillDecision]:
    """Fills for the eligible ``orders`` in one bar (BT-020..BT-024).

    ``after_open`` evaluates orders that became active inside the bar (legs released by an entry
    filled at this bar's open): the open is no longer available to them, so stops fill at the stop
    and limits only by trading through.
    """
    if bar.halted or bar.volume <= 0:
        return []
    liquidity = _shares(fill.participation_cap * bar.volume)
    out: list[FillDecision] = []
    done_oco: set[str] = set()
    for o in sorted((x for x in orders if eligible(x, bar)), key=lambda x: _priority(x, bar)):
        if not bar.regular and not (o.extended and fill.allow_extended_hours):
            continue
        if o.broker_order_id in done_oco or liquidity <= 0:
            continue
        qty_cap = min(o.remaining, liquidity)
        if not _may_fill(o, bar, fill, after_open):
            continue
        slip = float(
            slippage_bps(cost, float(qty_cap), bar.minute_of_session, bar.adv, bar.daily_vol, bar.spread_est_bps)
        )
        fallback = not (math.isfinite(bar.adv) and bar.adv > 0 and math.isfinite(bar.daily_vol))
        d = _match_one(o, bar, fill, slip, qty_cap, liquidity, after_open)
        if d is None:
            continue
        qty, px, ref, reason = d
        if qty <= 0:
            continue
        liquidity -= qty
        out.append(FillDecision(o, qty, px, ref, slip, reason, fallback))
        if qty == o.remaining:
            done_oco.update(o.siblings)
    return out


def _may_fill(o: WorkingOrder, bar: FillBar, fill: FillModelConfig, after_open: bool) -> bool:
    """Cheap pre-check before pricing: can this order trade in this bar at all?"""
    if o.kind == "market":
        return not after_open
    if o.kind == "stop" and o.stop is not None:
        return (o.side < 0 and bar.low <= o.stop) or (o.side > 0 and bar.high >= o.stop)
    if o.kind == "limit" and o.limit is not None:
        if not after_open and ((o.side > 0 and bar.open <= o.limit) or (o.side < 0 and bar.open >= o.limit)):
            return True
        through = fill.limit_through_ticks * fill.tick
        return (o.side > 0 and bar.low <= o.limit - through) or (o.side < 0 and bar.high >= o.limit + through)
    return False


def _match_one(
    o: WorkingOrder,
    bar: FillBar,
    fill: FillModelConfig,
    slip: float,
    qty_cap: Decimal,
    liquidity: Decimal,
    after_open: bool,
) -> tuple[Decimal, float, float, str] | None:
    through = fill.limit_through_ticks * fill.tick
    if o.kind == "market":
        if after_open:
            return None
        return qty_cap, float(apply_slippage(bar.open, o.side, slip)), bar.open, "market_open"
    if o.kind == "stop" and o.stop is not None:
        if o.side < 0 and bar.low <= o.stop:
            ref = o.stop if after_open else min(bar.open, o.stop)
            return qty_cap, float(apply_slippage(ref, -1, slip)), ref, "stop_gap" if ref < o.stop else "stop"
        if o.side > 0 and bar.high >= o.stop:
            ref = o.stop if after_open else max(bar.open, o.stop)
            return qty_cap, float(apply_slippage(ref, 1, slip)), ref, "stop_gap" if ref > o.stop else "stop"
        return None
    if o.kind == "limit" and o.limit is not None:
        lim = o.limit
        if not after_open:
            px = float(apply_slippage(bar.open, o.side, slip))
            if (o.side > 0 and px <= lim) or (o.side < 0 and px >= lim):
                return qty_cap, px, bar.open, "limit_open"
        crossed = (o.side > 0 and bar.low <= lim - through) or (o.side < 0 and bar.high >= lim + through)
        if crossed:
            # Queue haircut: only part of the volume traded through the limit reaches this order.
            avail = (liquidity * Decimal(str(fill.limit_queue_haircut))).to_integral_value(rounding=ROUND_FLOOR)
            return min(o.remaining, avail), lim, lim, "limit_through"
    return None
