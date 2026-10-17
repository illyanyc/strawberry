"""IBKR order status -> normalized :class:`OrderState` (OE-005, BR-006, IB-020, IB-024).

Every status IBKR (and ``ib_async``) can report is listed in :data:`STATUS_TABLE`; anything else
maps to ``unknown``, which fails closed. The table is mirrored in
``docs/business-logic/brokers-ibkr.md``; keep both in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tb_broker_ibkr.models import OrderState

# Status -> (state when nothing has filled, state when something has filled).
STATUS_TABLE: dict[str, tuple[OrderState, OrderState]] = {
    # Transmitted, not yet acknowledged by IBKR.
    "ApiPending": (OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED),
    "PendingSubmit": (OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED),
    # Held at IBKR (stop not triggered, outside RTH, simulated order type): working, not filled.
    "PreSubmitted": (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED),
    "Submitted": (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED),
    # ib_async-only transient status after a modification is acknowledged.
    "ApiUpdate": (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED),
    "PendingCancel": (OrderState.PENDING_CANCEL, OrderState.PENDING_CANCEL),
    # Partially filled then cancelled stays CANCELED; filled_qty carries the executed part.
    "Cancelled": (OrderState.CANCELED, OrderState.CANCELED),
    "ApiCancelled": (OrderState.CANCELED, OrderState.CANCELED),
    "Filled": (OrderState.FILLED, OrderState.FILLED),
    "ValidationError": (OrderState.REJECTED, OrderState.REJECTED),
    # Inactive is resolved by context below; this row is the fallback.
    "Inactive": (OrderState.UNKNOWN, OrderState.UNKNOWN),
}


@dataclass(frozen=True, slots=True)
class StatusContext:
    """Facts outside the status string that decide ambiguous cases."""

    filled: Decimal = Decimal(0)
    remaining: Decimal = Decimal(0)
    # An order-reject error (category ORDER_REJECT) was received for this order.
    reject_error: bool = False
    # The platform asked IBKR to cancel this order.
    cancel_requested: bool = False
    # The order's time in force has lapsed (DAY after its session close, OPG after the open auction,
    # CLS after the close), computed by the adapter from the TIF and the exchange clock.
    tif_lapsed: bool = False


def map_status(status: str, ctx: StatusContext | None = None) -> OrderState:
    """Map an IBKR status string to the normalized state (OE-005)."""
    ctx = ctx or StatusContext()
    row = STATUS_TABLE.get(status)
    if row is None:
        return OrderState.UNKNOWN
    if status == "Inactive":
        # IBKR uses Inactive for rejects and for orders it parked (e.g. outside permissions/hours).
        return OrderState.REJECTED if ctx.reject_error else OrderState.UNKNOWN
    if status == "Filled" and ctx.remaining > 0:
        # Out-of-order delivery: a Filled status with quantity still open is not complete.
        return OrderState.PARTIALLY_FILLED
    if status in {"Cancelled", "ApiCancelled"} and not ctx.cancel_requested and ctx.tif_lapsed:
        return OrderState.EXPIRED
    return row[1] if ctx.filled > 0 else row[0]
