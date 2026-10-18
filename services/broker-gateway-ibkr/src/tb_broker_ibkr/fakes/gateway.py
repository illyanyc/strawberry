"""In-process fake IB Gateway implementing the :class:`IbPort` seam.

Deterministic and clock-driven: every delay uses the injected :class:`Clock`, so tests run on
virtual time. One :class:`FakeGateway` holds the broker-side state (accounts, orders, executions,
positions); each :class:`FakePort` is one API client connection with its own client ID.
"""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from tb_broker_ibkr.clock import Clock
from tb_broker_ibkr.port import (
    CommissionFill,
    ExecFill,
    HistoricalRequest,
    OrderSnapshot,
    PlaceOrder,
    PortAccountValue,
    PortBar,
    PortConnectError,
    PortContract,
    PortHandlers,
    PortPosition,
)

TERMINAL = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}


class GatewayState(StrEnum):
    UP = "up"
    DOWN = "down"  # container stopped or restarting: connection refused
    AWAITING_LOGIN = "awaiting_login"  # IBC waiting for 2FA approval: API port closed


@dataclass
class FakeOrder:
    perm_id: int
    ib_order_id: int
    ib_client_id: int
    account: str
    order_ref: str
    con_id: int
    symbol: str
    action: str
    total_qty: Decimal
    order_type: str
    tif: str
    lmt_price: Decimal | None = None
    aux_price: Decimal | None = None
    outside_rth: bool = False
    status: str = "PreSubmitted"
    filled: Decimal = Decimal(0)
    avg_fill_price: Decimal | None = None
    fills: list[str] = field(default_factory=list)

    def snapshot(self) -> OrderSnapshot:
        return OrderSnapshot(
            ib_order_id=self.ib_order_id,
            ib_client_id=self.ib_client_id,
            perm_id=self.perm_id,
            account=self.account,
            order_ref=self.order_ref,
            con_id=self.con_id,
            symbol=self.symbol,
            action=self.action,
            total_qty=self.total_qty,
            order_type=self.order_type,
            tif=self.tif,
            status=self.status,
            filled=self.filled,
            remaining=self.total_qty - self.filled,
            avg_fill_price=self.avg_fill_price,
            lmt_price=self.lmt_price,
            aux_price=self.aux_price,
            outside_rth=self.outside_rth,
        )


