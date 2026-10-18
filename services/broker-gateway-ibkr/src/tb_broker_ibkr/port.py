"""The adapter's port to one IB Gateway API connection.

:class:`IbPort` is the seam between platform logic and ``ib_async``: the production implementation
is :class:`tb_broker_ibkr.ib_async_port.IbAsyncPort`; tests use the fakes in
:mod:`tb_broker_ibkr.fakes`. Port types are close to the TWS API (IBKR status strings, ``BOT``/
``SLD`` sides) but already carry ``Decimal`` quantities and prices and UTC datetimes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    """One order as sent to IBKR. ``account`` is always explicit (IB-005)."""

    account: str
    con_id: int
    exchange: str
    currency: str
    action: str  # BUY | SELL
    total_qty: Decimal
    order_type: str  # MKT | LMT | STP | STP LMT | MOC | LOC
    tif: str  # DAY | GTC | IOC | FOK | OPG
    order_ref: str
    lmt_price: Decimal | None = None
    aux_price: Decimal | None = None
    outside_rth: bool = False
    primary_exchange: str = ""
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    """``openOrder`` + ``orderStatus`` for one order, as IBKR reports it."""

    ib_order_id: int
    ib_client_id: int
    perm_id: int
    account: str
    order_ref: str
    con_id: int
    symbol: str
    action: str
    total_qty: Decimal
    order_type: str
    tif: str
    status: str
    filled: Decimal
    remaining: Decimal
    avg_fill_price: Decimal | None = None
    lmt_price: Decimal | None = None
    aux_price: Decimal | None = None
    outside_rth: bool = False
    why_held: str = ""
    # True when order_ref came from an IBKR message (openOrder / completedOrder), not the local
    # copy of what was sent; only echoed refs count for the IB-002 check.
    ref_echoed: bool = True


@dataclass(frozen=True, slots=True)
class ExecFill:
    """``execDetails`` for one fill."""

    exec_id: str
    account: str
    perm_id: int
    ib_order_id: int
    ib_client_id: int
    order_ref: str
    con_id: int
    symbol: str
    side: str  # BOT | SLD
    shares: Decimal
    price: Decimal
    cum_qty: Decimal
    time: datetime


@dataclass(frozen=True, slots=True)
class CommissionFill:
    """``commissionReport`` for one fill."""

    exec_id: str
    commission: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class PortPosition:
    account: str
    con_id: int
    symbol: str
    qty: Decimal
    avg_cost: Decimal


@dataclass(frozen=True, slots=True)
class PortAccountValue:
    account: str
    tag: str
    value: str
    currency: str


@dataclass(frozen=True, slots=True)
class PortContract:
    """``contractDetails`` for one US stock/ETF (SMART-routed, USD)."""

    con_id: int
    symbol: str
    primary_exchange: str
    currency: str
    min_tick: Decimal
    long_name: str = ""


@dataclass(frozen=True, slots=True)
class HistoricalRequest:
    con_id: int
    exchange: str
    end: datetime | None
    duration: str  # e.g. "1 D"
    bar_size: str  # e.g. "1 min"
    what_to_show: str = "TRADES"
    use_rth: bool = True


@dataclass(frozen=True, slots=True)
class PortBar:
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    wap: Decimal | None
    count: int


def _noop(*_args: object) -> None:
    return None


@dataclass
class PortHandlers:
    """Callbacks the port invokes from the event loop as gateway messages arrive."""

    on_disconnected: Callable[[], None] = _noop
    # req_id is the IBKR order id for order-scoped errors and -1 for session-scoped ones.
    on_error: Callable[[int, int, str], None] = _noop
    on_order: Callable[[OrderSnapshot], None] = _noop
    on_execution: Callable[[ExecFill], None] = _noop
    on_commission: Callable[[CommissionFill], None] = _noop
    extra: dict[str, Callable[..., None]] = field(default_factory=dict)


class PortConnectError(ConnectionError):
    """The gateway refused or dropped the API handshake (down, restarting, or awaiting login/2FA)."""


class IbPort(Protocol):
    """One API connection (one client ID) to IB Gateway."""

    client_id: int

    def set_handlers(self, handlers: PortHandlers) -> None: ...

    async def connect(self) -> list[str]:
        """Open the API session; return the managed account codes. Raises :class:`PortConnectError`."""
        ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    async def current_time(self) -> datetime:
        """Heartbeat: the gateway's clock (``reqCurrentTime``)."""
        ...

    def place_order(self, order: PlaceOrder) -> int:
        """Send a new order; return its IBKR order id. Status arrives through ``on_order``."""
        ...

    def modify_order(self, ib_order_id: int, order: PlaceOrder) -> None: ...

    def cancel_order(self, ib_order_id: int) -> None: ...

    async def open_orders(self) -> list[OrderSnapshot]:
        """All open orders of every client and of TWS/mobile (``reqAllOpenOrders``)."""
        ...

    async def completed_orders(self) -> list[OrderSnapshot]:
        """Completed orders including those placed outside the API (``reqCompletedOrders(False)``)."""
        ...

    async def executions(self, since: datetime | None) -> list[tuple[ExecFill, CommissionFill | None]]:
        """Executions since ``since`` (IBKR returns the current day at most) with commissions if known."""
        ...

    async def positions(self) -> list[PortPosition]: ...

    async def account_values(self, account: str) -> list[PortAccountValue]: ...

    async def historical_bars(self, request: HistoricalRequest) -> list[PortBar]: ...

    async def contract_details(self, *, symbol: str = "", con_id: int = 0) -> list[PortContract]:
        """US stock/ETF contracts matching ``symbol`` (SMART, USD) or ``con_id``."""
        ...
