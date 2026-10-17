"""Domain model of the IBKR adapter.

These types mirror the section 04 ``BrokerGateway`` contract (``BrokerOrderRequest``,
``BrokerOrder``, ``ExecutionReport``, ``AccountSnapshot``, ``SessionStatus``, capabilities) so the
gRPC servicer is a thin codec. Money and quantity are ``Decimal`` everywhere (OE-007); timestamps
are UTC nanoseconds.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Environment(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class SessionState(StrEnum):
    """Per-connection health state machine (OE-004, IB-010)."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATED = "authenticated"
    SYNCING = "syncing"
    READY = "ready"
    DEGRADED = "degraded"
    # Deliberately not running: a live adapter without both live gates (BR-013, OE-010).
    STOPPED = "stopped"


class OrderState(StrEnum):
    """Normalized order state shared by every broker adapter (section 04, OE-005)."""

    PENDING_SUBMIT = "pending_submit"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PENDING_CANCEL = "pending_cancel"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REPLACED = "replaced"
    PENDING_REPLACE = "pending_replace"
    UNKNOWN = "unknown"


TERMINAL_STATES = frozenset(
    {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED, OrderState.REPLACED}
)


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    SELL_SHORT = "sell_short"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"
    OPG = "opg"
    CLS = "cls"


class OwnerKind(StrEnum):
    """Who owns an order according to its attribution ID (VP-001)."""

    STRATEGY = "strategy"
    PLATFORM_MANUAL = "platform_manual"
    EXTERNAL = "external"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Instrument(_Frozen):
    """What the adapter needs to route an ``instrument_id`` to IBKR (from the instrument master).

    ``min_tick`` applies to prices >= $1; sub-dollar US equity prices tick at $0.0001 (Reg NMS 612).
    """

    instrument_id: str
    con_id: int = Field(gt=0)
    symbol: str
    exchange: str = "SMART"
    primary_exchange: str = ""
    currency: str = "USD"
    min_tick: Decimal = Decimal("0.01")
    tradable: bool = True
    shortable: bool = True


class OrderRequest(_Frozen):
    """``BrokerOrderRequest`` from section 04."""

    client_order_id: str
    broker_account_id: str
    instrument_id: str
    side: Side
    qty: Decimal = Field(gt=0)
    order_type: OrderType
    tif: TimeInForce = TimeInForce.DAY
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    extended_hours: bool = False
    correlation_id: str = ""
    # Broker identifiers resolved by the caller from the instrument master (contract BrokerOrderRequest).
    instrument: Instrument | None = None
    # Ticker, used only when neither instrument_id nor a conId identifies the contract (IB-027).
    symbol: str = ""


class OrderChanges(_Frozen):
    """Fields a replace may change; unset fields keep their current value."""

    qty: Decimal | None = Field(default=None, gt=0)
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: TimeInForce | None = None


class SubmitAck(_Frozen):
    client_order_id: str
    broker_order_id: str
    accepted_at: int
    # True when the ack came from a lookup after a submit timeout (OE-003), not a fresh placement.
    recovered: bool = False


class CancelAck(_Frozen):
    client_order_id: str
    broker_order_id: str
    requested_at: int


class ReplaceAck(_Frozen):
    client_order_id: str
    new_client_order_id: str
    broker_order_id: str
    requested_at: int


class BrokerOrder(_Frozen):
    """An order as the broker reports it, including orders placed outside the platform."""

    broker_account_id: str
    client_order_id: str
    # IBKR permId as a decimal string: the durable join key (IB-003). Empty until IBKR assigns it.
    broker_order_id: str
    ib_order_id: int
    ib_client_id: int
    owner: OwnerKind
    strategy_short_id: str = ""
    instrument_id: str = ""
    con_id: int
    symbol: str
    side: Side
    qty: Decimal
    order_type: str
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tif: str
    state: OrderState
    raw_status: str
    filled_qty: Decimal = Decimal(0)
    remaining_qty: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    why_held: str = ""
    updated_at: int


