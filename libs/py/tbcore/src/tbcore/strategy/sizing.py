"""Position sizing shared by backtest and live (RM-010: the gate may only cap it)."""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal


def fixed_fractional_qty(
    equity: Decimal, risk_fraction: Decimal, entry: Decimal, stop: Decimal, cash: Decimal
) -> Decimal:
    """Whole shares risking ``risk_fraction`` of ``equity`` between entry and stop, capped by cash.

    Returns 0 when the stop is not below the entry or nothing fits.
    """
    per_share = entry - stop
    if per_share <= 0 or entry <= 0 or equity <= 0:
        return Decimal(0)
    by_risk = (equity * risk_fraction / per_share).to_integral_value(rounding=ROUND_FLOOR)
    by_cash = (max(cash, Decimal(0)) / entry).to_integral_value(rounding=ROUND_FLOOR)
    return max(Decimal(0), min(by_risk, by_cash))