class FakeGateway:
    """Broker-side state and fault injection."""

    def __init__(self, clock: Clock, managed_accounts: list[str] | None = None) -> None:
        self.clock = clock
        self.managed_accounts = managed_accounts or ["DU0000001"]
        self.state = GatewayState.UP
        self.orders: dict[int, FakeOrder] = {}
        self.executions: list[tuple[ExecFill, CommissionFill | None]] = []
        self.positions: dict[tuple[str, int], tuple[str, Decimal, Decimal]] = {}
        self.account_values: dict[str, dict[str, str]] = {}
        self.bars: list[PortBar] = []
        self.contracts: list[PortContract] = [
            PortContract(265598, "AAPL", "NASDAQ", "USD", Decimal("0.01"), "APPLE INC"),
            PortContract(756733, "SPY", "ARCA", "USD", Decimal("0.01"), "SPDR S&P 500 ETF TRUST"),
        ]
        self.historical_requests: list[tuple[datetime, HistoricalRequest]] = []
        self.ports: list[FakePort] = []
        self.placements: list[PlaceOrder] = []
        self.modifications: list[tuple[int, PlaceOrder]] = []
        self.cancels: list[int] = []
        self.connect_attempts = 0
        # Fault injection.
        self.ack_delay: float = 0.0
        self.drop_next_order = False
        self.reject_next: tuple[int, str] | None = None
        self.reject_next_modify: tuple[int, str] | None = None
        self.order_ref_limit: int | None = None
        self.hang_heartbeat = False
        self._perm = itertools.count(1_000_001)
        self._exec = itertools.count(1)
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- connections -------------------------------------------------------------------------
    def port(self, client_id: int) -> FakePort:
        port = FakePort(self, client_id)
        self.ports.append(port)
        return port

    def connected_ports(self) -> list[FakePort]:
        return [p for p in self.ports if p.connected]

    def go_down(self, state: GatewayState = GatewayState.DOWN) -> None:
        """Container stop/restart or IBC logout: drop every API connection."""
        self.state = state
        for p in self.connected_ports():
            p.connected = False
            p.handlers.on_disconnected()

    def come_up(self) -> None:
        self.state = GatewayState.UP

    def require_login(self) -> None:
        self.go_down(GatewayState.AWAITING_LOGIN)

    def approve_2fa(self) -> None:
        if self.state is GatewayState.AWAITING_LOGIN:
            self.state = GatewayState.UP

    def emit_error(self, code: int, message: str = "", req_id: int = -1) -> None:
        for p in self.connected_ports():
            p.handlers.on_error(req_id, code, message)

    # ---- orders ------------------------------------------------------------------------------
    def _spawn(self, coro: object) -> None:
        task = asyncio.ensure_future(coro)  # type: ignore[call-overload]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _broadcast_order(self, order: FakeOrder) -> None:
        for p in self.connected_ports():
            p.handlers.on_order(order.snapshot())

    def _place(self, client_id: int, ib_order_id: int, req: PlaceOrder) -> None:
        self.placements.append(req)
        if self.drop_next_order:
            self.drop_next_order = False
            return
        if self.reject_next is not None:
            code, message = self.reject_next
            self.reject_next = None
            self._spawn(self._deliver_reject(client_id, ib_order_id, code, message))
            return
        ref = req.order_ref if self.order_ref_limit is None else req.order_ref[: self.order_ref_limit]
        order = FakeOrder(
            perm_id=next(self._perm),
            ib_order_id=ib_order_id,
            ib_client_id=client_id,
            account=req.account,
            order_ref=ref,
            con_id=req.con_id,
            symbol=req.symbol,
            action=req.action,
            total_qty=req.total_qty,
            order_type=req.order_type,
            tif=req.tif,
            lmt_price=req.lmt_price,
            aux_price=req.aux_price,
            outside_rth=req.outside_rth,
        )
        self.orders[order.perm_id] = order
        self._spawn(self._deliver_ack(order))

    async def _deliver_ack(self, order: FakeOrder) -> None:
        if self.ack_delay:
            await self.clock.sleep(self.ack_delay)
        order.status = "Submitted"
        self._broadcast_order(order)

    async def _deliver_reject(self, client_id: int, ib_order_id: int, code: int, message: str) -> None:
        await asyncio.sleep(0)
        for p in self.connected_ports():
            if p.client_id == client_id:
                p.handlers.on_error(ib_order_id, code, message)

    def _modify(self, client_id: int, ib_order_id: int, req: PlaceOrder) -> None:
        self.modifications.append((ib_order_id, req))
        order = self._find(client_id, ib_order_id)
        if self.reject_next_modify is not None:
            code, message = self.reject_next_modify
            self.reject_next_modify = None
            self._spawn(self._deliver_reject(client_id, ib_order_id, code, message))
            return
        if order is None:
            return
        order.total_qty = req.total_qty
        order.lmt_price = req.lmt_price
        order.aux_price = req.aux_price
        order.order_ref = req.order_ref if self.order_ref_limit is None else req.order_ref[: self.order_ref_limit]
        self._broadcast_order(order)

    def _cancel(self, client_id: int, ib_order_id: int) -> None:
        self.cancels.append(ib_order_id)
        order = self._find(client_id, ib_order_id)
        if order is None or order.status in TERMINAL:
            return
        order.status = "Cancelled"
        self._broadcast_order(order)

    def _find(self, client_id: int, ib_order_id: int) -> FakeOrder | None:
        for order in self.orders.values():
            if order.ib_client_id == client_id and order.ib_order_id == ib_order_id:
                return order
        return None

    def order_by_ref(self, order_ref: str) -> FakeOrder:
        for o in self.orders.values():
            if o.order_ref == order_ref:
                return o
        raise KeyError(order_ref)

    def place_external_order(
        self, account: str, con_id: int, symbol: str, action: str, qty: Decimal, order_ref: str = ""
    ) -> FakeOrder:
        """An order placed in TWS / IBKR Mobile by the owner (client 0, no platform orderRef)."""
        order = FakeOrder(
            perm_id=next(self._perm),
            ib_order_id=0,
            ib_client_id=0,
            account=account,
            order_ref=order_ref,
            con_id=con_id,
            symbol=symbol,
            action=action,
            total_qty=qty,
            order_type="MKT",
            tif="DAY",
            status="Submitted",
        )
        self.orders[order.perm_id] = order
        self._broadcast_order(order)
        return order

    def set_status(self, perm_id: int, status: str) -> None:
        order = self.orders[perm_id]
        order.status = status
        self._broadcast_order(order)

    def fill(
        self,
        perm_id: int,
        qty: Decimal,
        price: Decimal,
        *,
        commission: Decimal | None = Decimal("1.00"),
        commission_first: bool = False,
        duplicate: bool = False,
        exec_id: str | None = None,
        status_before_exec: bool = False,
    ) -> ExecFill:
        """Execute ``qty`` at ``price``; deliver execDetails, commissionReport, and orderStatus."""
        order = self.orders[perm_id]
        order.filled += qty
        prev_value = (order.avg_fill_price or Decimal(0)) * (order.filled - qty)
        order.avg_fill_price = (prev_value + qty * price) / order.filled
        order.status = "Filled" if order.filled >= order.total_qty else "Submitted"
        fill = ExecFill(
            exec_id=exec_id or f"0000e0d5.{next(self._exec):08x}.01.01",
            account=order.account,
            perm_id=order.perm_id,
            ib_order_id=order.ib_order_id,
            ib_client_id=order.ib_client_id,
            order_ref=order.order_ref,
            con_id=order.con_id,
            symbol=order.symbol,
            side="BOT" if order.action == "BUY" else "SLD",
            shares=qty,
            price=price,
            cum_qty=order.filled,
            time=self.clock.now(),
        )
        report = CommissionFill(fill.exec_id, commission, "USD") if commission is not None else None
        self.executions.append((fill, report))
        order.fills.append(fill.exec_id)
        key = (order.account, order.con_id)
        _, pos, cost = self.positions.get(key, (order.symbol, Decimal(0), Decimal(0)))
        signed = qty if order.action == "BUY" else -qty
        self.positions[key] = (order.symbol, pos + signed, price if pos == 0 else cost)
        for p in self.connected_ports():
            if status_before_exec:
                p.handlers.on_order(order.snapshot())
            if commission_first and report is not None:
                p.handlers.on_commission(report)
            p.handlers.on_execution(fill)
            if duplicate:
                p.handlers.on_execution(fill)
            if not commission_first and report is not None:
                p.handlers.on_commission(report)
            if not status_before_exec:
                p.handlers.on_order(order.snapshot())
        return fill


