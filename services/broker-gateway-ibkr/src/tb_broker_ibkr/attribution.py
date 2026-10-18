"""Attribution IDs in IBKR ``orderRef`` (VP-001, VP-002, OE-002; IB-001, IB-004).

Format (BR-003): ``^tb1-[a-z0-9]{6,8}-[0-9A-HJKMNP-TV-Z]{26}-[0-9]{1,4}$``; the short ID ``manual``
is reserved for platform UI orders. The longest valid ID is ``ATTRIBUTION_MAX_LEN`` (44)
characters; the IBKR ``orderRef`` limit is configured separately (``TB_IBKR_ORDER_REF_MAX_LEN``)
until spike V1 records the broker's real limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tb_broker_ibkr.models import OwnerKind

PREFIX = "tb1-"
MANUAL_SHORT_ID = "manual"
ATTRIBUTION_MAX_LEN = len("tb1-") + 8 + 1 + 26 + 1 + 4

_ATTRIBUTION_RE = re.compile(r"^tb1-(?P<sid>[a-z0-9]{6,8})-(?P<ulid>[0-9A-HJKMNP-TV-Z]{26})-(?P<attempt>[0-9]{1,4})$")


@dataclass(frozen=True, slots=True)
class Attribution:
    short_id: str
    intent_ulid: str
    attempt: int

    @property
    def owner(self) -> OwnerKind:
        return OwnerKind.PLATFORM_MANUAL if self.short_id == MANUAL_SHORT_ID else OwnerKind.STRATEGY

    def with_attempt(self, attempt: int) -> str:
        return format_attribution(self.short_id, self.intent_ulid, attempt)


@dataclass(frozen=True, slots=True)
class OwnerResolution:
    owner: OwnerKind
    strategy_short_id: str = ""
    # A ``tb1-`` prefix that does not parse: assigned to manual and alerted, never to a strategy.
    malformed: bool = False


class AttributionError(ValueError):
    """The attribution ID is missing, malformed, or too long for the broker field."""


def parse_attribution(value: str) -> Attribution:
    """Parse a well-formed attribution ID or raise :class:`AttributionError`."""
    m = _ATTRIBUTION_RE.match(value or "")
    if not m:
        raise AttributionError(f"malformed attribution ID {value!r}")
    return Attribution(short_id=m["sid"], intent_ulid=m["ulid"], attempt=int(m["attempt"]))


def format_attribution(short_id: str, intent_ulid: str, attempt: int) -> str:
    value = f"{PREFIX}{short_id}-{intent_ulid}-{attempt}"
    parse_attribution(value)
    return value


def validate_order_ref(value: str, max_len: int) -> Attribution:
    """IB-001: an outbound order's ``orderRef`` must be a valid attribution ID within ``max_len``."""
    attribution = parse_attribution(value)
    if len(value) > max_len:
        raise AttributionError(f"attribution ID is {len(value)} chars; IBKR orderRef limit is {max_len}")
    return attribution


def resolve_owner(order_ref: str) -> OwnerResolution:
    """IB-004 / VP-001: who owns an order or execution given the ``orderRef`` IBKR reports."""
    if not order_ref or not order_ref.startswith(PREFIX):
        return OwnerResolution(owner=OwnerKind.EXTERNAL)
    try:
        attribution = parse_attribution(order_ref)
    except AttributionError:
        return OwnerResolution(owner=OwnerKind.EXTERNAL, malformed=True)
    if attribution.owner is OwnerKind.PLATFORM_MANUAL:
        return OwnerResolution(owner=OwnerKind.PLATFORM_MANUAL)
    return OwnerResolution(owner=OwnerKind.STRATEGY, strategy_short_id=attribution.short_id)
