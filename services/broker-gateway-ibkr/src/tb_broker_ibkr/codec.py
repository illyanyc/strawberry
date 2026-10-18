"""Domain model <-> ``tb.broker.v1`` / ``tb.orders.v1`` / ``tb.ledger.v1`` protobuf (ADR-0018, D36).

Also the IBKR rows of the shared reject-code mapping (BR-005).
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

from tb.broker.v1 import gateway_pb2 as gw
from tb.broker.v1.credentials_pb2 import ENVIRONMENT_LIVE, ENVIRONMENT_PAPER
from tb.common.v1 import common_pb2 as common
from tb.ledger.v1 import ledger_pb2 as ledger
from tb.orders.v1 import orders_pb2 as orders
from tb_broker_ibkr.models import (
    AccountSnapshot,
    BrokerAccount,
    BrokerOrder,
    Capabilities,
    Environment,
    ExecutionReport,
    Instrument,
    InvalidOrderError,
    OrderChanges,
    OrderRequest,
    OrderState,
    OrderType,
    SessionState,
    SessionStatus,
    Side,
    TimeInForce,
)

ORDER_STATE = {
    OrderState.PENDING_SUBMIT: orders.ORDER_STATE_PENDING_SUBMIT,
    OrderState.SUBMITTED: orders.ORDER_STATE_SUBMITTED,
    OrderState.ACCEPTED: orders.ORDER_STATE_ACCEPTED,
    OrderState.PARTIALLY_FILLED: orders.ORDER_STATE_PARTIALLY_FILLED,
    OrderState.FILLED: orders.ORDER_STATE_FILLED,
    OrderState.PENDING_CANCEL: orders.ORDER_STATE_PENDING_CANCEL,
    OrderState.CANCELED: orders.ORDER_STATE_CANCELED,
    OrderState.REJECTED: orders.ORDER_STATE_REJECTED,
    OrderState.EXPIRED: orders.ORDER_STATE_EXPIRED,
    OrderState.REPLACED: orders.ORDER_STATE_REPLACED,
    OrderState.PENDING_REPLACE: orders.ORDER_STATE_PENDING_REPLACE,
    OrderState.UNKNOWN: orders.ORDER_STATE_UNKNOWN,
}
SESSION_STATE = {
    SessionState.DISCONNECTED: gw.SESSION_STATE_DISCONNECTED,
    SessionState.CONNECTING: gw.SESSION_STATE_CONNECTING,
    SessionState.AUTHENTICATED: gw.SESSION_STATE_AUTHENTICATED,
    SessionState.SYNCING: gw.SESSION_STATE_SYNCING,
    SessionState.READY: gw.SESSION_STATE_READY,
    SessionState.DEGRADED: gw.SESSION_STATE_DEGRADED,
    SessionState.STOPPED: gw.SESSION_STATE_STOPPED,
}
SIDE = {Side.BUY: common.SIDE_BUY, Side.SELL: common.SIDE_SELL, Side.SELL_SHORT: common.SIDE_SELL_SHORT}
SIDE_IN = {v: k for k, v in SIDE.items()}
ORDER_TYPE = {
    OrderType.MARKET: orders.ORDER_TYPE_MARKET,
    OrderType.LIMIT: orders.ORDER_TYPE_LIMIT,
    OrderType.STOP: orders.ORDER_TYPE_STOP,
    OrderType.STOP_LIMIT: orders.ORDER_TYPE_STOP_LIMIT,
}
ORDER_TYPE_IN = {v: k for k, v in ORDER_TYPE.items()}
TIF = {
    TimeInForce.DAY: orders.TIME_IN_FORCE_DAY,
    TimeInForce.GTC: orders.TIME_IN_FORCE_GTC,
    TimeInForce.IOC: orders.TIME_IN_FORCE_IOC,
    TimeInForce.FOK: orders.TIME_IN_FORCE_FOK,
    TimeInForce.OPG: orders.TIME_IN_FORCE_OPG,
    TimeInForce.CLS: orders.TIME_IN_FORCE_CLS,
}
TIF_IN = {v: k for k, v in TIF.items()}
_IB_TYPE = {
    "MKT": orders.ORDER_TYPE_MARKET,
    "MOC": orders.ORDER_TYPE_MARKET,
    "LMT": orders.ORDER_TYPE_LIMIT,
    "LOC": orders.ORDER_TYPE_LIMIT,
    "STP": orders.ORDER_TYPE_STOP,
    "STP LMT": orders.ORDER_TYPE_STOP_LIMIT,
    "TRAIL": orders.ORDER_TYPE_TRAILING_STOP,
}
_IB_TIF: dict[str, orders.TimeInForce] = {
    "DAY": orders.TIME_IN_FORCE_DAY,
    "GTC": orders.TIME_IN_FORCE_GTC,
    "IOC": orders.TIME_IN_FORCE_IOC,
    "FOK": orders.TIME_IN_FORCE_FOK,
    "OPG": orders.TIME_IN_FORCE_OPG,
}
LOCAL_REJECT: dict[str, gw.RejectCode] = {
    "invalid_attribution": gw.REJECT_CODE_INVALID_ATTRIBUTION,
    "invalid_order": gw.REJECT_CODE_INVALID_ORDER,
    "unknown_instrument": gw.REJECT_CODE_UNKNOWN_INSTRUMENT,
    "not_shortable": gw.REJECT_CODE_NOT_SHORTABLE,
    "not_tradable": gw.REJECT_CODE_NOT_TRADABLE,
}
# BR-005, IBKR rows: TWS API error code -> RejectCode. 201 is refined by message text.
IBKR_REJECT: dict[int, gw.RejectCode] = {
    103: gw.REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID,
    104: gw.REJECT_CODE_INVALID_ORDER,
    105: gw.REJECT_CODE_INVALID_ORDER,
    109: gw.REJECT_CODE_INVALID_ORDER,
    110: gw.REJECT_CODE_INVALID_ORDER,
    116: gw.REJECT_CODE_NOT_TRADABLE,
    117: gw.REJECT_CODE_NOT_TRADABLE,
    200: gw.REJECT_CODE_UNKNOWN_INSTRUMENT,
    201: gw.REJECT_CODE_BROKER_RISK,
    203: gw.REJECT_CODE_NOT_TRADABLE,
    321: gw.REJECT_CODE_INVALID_ORDER,
    388: gw.REJECT_CODE_INVALID_ORDER,
    404: gw.REJECT_CODE_NOT_SHORTABLE,
    434: gw.REJECT_CODE_INVALID_ORDER,
}
_BUYING_POWER_TEXT = ("equity with loan", "buying power", "insufficient", "initial margin", "available funds")


def ibkr_reject_code(code: int, message: str) -> gw.RejectCode:
    if code == 201:
        text = message.lower()
        if any(t in text for t in _BUYING_POWER_TEXT):
            return gw.REJECT_CODE_INSUFFICIENT_BUYING_POWER
        if "short" in text and ("locate" in text or "available" in text):
            return gw.REJECT_CODE_NOT_SHORTABLE
        if "market" in text and "closed" in text:
            return gw.REJECT_CODE_MARKET_CLOSED
    return IBKR_REJECT.get(code, gw.REJECT_CODE_BROKER_OTHER)


def dec(value: Decimal | None) -> common.Decimal | None:
    return None if value is None else common.Decimal(value=format(value, "f"))


def from_dec(msg: common.Decimal, field: str, *, required: bool = False) -> Decimal | None:
    if not msg.value:
        if required:
            raise InvalidOrderError(f"{field} is required")
        return None
    try:
        value = Decimal(msg.value)
    except InvalidOperation:
        raise InvalidOrderError(f"{field} is not a decimal") from None
    if not value.is_finite():
        raise InvalidOrderError(f"{field} must be finite")
    return value


def mode(environment: Environment) -> common.Mode:
    return common.MODE_LIVE if environment is Environment.LIVE else common.MODE_PAPER


def instrument_proto(instrument_id: str, con_id: int, symbol: str) -> common.Instrument:
    return common.Instrument(
        instrument_id=instrument_id,
        symbol=symbol,
        ibkr_con_id=str(con_id) if con_id else "",
        asset_class=common.ASSET_CLASS_US_EQUITY,
        currency="USD",
    )


def order_request_from_proto(req: gw.BrokerOrderRequest) -> OrderRequest:
    if req.HasField("bracket") and req.bracket.order_class not in (gw.ORDER_CLASS_UNSPECIFIED, gw.ORDER_CLASS_SIMPLE):
        raise InvalidOrderError("IBKR adapter does not support broker-side order classes (capabilities)")
    if req.trail_price.value or req.trail_percent.value or req.type == orders.ORDER_TYPE_TRAILING_STOP:
        raise InvalidOrderError("IBKR adapter does not support trailing stops (capabilities)")
    if req.type not in ORDER_TYPE_IN:
        raise InvalidOrderError("unsupported order type")
    if req.tif not in TIF_IN:
        raise InvalidOrderError("unsupported time in force")
    if req.side not in SIDE_IN:
        raise InvalidOrderError("side is required")
    qty = from_dec(req.qty, "qty", required=True)
    assert qty is not None
    if qty <= 0:
        raise InvalidOrderError("qty must be positive")
    inst = req.instrument
    instrument = None
    if inst.ibkr_con_id:
        try:
            con_id = int(inst.ibkr_con_id)
        except ValueError:
            raise InvalidOrderError("ibkr_con_id must be an integer", "unknown_instrument") from None
        instrument = Instrument(
            instrument_id=inst.instrument_id,
            con_id=con_id,
            symbol=inst.symbol,
            primary_exchange=inst.exchange,
            currency=inst.currency or "USD",
        )
    return OrderRequest(
        client_order_id=req.client_order_id,
        broker_account_id=req.broker_account_id,
        instrument_id=inst.instrument_id,
        side=SIDE_IN[req.side],
        qty=qty,
        order_type=ORDER_TYPE_IN[req.type],
        tif=TIF_IN[req.tif],
        limit_price=from_dec(req.limit_price, "limit_price"),
        stop_price=from_dec(req.stop_price, "stop_price"),
        extended_hours=req.extended_hours,
        correlation_id=req.correlation_id,
        instrument=instrument,
        symbol=inst.symbol,
        mode="live" if req.mode == common.MODE_LIVE else ("paper" if req.mode == common.MODE_PAPER else ""),
    )


def changes_from_proto(req: gw.ReplaceOrderRequest) -> OrderChanges:
    if req.trail_price.value:
        raise InvalidOrderError("IBKR adapter does not support trailing stops (capabilities)")
    return OrderChanges(
        qty=from_dec(req.qty, "qty"),
        limit_price=from_dec(req.limit_price, "limit_price"),
        stop_price=from_dec(req.stop_price, "stop_price"),
        tif=TIF_IN.get(req.tif) if req.tif else None,
    )


def broker_order_proto(order: BrokerOrder) -> gw.BrokerOrder:
    ib_type = order.order_type.upper()
    tif = (
        orders.TIME_IN_FORCE_CLS
        if ib_type in {"MOC", "LOC"}
        else _IB_TIF.get(order.tif.upper(), orders.TIME_IN_FORCE_UNSPECIFIED)
    )
    return gw.BrokerOrder(
        broker_account_id=order.broker_account_id,
        broker_order_id=order.broker_order_id,
        client_order_id=order.client_order_id,
        perm_id=order.broker_order_id,
        instrument=instrument_proto(order.instrument_id, order.con_id, order.symbol),
        side=SIDE[order.side],
        type=_IB_TYPE.get(ib_type, orders.ORDER_TYPE_UNSPECIFIED),
        tif=tif,
        order_class=gw.ORDER_CLASS_SIMPLE,
        qty=dec(order.qty),
        filled_qty=dec(order.filled_qty),
        avg_fill_price=dec(order.avg_fill_price),
        limit_price=dec(order.limit_price),
        stop_price=dec(order.stop_price),
        state=ORDER_STATE[order.state],
        broker_status=order.raw_status,
        updated_at=order.updated_at,
    )


def execution_proto(report: ExecutionReport, environment: Environment) -> orders.ExecutionReport:
    return orders.ExecutionReport(
        broker_account_id=report.broker_account_id,
        broker_exec_id=report.broker_exec_id,
        client_order_id=report.client_order_id,
        broker_order_id=report.broker_order_id,
        perm_id=report.broker_order_id,
        instrument_id=report.instrument_id,
        symbol=report.symbol,
        side=SIDE[report.side],
        qty=dec(report.qty),
        price=dec(report.price),
        fees=dec(report.fees),
        fee_currency=report.fee_currency,
        event_time=report.event_time,
        mode=mode(environment),
        ingested_at=report.ingested_at,
        broker=common.BROKER_IBKR,
    )


def snapshot_proto(snapshot: AccountSnapshot, environment: Environment) -> ledger.AccountSnapshot:
    return ledger.AccountSnapshot(
        broker_account_id=snapshot.broker_account_id,
        broker=common.BROKER_IBKR,
        mode=mode(environment),
        cash=dec(snapshot.cash),
        buying_power=dec(snapshot.buying_power),
        equity=dec(snapshot.net_liquidation),
        positions=[
            ledger.Position(
                instrument_id=p.instrument_id,
                qty=dec(p.qty),
                avg_cost=dec(p.avg_cost),
                symbol=p.symbol,
                broker_instrument_id=str(p.con_id),
            )
            for p in snapshot.positions
        ],
        as_of=snapshot.as_of,
        intraday_margin_deficit=snapshot.margin_deficit,
        maintenance_margin=dec(snapshot.maintenance_margin),
        currency=snapshot.currency,
        day_trade_count=-1,
    )


def session_status_proto(status: SessionStatus, last_exec_time: int = 0) -> gw.SessionStatus:
    return gw.SessionStatus(
        broker_account_id=status.broker_account_id,
        state=SESSION_STATE[status.state],
        connected=status.connected,
        authenticated=status.authenticated,
        last_heartbeat=status.last_heartbeat,
        state_since=status.since,
        degraded_reasons=list(status.degraded_reasons),
        last_exec_time=last_exec_time,
    )


def capabilities_proto(caps: Capabilities) -> gw.Capabilities:
    return gw.Capabilities(
        order_types=[ORDER_TYPE[t] for t in caps.order_types],
        tifs=[TIF[t] for t in caps.tifs],
        supports_bracket=caps.supports_bracket,
        supports_oco=caps.supports_oco,
        supports_oto=caps.supports_oto,
        supports_fractional=caps.supports_fractional,
        supports_short=caps.supports_short,
        supports_extended_hours=caps.supports_extended_hours,
        max_client_order_id_len=caps.max_client_order_id_len,
        pdt_applies=caps.pdt_applies,
        is_margin=caps.is_margin,
        data_feeds=list(caps.data_feeds),
        supports_replace=caps.supports_replace,
        supports_trailing_stop=caps.supports_trailing_stop,
    )


def account_proto(account: BrokerAccount, status: SessionStatus) -> gw.BrokerAccount:
    return gw.BrokerAccount(
        broker_account_id=account.broker_account_id,
        broker=common.BROKER_IBKR,
        environment=ENVIRONMENT_LIVE if account.environment is Environment.LIVE else ENVIRONMENT_PAPER,
        capabilities=capabilities_proto(account.capabilities),
        session=session_status_proto(status),
    )


def subject_token(broker_account_id: str) -> str:
    """NATS subject tokens cannot contain '.', '*', '>' or whitespace."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in broker_account_id)


def order_update_proto(order: BrokerOrder) -> gw.BrokerOrderUpdate:
    return gw.BrokerOrderUpdate(
        broker_account_id=order.broker_account_id,
        event=order.state.value,
        order=broker_order_proto(order),
        event_time=order.updated_at,
        received_at=time.time_ns(),
    )


def order_dedupe_key(order: BrokerOrder) -> str:
    """Same shape as the Go adapter's ``OrderDedupeKey``: id|status|filled|updated_at."""
    return f"{order.broker_order_id}|{order.raw_status}|{order.filled_qty}|{order.updated_at}"
