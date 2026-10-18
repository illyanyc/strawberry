"""Candidate deployment limits (EX-062..EX-064, ADR-0047).

A lineage's candidates are judged in the event-driven gate rerun under the same risk limits and
allocation they trade under on paper: the horizon profile from the owner's config file
(``TB_OPT_DEPLOYMENT_LIMITS``) overlaid field by field with an optional per-lineage override that only
the owner can set (raising needs a fresh passkey step-up, RM-007; the analyst is always denied).
Execution applies the same values in the strategy scope, which replaces the global paper defaults
(RM-019) and stays capped by account limits.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from tb.common.v1 import common_pb2
from tb.orders.v1 import execution_pb2 as ex

# Registry-seed keys (execution app.setLimit) ↔ RiskLimits fields.
DECIMAL_FIELDS = (
    "max_order_notional",
    "max_order_qty",
    "max_position_notional",
    "max_position_pct_allocation",
    "max_gross_exposure",
    "max_net_exposure",
    "max_concentration_pct",
    "max_daily_loss",
    "max_drawdown",
    "price_collar_pct",
    "fat_finger_multiple",
)
INT_FIELDS = (
    "max_positions",
    "max_orders_per_minute",
    "max_cancels_per_minute",
    "max_consecutive_losses",
    "duplicate_window_seconds",
    "max_open_orders",
)

# Shipped defaults, sized for a model strategy on its own allocation (infra/compose/deployment-limits.json
# carries the same values for the owner to edit). Global paper defaults for comparison: drawdown 2,500,
# 5 consecutive losses, 10 positions, daily loss 1,000 (risk-management.md).
DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "intraday": {
        "allocation": "20000",
        "limits": {
            "max_order_notional": "10000",
            "max_position_notional": "10000",
            "max_position_pct_allocation": "0.5",
            "max_gross_exposure": "20000",
            "max_net_exposure": "20000",
            "max_positions": "8",
            "max_daily_loss": "600",
            "max_drawdown": "3000",
            "max_consecutive_losses": "12",
        },
    },
    "swing": {
        "allocation": "50000",
        "limits": {
            "max_order_notional": "12500",
            "max_position_notional": "12500",
            "max_position_pct_allocation": "0.25",
            "max_gross_exposure": "50000",
            "max_net_exposure": "50000",
            "max_positions": "15",
            "max_daily_loss": "2000",
            "max_drawdown": "10000",
            "max_consecutive_losses": "15",
        },
    },
}


def horizon_of(timeframe: str) -> str:
    return "swing" if timeframe == "1d" else "intraday"


def load_profiles(path: str = "") -> dict[str, dict[str, Any]]:
    """Profiles from the owner's file, falling back to the shipped defaults per horizon."""
    out = {h: {"allocation": p["allocation"], "limits": dict(p["limits"])} for h, p in DEFAULT_PROFILES.items()}
    if path and Path(path).exists():
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for h in ("intraday", "swing"):
            if h in data:
                out[h] = {"allocation": str(data[h].get("allocation", out[h]["allocation"])), "limits": {}}
                for k, v in data[h].get("limits", {}).items():
                    if k not in DECIMAL_FIELDS + INT_FIELDS:
                        raise ValueError(f"deployment limits: unknown field {k!r} in {path}")
                    out[h]["limits"][k] = str(v)
    for p in out.values():
        _check_decimal("allocation", p["allocation"])
        for k, v in p["limits"].items():
            _check_decimal(k, v)
    return out


def _check_decimal(name: str, v: str) -> None:
    try:
        if Decimal(v) < 0:
            raise ValueError(f"deployment limits: {name} is negative")
    except InvalidOperation:
        raise ValueError(f"deployment limits: {name} = {v!r} is not a number") from None


def to_proto(limits: dict[str, str]) -> ex.RiskLimits:
    r = ex.RiskLimits()
    for k, v in limits.items():
        if k in DECIMAL_FIELDS:
            getattr(r, k).CopyFrom(common_pb2.Decimal(value=str(v)))
        elif k in INT_FIELDS:
            setattr(r, k, int(Decimal(str(v))))
    return r


def to_dict(r: ex.RiskLimits) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in DECIMAL_FIELDS:
        v = getattr(r, k).value
        if v and Decimal(v) != 0:
            out[k] = v
    for k in INT_FIELDS:
        n = int(getattr(r, k))
        if n:
            out[k] = str(n)
    return out


def overlay(base: dict[str, str], override: dict[str, str]) -> dict[str, str]:
    return {**base, **override}


def raised(prev: dict[str, str], prev_alloc: str, nxt: dict[str, str], nxt_alloc: str) -> bool:
    """True when any limit is looser (higher, or removed) or the allocation is higher; the duplicate
    window is the only field where lower is looser (execution ``Raised``). A field the current limits do
    not set inherits execution's global default, which the optimizer does not know, so setting it counts
    as a raise (conservative)."""
    for k in DECIMAL_FIELDS + INT_FIELDS:
        a, b = prev.get(k), nxt.get(k)
        if a is None:
            if b is not None:
                return True
            continue
        if k == "duplicate_window_seconds":
            if b is None or Decimal(b) < Decimal(a):
                return True
            continue
        if b is None or Decimal(b) > Decimal(a):
            return True
    return Decimal(nxt_alloc or "0") > Decimal(prev_alloc or "0")
