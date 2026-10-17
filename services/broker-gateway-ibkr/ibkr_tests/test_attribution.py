"""Attribution IDs in orderRef: VP-001, VP-002, OE-002, IB-001, IB-004."""

from __future__ import annotations

import pytest
from tb_broker_ibkr.attribution import (
    ATTRIBUTION_MAX_LEN,
    AttributionError,
    format_attribution,
    parse_attribution,
    resolve_owner,
    validate_order_ref,
)
from tb_broker_ibkr.models import OwnerKind

from .conftest import ULID, cid


@pytest.mark.parametrize(
    "value",
    [
        cid(),
        cid("abcdef"),
        cid("abcd1234", attempt=9999),
        f"tb1-swing01-{ULID}-01",  # BR-003 allows leading zeros
        cid("manual"),
        f"tb1-swing01-{ULID}-12",
    ],
)
def test_br003_valid_attribution_ids(value: str) -> None:
    parsed = parse_attribution(value)
    assert parsed.intent_ulid == ULID
    assert len(value) <= ATTRIBUTION_MAX_LEN


@pytest.mark.parametrize(
    "value",
    [
        "",
        "manual",
        f"tb2-swing01-{ULID}-0",  # wrong version
        f"tb1-SWING01-{ULID}-0",  # uppercase short id
        f"tb1-abc-{ULID}-0",  # short id too short
        f"tb1-abcdefghi-{ULID}-0",  # short id too long
        f"tb1-swing01-{ULID[:-1]}-0",  # ULID too short
        f"tb1-swing01-{ULID[:-1]}I-0",  # I is not Crockford base32
        f"tb1-swing01-{ULID}-10000",  # attempt too large
        f"tb1-swing01-{ULID}--1",
        f"tb1-swing01-{ULID}",  # no attempt
        f" tb1-swing01-{ULID}-0",
    ],
)
def test_br003_oe002_malformed_attribution_rejected(value: str) -> None:
    with pytest.raises(AttributionError):
        parse_attribution(value)


def test_br003_max_length_is_44() -> None:
    longest = format_attribution("abcd1234", ULID, 9999)
    assert len(longest) == ATTRIBUTION_MAX_LEN == 44


def test_ib001_order_ref_limit_enforced() -> None:
    value = cid()
    validate_order_ref(value, max_len=len(value))
    with pytest.raises(AttributionError, match="limit"):
        validate_order_ref(value, max_len=len(value) - 1)


@pytest.mark.parametrize(
    ("order_ref", "owner", "short_id", "malformed"),
    [
        ("", OwnerKind.EXTERNAL, "", False),
        ("my manual note", OwnerKind.EXTERNAL, "", False),
        ("TradingView", OwnerKind.EXTERNAL, "", False),
        (cid("manual"), OwnerKind.PLATFORM_MANUAL, "", False),
        (cid("swing01"), OwnerKind.STRATEGY, "swing01", False),
        ("tb1-garbage", OwnerKind.EXTERNAL, "", True),
        (cid("swing01")[:20], OwnerKind.EXTERNAL, "", True),  # truncated by a broker
    ],
)
def test_vp001_ib004_owner_resolution(order_ref: str, owner: OwnerKind, short_id: str, malformed: bool) -> None:
    resolution = resolve_owner(order_ref)
    assert resolution.owner is owner
    assert resolution.strategy_short_id == short_id
    assert resolution.malformed is malformed


def test_with_attempt_keeps_strategy_and_intent() -> None:
    parsed = parse_attribution(cid(attempt=3))
    nxt = parse_attribution(parsed.with_attempt(4))
    assert (nxt.short_id, nxt.intent_ulid, nxt.attempt) == (parsed.short_id, parsed.intent_ulid, 4)