class ExecutionReport(_Frozen):
    """One fill, joined with its commission report when available (IB-030)."""

    broker_account_id: str
    broker_exec_id: str
    client_order_id: str
    broker_order_id: str
    owner: OwnerKind
    strategy_short_id: str = ""
    instrument_id: str = ""
    con_id: int
    symbol: str
    side: Side
    qty: Decimal
    price: Decimal
    cum_qty: Decimal
    fees: Decimal | None = None
    fee_currency: str = ""
    # Exec ID this report corrects (same IBKR exec base ID, lower revision), if any (IB-032).
    corrects_exec_id: str = ""
    event_time: int
    ingested_at: int


class Position(_Frozen):
    con_id: int
    instrument_id: str = ""
    symbol: str
    qty: Decimal
    avg_cost: Decimal


class AccountSnapshot(_Frozen):
    broker_account_id: str
    currency: str = "USD"
    net_liquidation: Decimal | None = None
    cash: Decimal | None = None
    buying_power: Decimal | None = None
    excess_liquidity: Decimal | None = None
    maintenance_margin: Decimal | None = None
    # Negative excess liquidity: a margin deficit, which halts the account (RM-013, IB-041).
    margin_deficit: bool = False
    positions: tuple[Position, ...] = ()
    as_of: int


class Capabilities(_Frozen):
    order_types: tuple[OrderType, ...]
    tifs: tuple[TimeInForce, ...]
    supports_bracket: bool
    supports_oco: bool
    supports_oto: bool
    supports_replace: bool
    supports_trailing_stop: bool
    supports_fractional: bool
    supports_short: bool
    supports_extended_hours: bool
    max_client_order_id_len: int
    pdt_applies: bool
    is_margin: bool
    data_feeds: tuple[str, ...]


class BrokerAccount(_Frozen):
    broker_account_id: str
    broker: str = "ibkr"
    environment: Environment
    capabilities: Capabilities


class SessionStatus(_Frozen):
    broker_account_id: str
    state: SessionState
    connected: bool
    authenticated: bool
    last_heartbeat: int
    degraded_reasons: tuple[str, ...] = ()
    reauth_required: bool = False
    since: int


class Bar(_Frozen):
    con_id: int
    ts_open: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    vwap: Decimal | None = None
    trade_count: int = 0


class AdapterError(Exception):
    """Base class for typed adapter errors surfaced to callers."""

    code = "adapter_error"


class BrokerUnavailableError(AdapterError):
    """The account's session is not ``ready`` (OE-004)."""

    code = "broker_unavailable"

    def __init__(self, broker_account_id: str, state: SessionState, reasons: tuple[str, ...] = ()) -> None:
        self.broker_account_id = broker_account_id
        self.state = state
        self.reasons = reasons
        detail = f" ({', '.join(reasons)})" if reasons else ""
        super().__init__(f"{broker_account_id} is {state.value}{detail}")


class InvalidOrderError(AdapterError):
    """The request is malformed or not allowed for this adapter (rejected before reaching IBKR).

    ``reason`` maps to the contract's ``RejectCode`` (BR-005): ``invalid_order``,
    ``invalid_attribution``, ``unknown_instrument``, ``not_shortable``, ``not_tradable``.
    """

    code = "invalid_order"

    def __init__(self, message: str, reason: str = "invalid_order") -> None:
        self.reason = reason
        super().__init__(message)


class AccountNotFoundError(AdapterError):
    """The ``broker_account_id`` is not served by this adapter (gRPC NOT_FOUND)."""

    code = "account_not_found"


class OrderRejectedError(AdapterError):
    """IBKR rejected the order."""

    code = "order_rejected"

    def __init__(self, broker_code: int, message: str) -> None:
        self.broker_code = broker_code
        super().__init__(f"IBKR {broker_code}: {message}")


class OrderNotFoundError(AdapterError):
    code = "order_not_found"


class OrderAlreadyFinalError(InvalidOrderError):
    """Cancel/replace of an order that is already filled, canceled, rejected, expired, or replaced."""

    code = "order_already_final"


class SubmitOutcomeUnknownError(AdapterError):
    """A submit timed out and the lookup could not confirm the order either way (OE-003).

    The caller MUST treat the order as ``unknown`` and MUST NOT retry with the same attempt;
    reconciliation resolves it.
    """

    code = "submit_outcome_unknown"
