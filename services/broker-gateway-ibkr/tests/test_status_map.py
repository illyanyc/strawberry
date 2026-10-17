"""IBKR status mapping: OE-005, IB-020, IB-024."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tb_broker_ibkr.models import OrderState
from tb_broker_ibkr.status_map import STATUS_TABLE, StatusContext, map_status

ZERO = StatusContext()
PARTIAL = StatusContext(filled=Decimal(3), remaining=Decimal(7))


@pytest.mark.parametrize(
    ("status", "ctx", "expected"),
    [
        ("ApiPending", ZERO, OrderState.SUBMITTED),
        ("PendingSubmit", ZERO, OrderState.SUBMITTED),
        ("PreSubmitted", ZERO, OrderState.ACCEPTED),
        ("PreSubmitted", PARTIAL, OrderState.PARTIALLY_FILLED),
        ("Submitted", ZERO, OrderState.ACCEPTED),
        ("Submitted", PARTIAL, OrderState.PARTIALLY_FILLED),
        ("ApiUpdate", ZERO, OrderState.ACCEPTED),
        ("PendingCancel", ZERO, OrderState.PENDING_CANCEL),
        ("PendingCancel", PARTIAL, OrderState.PENDING_CANCEL),
        ("Cancelled", ZERO, OrderState.CANCELED),
        ("Cancelled", PARTIAL, OrderState.CANCELED),
        ("ApiCancelled", ZERO, OrderState.CANCELED),
        ("Filled", StatusContext(filled=Decimal(10)), OrderState.FILLED),
        ("Filled", PARTIAL, OrderState.PARTIALLY_FILLED),  # out-of-order Filled with qty open
        ("ValidationError", ZERO, OrderState.REJECTED),
        ("Inactive", StatusContext(reject_error=True), OrderState.REJECTED),
        ("Inactive", ZERO, OrderState.UNKNOWN),  # parked by IBKR: fail closed
        ("", ZERO, OrderState.UNKNOWN),
        ("SomethingNew", ZERO, OrderState.UNKNOWN),
    ],
)
def test_br006_oe005_status_mapping(status: str, ctx: StatusContext, expected: OrderState) -> None:
    assert map_status(status, ctx) is expected


def test_oe005_every_documented_status_is_mapped() -> None:
    documented = {
        "ApiPending",
        "PendingSubmit",
        "PreSubmitted",
        "Submitted",
        "ApiUpdate",
        "PendingCancel",
        "Cancelled",
        "ApiCancelled",
        "Filled",
        "Inactive",
        "ValidationError",
    }
    assert set(STATUS_TABLE) == documented


@pytest.mark.parametrize(
    ("ctx", "expected"),
    [
        (StatusContext(tif_lapsed=True), OrderState.EXPIRED),
        (StatusContext(tif_lapsed=True, cancel_requested=True), OrderState.CANCELED),
        (StatusContext(tif_lapsed=False), OrderState.CANCELED),
    ],
)
def test_ib024_expiry_vs_cancel(ctx: StatusContext, expected: OrderState) -> None:
    assert map_status("Cancelled", ctx) is expected