class FakePort:
    """One API connection to a :class:`FakeGateway`."""

    def __init__(self, gateway: FakeGateway, client_id: int) -> None:
        self.gateway = gateway
        self.client_id = client_id
        self.connected = False
        self.handlers = PortHandlers()
        self._next_order_id = itertools.count(1)

    def set_handlers(self, handlers: PortHandlers) -> None:
        self.handlers = handlers

    async def connect(self) -> list[str]:
        self.gateway.connect_attempts += 1
        await asyncio.sleep(0)
        if self.gateway.state is not GatewayState.UP:
            raise PortConnectError(f"gateway {self.gateway.state.value}")
        self.connected = True
        return list(self.gateway.managed_accounts)

    def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    def _check(self) -> None:
        if not self.connected:
            raise ConnectionError("not connected")

    async def current_time(self) -> datetime:
        self._check()
        if self.gateway.hang_heartbeat:
            await asyncio.get_running_loop().create_future()
        return self.gateway.clock.now()

    def place_order(self, order: PlaceOrder) -> int:
        self._check()
        ib_order_id = next(self._next_order_id)
        self.gateway._place(self.client_id, ib_order_id, order)
        return ib_order_id

    def modify_order(self, ib_order_id: int, order: PlaceOrder) -> None:
        self._check()
        self.gateway._modify(self.client_id, ib_order_id, order)

    def cancel_order(self, ib_order_id: int) -> None:
        self._check()
        self.gateway._cancel(self.client_id, ib_order_id)

    async def open_orders(self) -> list[OrderSnapshot]:
        self._check()
        return [o.snapshot() for o in self.gateway.orders.values() if o.status not in TERMINAL]

    async def completed_orders(self) -> list[OrderSnapshot]:
        self._check()
        return [o.snapshot() for o in self.gateway.orders.values() if o.status in TERMINAL]

    async def executions(self, since: datetime | None) -> list[tuple[ExecFill, CommissionFill | None]]:
        self._check()
        return [(f, c) for f, c in self.gateway.executions if since is None or f.time >= since]

    async def positions(self) -> list[PortPosition]:
        self._check()
        return [
            PortPosition(account=a, con_id=c, symbol=s, qty=q, avg_cost=cost)
            for (a, c), (s, q, cost) in self.gateway.positions.items()
        ]

    async def account_values(self, account: str) -> list[PortAccountValue]:
        self._check()
        values = self.gateway.account_values.get(account, {"NetLiquidation": "100000", "ExcessLiquidity": "50000"})
        return [PortAccountValue(account, tag, value, "USD") for tag, value in values.items()]

    async def contract_details(self, *, symbol: str = "", con_id: int = 0) -> list[PortContract]:
        self._check()
        return [c for c in self.gateway.contracts if (con_id and c.con_id == con_id) or (symbol and c.symbol == symbol)]

    async def historical_bars(self, request: HistoricalRequest) -> list[PortBar]:
        self._check()
        self.gateway.historical_requests.append((self.gateway.clock.now(), request))
        return list(self.gateway.bars)


def with_status(snapshot: OrderSnapshot, status: str, **changes: object) -> OrderSnapshot:
    return replace(snapshot, status=status, **changes)  # type: ignore[arg-type]
