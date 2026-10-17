"""IBKR TWS API error and status codes by category (IB-060).

Codes from IBKR's message-code reference (https://interactivebrokers.github.io/tws-api/message_codes.html)
and the TWS API error documentation. Unlisted codes fall back by range: 2100-2199 are warnings,
everything else is ``UNCLASSIFIED`` (logged at warning, no state change). The table is mirrored in
``docs/business-logic/brokers-ibkr.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    CONNECTIVITY = "connectivity"
    FARM_STATUS = "farm_status"
    PACING = "pacing"
    MARKET_DATA = "market_data"
    ORDER_REJECT = "order_reject"
    ORDER_CANCEL = "order_cancel"
    ORDER_WARNING = "order_warning"
    CONTRACT = "contract"
    SESSION = "session"
    INFORMATIONAL = "informational"
    UNCLASSIFIED = "unclassified"


class Action(StrEnum):
    """What the adapter does on receipt."""

    DEGRADE = "degrade"  # mark the session degraded, block orders
    RESYNC = "resync"  # connectivity restored: full resync before ready (IB-012)
    RESYNC_RESUBSCRIBE = "resync_resubscribe"  # restored with data loss: also resubscribe market data
    RECONNECT = "reconnect"  # the socket is unusable: drop and reconnect with backoff
    REJECT_ORDER = "reject_order"  # the order is rejected (state REJECTED, typed error to caller)
    CANCEL_NOTICE = "cancel_notice"  # informational cancel confirmation; state comes from orderStatus
    PACE = "pace"  # back off the pacing scheduler (IB-050)
    SUBSCRIPTION_ERROR = "subscription_error"  # surface a clear market-data subscription error (IB-051)
    LOG = "log"


@dataclass(frozen=True, slots=True)
class CodeInfo:
    code: int
    category: Category
    action: Action
    meaning: str


_TABLE: tuple[CodeInfo, ...] = (
    # Connectivity between API client, Gateway, and IBKR servers.
    CodeInfo(1100, Category.CONNECTIVITY, Action.DEGRADE, "Connectivity between IB and the Gateway has been lost"),
    CodeInfo(1101, Category.CONNECTIVITY, Action.RESYNC_RESUBSCRIBE, "Connectivity restored, data lost"),
    CodeInfo(1102, Category.CONNECTIVITY, Action.RESYNC, "Connectivity restored, data maintained"),
    CodeInfo(1300, Category.CONNECTIVITY, Action.RECONNECT, "Socket port has been reset; reconnect"),
    CodeInfo(2110, Category.CONNECTIVITY, Action.DEGRADE, "Connectivity between Gateway and server is broken"),
    CodeInfo(502, Category.CONNECTIVITY, Action.RECONNECT, "Could not connect to the Gateway"),
    CodeInfo(504, Category.CONNECTIVITY, Action.RECONNECT, "Not connected"),
    CodeInfo(507, Category.CONNECTIVITY, Action.RECONNECT, "Bad message length / socket error"),
    CodeInfo(509, Category.CONNECTIVITY, Action.RECONNECT, "Exception while reading the socket"),
    # Session conflicts.
    CodeInfo(10197, Category.SESSION, Action.DEGRADE, "No market data during a competing live session"),
    # Data-farm status (informational; real loss is signalled by 1100/2110).
    CodeInfo(2103, Category.FARM_STATUS, Action.LOG, "Market data farm connection is broken"),
    CodeInfo(2104, Category.FARM_STATUS, Action.LOG, "Market data farm connection is OK"),
    CodeInfo(2105, Category.FARM_STATUS, Action.LOG, "Historical data farm connection is broken"),
    CodeInfo(2106, Category.FARM_STATUS, Action.LOG, "Historical data farm connection is OK"),
    CodeInfo(2107, Category.FARM_STATUS, Action.LOG, "Historical data farm connection is inactive"),
    CodeInfo(2108, Category.FARM_STATUS, Action.LOG, "Market data farm connection is inactive"),
    CodeInfo(2119, Category.FARM_STATUS, Action.LOG, "Market data farm is connecting"),
    CodeInfo(2157, Category.FARM_STATUS, Action.LOG, "Sec-def data farm connection is broken"),
    CodeInfo(2158, Category.FARM_STATUS, Action.LOG, "Sec-def data farm connection is OK"),
    # Pacing.
    CodeInfo(100, Category.PACING, Action.PACE, "Max rate of messages per second exceeded"),
    CodeInfo(162, Category.PACING, Action.PACE, "Historical market data service error (pacing violation)"),
    CodeInfo(420, Category.PACING, Action.PACE, "Invalid real-time query / pacing violation"),
    CodeInfo(366, Category.PACING, Action.LOG, "No historical data query found for ticker id"),
    # Market-data subscriptions and line limits.
    CodeInfo(101, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR, "Max number of tickers (market data lines)"),
    CodeInfo(354, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR, "Requested market data is not subscribed"),
    CodeInfo(10089, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR, "Additional subscription required"),
    CodeInfo(10090, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR, "Part of the data requires a subscription"),
    CodeInfo(10167, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR, "Displaying delayed market data"),
    CodeInfo(10168, Category.MARKET_DATA, Action.SUBSCRIPTION_ERROR, "Delayed market data is not enabled"),
    # Contract definition.
    CodeInfo(200, Category.CONTRACT, Action.REJECT_ORDER, "No security definition found"),
    CodeInfo(321, Category.CONTRACT, Action.REJECT_ORDER, "Error validating request"),
    # Order rejects.
    CodeInfo(103, Category.ORDER_REJECT, Action.REJECT_ORDER, "Duplicate order id"),
    CodeInfo(104, Category.ORDER_REJECT, Action.REJECT_ORDER, "Cannot modify a filled order"),
    CodeInfo(105, Category.ORDER_REJECT, Action.REJECT_ORDER, "Order modification does not match the original"),
    CodeInfo(106, Category.ORDER_REJECT, Action.REJECT_ORDER, "Cannot transmit order id"),
    CodeInfo(107, Category.ORDER_REJECT, Action.REJECT_ORDER, "Cannot transmit incomplete order"),
    CodeInfo(109, Category.ORDER_REJECT, Action.REJECT_ORDER, "Price is out of the price range"),
    CodeInfo(110, Category.ORDER_REJECT, Action.REJECT_ORDER, "Price does not conform to the minimum tick"),
    CodeInfo(116, Category.ORDER_REJECT, Action.REJECT_ORDER, "Exchange not supported for this security"),
    CodeInfo(117, Category.ORDER_REJECT, Action.REJECT_ORDER, "Security not supported"),
    CodeInfo(135, Category.ORDER_REJECT, Action.REJECT_ORDER, "Cannot find order with this id"),
    CodeInfo(161, Category.ORDER_REJECT, Action.REJECT_ORDER, "Cancel attempted when the order is not cancellable"),
    CodeInfo(201, Category.ORDER_REJECT, Action.REJECT_ORDER, "Order rejected"),
    CodeInfo(203, Category.ORDER_REJECT, Action.REJECT_ORDER, "Security is not available or allowed for account"),
    CodeInfo(388, Category.ORDER_REJECT, Action.REJECT_ORDER, "Order size is smaller than the minimum"),
    CodeInfo(404, Category.ORDER_REJECT, Action.REJECT_ORDER, "Shares are not available for a short sale"),
    CodeInfo(434, Category.ORDER_REJECT, Action.REJECT_ORDER, "Order size cannot be zero"),
    CodeInfo(10147, Category.ORDER_REJECT, Action.REJECT_ORDER, "Order id to cancel was not found"),
    CodeInfo(10148, Category.ORDER_REJECT, Action.REJECT_ORDER, "Order cannot be cancelled in its current state"),
    # Cancel confirmations and order warnings.
    CodeInfo(202, Category.ORDER_CANCEL, Action.CANCEL_NOTICE, "Order cancelled"),
    CodeInfo(399, Category.ORDER_WARNING, Action.LOG, "Order message (e.g. held until market open)"),
    CodeInfo(2109, Category.ORDER_WARNING, Action.LOG, "Outside-RTH attribute ignored"),
    CodeInfo(2137, Category.ORDER_WARNING, Action.LOG, "Order would cross a resting order of the account"),
    CodeInfo(2161, Category.ORDER_WARNING, Action.LOG, "Order message (warning)"),
    # Informational.
    CodeInfo(2100, Category.INFORMATIONAL, Action.LOG, "Account data subscription replaced by another client"),
)

CODE_TABLE: dict[int, CodeInfo] = {c.code: c for c in _TABLE}


def classify(code: int) -> CodeInfo:
    """Return the category and action for an IBKR error/status code (IB-060)."""
    info = CODE_TABLE.get(code)
    if info is not None:
        return info
    if 2100 <= code <= 2199:
        return CodeInfo(code, Category.INFORMATIONAL, Action.LOG, "Warning")
    return CodeInfo(code, Category.UNCLASSIFIED, Action.LOG, "Unclassified")


def is_pacing_message(code: int, message: str) -> bool:
    """162 is reused for non-pacing historical errors; only the pacing text triggers backoff."""
    if code != 162:
        return classify(code).action is Action.PACE
    return "pacing violation" in message.lower()
