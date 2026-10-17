"""Production :class:`~tb_broker_ibkr.port.IbPort` over ``ib_async`` (D18).

``ib_async`` parses TWS wire values into floats; this module converts every price and quantity to
``Decimal`` through ``str(float)`` (the shortest repr, which round-trips the wire's decimal text for
the magnitudes IBKR sends) and maps IBKR's "unset" sentinels to ``None`` (OE-007).
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from ib_async import IB, Contract, ExecutionFilter, Order, Trade
from ib_async.ib import StartupFetch

from tb_broker_ibkr.port import (
    CommissionFill,
    ExecFill,
    HistoricalRequest,
    OrderSnapshot,
    PlaceOrder,
    PortAccountValue,
    PortBar,
    PortConnectError,
    PortHandlers,
    PortPosition,
)

log = logging.getLogger("tb_broker_ibkr.ib_async_port")

UNSET_DOUBLE = sys.float_info.max
_STARTUP_FETCH = StartupFetch.POSITIONS | StartupFetch.ORDERS_OPEN | StartupFetch.ORDERS_COMPLETE


def dec(value: float | int | str | Decimal | None) -> Decimal:
    if value is None or value == "" or value == UNSET_DOUBLE:
        return Decimal(0)
    return Decimal(str(value))


def opt_dec(value: float | Decimal | None) -> Decimal | None:
    if value is None or value == UNSET_DOUBLE or value == 0.0:
        return None
    return Decimal(str(value))


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def snapshot_from_trade(trade: Trade, *, ref_echoed: bool) -> OrderSnapshot:
    order, status, contract = trade.order, trade.orderStatus, trade.contract
    remaining = status.remaining if status.remaining not in (None, UNSET_DOUBLE) else 0.0
    return OrderSnapshot(
        ib_order_id=int(order.orderId or 0),
        ib_client_id=int(order.clientId or 0),
        perm_id=int(order.permId or status.permId or 0),
        account=order.account or "",
        order_ref=order.orderRef or "",
        con_id=int(contract.conId or 0),
        symbol=contract.symbol or "",
        action=order.action or "",
        total_qty=dec(order.totalQuantity),
        order_type=order.orderType or "",
        tif=order.tif or "",
        status=status.status or "",
        filled=dec(status.filled),
        remaining=dec(remaining),
        avg_fill_price=opt_dec(status.avgFillPrice),
        lmt_price=opt_dec(order.lmtPrice),
        aux_price=opt_dec(order.auxPrice),
        outside_rth=bool(order.outsideRth),
        why_held=status.whyHeld or "",
        ref_echoed=ref_echoed,
    )


def exec_from_fill(fill: Any) -> ExecFill:
    ex, contract = fill.execution, fill.contract
    return ExecFill(
        exec_id=ex.execId,
        account=ex.acctNumber,
        perm_id=int(ex.permId or 0),
        ib_order_id=int(ex.orderId or 0),
        ib_client_id=int(ex.clientId or 0),
        order_ref=ex.orderRef or "",
        con_id=int(contract.conId or 0),
        symbol=contract.symbol or "",
        side=ex.side,
        shares=dec(ex.shares),
        price=dec(ex.price),
        cum_qty=dec(ex.cumQty),
        time=_utc(ex.time),
    )


def commission_from_report(report: Any) -> CommissionFill | None:
    if report is None or not report.execId:
        return None
    if report.commission in (None, UNSET_DOUBLE):
        return None
    return CommissionFill(exec_id=report.execId, commission=dec(report.commission), currency=report.currency or "")


def contract_for(order: PlaceOrder) -> Contract:
    return Contract(
        conId=order.con_id,
        exchange=order.exchange,
        primaryExchange=order.primary_exchange,
        currency=order.currency,
        symbol=order.symbol,
        secType="STK",
    )


def ib_order_for(order: PlaceOrder, base: Order | None = None) -> Order:
    ib_order = base or Order()
    ib_order.account = order.account
    ib_order.action = order.action
    ib_order.totalQuantity = float(order.total_qty)
    ib_order.orderType = order.order_type
    ib_order.tif = order.tif
    ib_order.orderRef = order.order_ref
    ib_order.outsideRth = order.outside_rth
    ib_order.lmtPrice = float(order.lmt_price) if order.lmt_price is not None else UNSET_DOUBLE
    ib_order.auxPrice = float(order.aux_price) if order.aux_price is not None else UNSET_DOUBLE
    ib_order.transmit = True
    return ib_order


class TradeRecorder(Protocol):
    """Receives raw gateway callbacks (``tb_broker_ibkr.fakes.replay.Recorder``)."""

    def trade(self, now: datetime, event: str, trade: Trade) -> None: ...

    def fill(self, now: datetime, fill: Any) -> None: ...

    def commission(self, now: datetime, report: Any) -> None: ...

    def error(self, now: datetime, req_id: int, code: int, message: str) -> None: ...


class IbAsyncPort:
    """One ``ib_async.IB`` connection with a fixed client ID."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        *,
        connect_timeout: float = 10.0,
        recorder: TradeRecorder | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self._timeout = connect_timeout
        self._recorder = recorder
        self._ib = IB()
        self._handlers = PortHandlers()
        self._trades: dict[int, Trade] = {}
        self._ib.disconnectedEvent += self._on_disconnected
        self._ib.errorEvent += self._on_error
        self._ib.openOrderEvent += self._on_open_order
        self._ib.orderStatusEvent += self._on_status
        self._ib.execDetailsEvent += self._on_exec
        self._ib.commissionReportEvent += self._on_commission

    # ---- event bridging ----------------------------------------------------------------------
    def set_handlers(self, handlers: PortHandlers) -> None:
        self._handlers = handlers

    def _on_disconnected(self) -> None:
        self._handlers.on_disconnected()

    def _on_error(self, req_id: int, code: int, message: str, _contract: object = None) -> None:
        if self._recorder is not None:
            self._recorder.error(datetime.now(UTC), int(req_id), int(code), str(message))
        self._handlers.on_error(int(req_id), int(code), str(message))

    def _on_open_order(self, trade: Trade) -> None:
        if self._recorder is not None:
            self._recorder.trade(datetime.now(UTC), "openOrder", trade)
        self._handlers.on_order(snapshot_from_trade(trade, ref_echoed=True))

    def _on_status(self, trade: Trade) -> None:
        if self._recorder is not None:
            self._recorder.trade(datetime.now(UTC), "orderStatus", trade)
        self._handlers.on_order(snapshot_from_trade(trade, ref_echoed=False))

    def _on_exec(self, _trade: Trade, fill: Any) -> None:
        if self._recorder is not None:
            self._recorder.fill(datetime.now(UTC), fill)
        self._handlers.on_execution(exec_from_fill(fill))

    def _on_commission(self, _trade: Trade, _fill: Any, report: Any) -> None:
        if self._recorder is not None:
            self._recorder.commission(datetime.now(UTC), report)
        commission = commission_from_report(report)
        if commission is not None:
            self._handlers.on_commission(commission)

    # ---- connection ----------------------------------------------------------------------------
    async def connect(self) -> list[str]:
        try:
            await self._ib.connectAsync(
                self.host, self.port, clientId=self.client_id, timeout=self._timeout, fetchFields=_STARTUP_FETCH
            )
        except Exception as exc:
            self._ib.disconnect()
            raise PortConnectError(f"IB Gateway API handshake failed: {type(exc).__name__}") from exc
        return list(self._ib.managedAccounts())

    def disconnect(self) -> None:
        self._ib.disconnect()

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    async def current_time(self) -> datetime:
        return _utc(await self._ib.reqCurrentTimeAsync())

    # ---- orders --------------------------------------------------------------------------------
    def place_order(self, order: PlaceOrder) -> int:
        trade = self._ib.placeOrder(contract_for(order), ib_order_for(order))
        self._trades[trade.order.orderId] = trade
        return int(trade.order.orderId)

    def modify_order(self, ib_order_id: int, order: PlaceOrder) -> None:
        trade = self._trades.get(ib_order_id) or self._find_trade(ib_order_id)
        if trade is None:
            raise LookupError(f"no trade for order id {ib_order_id}")
        self._ib.placeOrder(trade.contract, ib_order_for(order, trade.order))

    def cancel_order(self, ib_order_id: int) -> None:
        trade = self._trades.get(ib_order_id) or self._find_trade(ib_order_id)
        if trade is None:
            raise LookupError(f"no trade for order id {ib_order_id}")
        self._ib.cancelOrder(trade.order)

    def _find_trade(self, ib_order_id: int) -> Trade | None:
        for trade in self._ib.trades():
            if trade.order.orderId == ib_order_id and trade.order.clientId == self.client_id:
                return trade
        return None

    async def open_orders(self) -> list[OrderSnapshot]:
        trades = await self._ib.reqAllOpenOrdersAsync()
        return [snapshot_from_trade(t, ref_echoed=True) for t in trades]

    async def completed_orders(self) -> list[OrderSnapshot]:
        trades = await self._ib.reqCompletedOrdersAsync(False)
        return [snapshot_from_trade(t, ref_echoed=True) for t in trades]

    async def executions(self, since: datetime | None) -> list[tuple[ExecFill, CommissionFill | None]]:
        flt = ExecutionFilter()
        if since is not None:
            flt.time = since.astimezone(UTC).strftime("%Y%m%d-%H:%M:%S")
        fills = await self._ib.reqExecutionsAsync(flt)
        return [(exec_from_fill(f), commission_from_report(f.commissionReport)) for f in fills]

    async def positions(self) -> list[PortPosition]:
        out = []
        for p in await self._ib.reqPositionsAsync():
            out.append(
                PortPosition(
                    account=p.account,
                    con_id=int(p.contract.conId),
                    symbol=p.contract.symbol,
                    qty=dec(p.position),
                    avg_cost=dec(p.avgCost),
                )
            )
        return out

    async def account_values(self, account: str) -> list[PortAccountValue]:
        values = await self._ib.accountSummaryAsync(account)
        return [PortAccountValue(v.account, v.tag, v.value, v.currency) for v in values]

    async def historical_bars(self, request: HistoricalRequest) -> list[PortBar]:
        contract = Contract(conId=request.con_id, exchange=request.exchange, secType="STK")
        bars = await self._ib.reqHistoricalDataAsync(
            contract,
            endDateTime=request.end or "",
            durationStr=request.duration,
            barSizeSetting=request.bar_size,
            whatToShow=request.what_to_show,
            useRTH=request.use_rth,
            formatDate=2,
        )
        out = []
        for b in bars:
            ts = b.date if isinstance(b.date, datetime) else datetime.combine(b.date, datetime.min.time())
            out.append(
                PortBar(
                    time=_utc(ts),
                    open=dec(b.open),
                    high=dec(b.high),
                    low=dec(b.low),
                    close=dec(b.close),
                    volume=dec(b.volume),
                    wap=opt_dec(b.average),
                    count=int(b.barCount or 0),
                )
            )
        return out
