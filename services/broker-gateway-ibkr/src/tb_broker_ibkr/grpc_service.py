"""gRPC server for ``tb.broker.v1.BrokerGatewayService`` (ADR-0018, D36; BR-004, SE-040).

Handlers are registered generically (``grpc.method_handlers_generic_handler``) against the generated
message classes, so no Python gRPC codegen is needed. Error model (BR-004): rejections are typed
responses; a non-ready session is ``FAILED_PRECONDITION`` with
``google.rpc.ErrorInfo{domain: "tb.broker.v1", reason: "BROKER_UNAVAILABLE"}``; unknown account is
``NOT_FOUND``; malformed input is ``INVALID_ARGUMENT``; a caller outside the method allowlist is
``PERMISSION_DENIED``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import grpc
from google.protobuf import any_pb2
from google.rpc import code_pb2, error_details_pb2, status_pb2
from grpc_status import rpc_status

from tb.broker.v1 import gateway_pb2 as gw
from tb.common.v1 import common_pb2 as common
from tb_broker_ibkr import codec
from tb_broker_ibkr.adapter import IbkrAdapter
from tb_broker_ibkr.events import AlertEvent, ReconciliationRequest
from tb_broker_ibkr.instruments import UnknownInstrumentError
from tb_broker_ibkr.models import (
    AccountNotFoundError,
    AccountSnapshot,
    BrokerOrder,
    BrokerUnavailableError,
    ExecutionReport,
    InvalidOrderError,
    OrderAlreadyFinalError,
    OrderNotFoundError,
    OrderRejectedError,
    SessionState,
    SessionStatus,
    SubmitOutcomeUnknownError,
)
from tb_broker_ibkr.svcauth import SCHEME, TokenError, Verifier

log = logging.getLogger("tb_broker_ibkr.grpc")

SERVICE_NAME = "tb.broker.v1.BrokerGatewayService"
AUDIENCE = "broker-gateway-ibkr"
_READERS = ["execution", "ledger", "api-gateway"]
# Per-method caller allowlist (SE-040), following the contract's consumer list.
ALLOWLIST: dict[str, list[str]] = {
    "ListAccounts": [*_READERS, "marketdata"],
    "GetSessionStatus": [*_READERS, "marketdata"],
    "GetAccountSnapshot": _READERS,
    "StreamAccountSnapshots": ["execution", "ledger"],
    "SubmitOrder": ["execution"],
    "CancelOrder": ["execution"],
    "ReplaceOrder": ["execution"],
    "GetOrder": ["execution", "ledger"],
    "ListOpenOrders": ["execution", "ledger"],
    "ListOrders": ["execution", "ledger"],
    "ListExecutions": ["execution", "ledger"],
    "StreamOrderUpdates": ["execution", "ledger"],
    "StreamExecutions": ["execution", "ledger"],
    "GetInstrumentInfo": ["execution", "marketdata"],
}
_SESSION_STATE_NAME = {s: gw.SessionState.Name(v) for s, v in codec.SESSION_STATE.items()}


def _now_ns() -> int:
    return time.time_ns()


@dataclass
class StreamHub:
    """In-process fan-out of adapter events to gRPC stream subscribers (an EventSink)."""

    environment: Any
    state: Callable[[], SessionState] = lambda: SessionState.READY
    _subs: dict[int, tuple[str, str, asyncio.Queue[Any]]] = field(default_factory=dict)
    _next: int = 0

    @contextlib.asynccontextmanager
    async def subscribe(self, kind: str, broker_account_id: str) -> AsyncIterator[asyncio.Queue[Any]]:
        self._next += 1
        sid = self._next
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=10_000)
        self._subs[sid] = (kind, broker_account_id, queue)
        try:
            yield queue
        finally:
            self._subs.pop(sid, None)

    def _fanout(self, kind: str, broker_account_id: str, item: Any) -> None:
        for sub_kind, acct, queue in list(self._subs.values()):
            if sub_kind == kind and acct == broker_account_id:
                try:
                    queue.put_nowait(item)
                except asyncio.QueueFull:
                    log.warning("stream subscriber too slow; dropping", extra={"stream": kind})

    async def order_update(self, order: BrokerOrder) -> None:
        update = gw.BrokerOrderUpdate(
            broker_account_id=order.broker_account_id,
            event=order.state.value,
            order=codec.broker_order_proto(order),
            event_time=order.updated_at,
            received_at=_now_ns(),
            from_resync=self.state() is SessionState.SYNCING,
        )
        self._fanout("orders", order.broker_account_id, update)

    async def execution(self, report: ExecutionReport) -> None:
        self._fanout("executions", report.broker_account_id, codec.execution_proto(report, self.environment))

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._fanout("snapshots", snapshot.broker_account_id, codec.snapshot_proto(snapshot, self.environment))

    async def session_status(self, status: SessionStatus) -> None:
        return None

    async def alert(self, event: AlertEvent) -> None:
        return None

    async def resolve_alert(self, dedupe_key: str, message: str) -> None:
        return None

    async def request_reconciliation(self, request: ReconciliationRequest) -> None:
        return None


class _Abort(Exception):
    def __init__(self, status: grpc.Status) -> None:
        self.status = status


def _status(code: int, message: str, reason: str = "", metadata: dict[str, str] | None = None) -> grpc.Status:
    details = []
    if reason:
        packed = any_pb2.Any()
        packed.Pack(error_details_pb2.ErrorInfo(domain="tb.broker.v1", reason=reason, metadata=metadata or {}))
        details.append(packed)
    return rpc_status.to_status(status_pb2.Status(code=code, message=message, details=details))


def _unavailable(err: BrokerUnavailableError) -> _Abort:
    return _Abort(
        _status(
            code_pb2.FAILED_PRECONDITION,
            str(err),
            "BROKER_UNAVAILABLE",
            {"session_state": _SESSION_STATE_NAME[err.state]},
        )
    )


def _local_reject(err: InvalidOrderError) -> gw.RejectReason:
    return gw.RejectReason(
        code=codec.LOCAL_REJECT.get(err.reason, gw.REJECT_CODE_INVALID_ORDER), message=str(err), local=True
    )


def _broker_reject(err: OrderRejectedError) -> gw.RejectReason:
    message = str(err)
    return gw.RejectReason(
        code=codec.ibkr_reject_code(err.broker_code, message), message=message, broker_code=str(err.broker_code)
    )


class BrokerGatewayServicer:
    def __init__(self, adapter: IbkrAdapter, hub: StreamHub) -> None:
        self._a = adapter
        self._hub = hub

    # ---- accounts and session ------------------------------------------------------------------
    async def ListAccounts(self, req: gw.ListAccountsRequest, ctx: grpc.aio.ServicerContext) -> gw.ListAccountsResponse:
        return gw.ListAccountsResponse(
            accounts=[
                codec.account_proto(a, self._a.session_status(a.broker_account_id)) for a in self._a.list_accounts()
            ]
        )

    async def GetSessionStatus(
        self, req: gw.GetSessionStatusRequest, ctx: grpc.aio.ServicerContext
    ) -> gw.GetSessionStatusResponse:
        status = self._a.session_status(req.broker_account_id)
        return gw.GetSessionStatusResponse(
            status=codec.session_status_proto(status, self._a.last_exec_time(req.broker_account_id))
        )

    async def GetAccountSnapshot(
        self, req: gw.GetAccountSnapshotRequest, ctx: grpc.aio.ServicerContext
    ) -> gw.GetAccountSnapshotResponse:
        snap = await self._a.get_account_snapshot(req.broker_account_id)
        return gw.GetAccountSnapshotResponse(snapshot=codec.snapshot_proto(snap, self._a.environment))

    async def StreamAccountSnapshots(
        self, req: gw.StreamAccountSnapshotsRequest, ctx: grpc.aio.ServicerContext
    ) -> AsyncIterator[gw.StreamAccountSnapshotsResponse]:
        self._a.session_status(req.broker_account_id)
        async with self._hub.subscribe("snapshots", req.broker_account_id) as queue:
            with contextlib.suppress(BrokerUnavailableError):
                snap = await self._a.get_account_snapshot(req.broker_account_id)
                yield gw.StreamAccountSnapshotsResponse(snapshot=codec.snapshot_proto(snap, self._a.environment))
            while True:
                yield gw.StreamAccountSnapshotsResponse(snapshot=await queue.get())

    # ---- orders --------------------------------------------------------------------------------
    async def SubmitOrder(self, req: gw.SubmitOrderRequest, ctx: grpc.aio.ServicerContext) -> gw.SubmitOrderResponse:
        try:
            order = codec.order_request_from_proto(req.order)
        except InvalidOrderError as err:
            return gw.SubmitOrderResponse(outcome=gw.SUBMIT_OUTCOME_REJECTED, reject=_local_reject(err))
        try:
            ack = await self._a.submit_order(order)
        except InvalidOrderError as err:
            return gw.SubmitOrderResponse(outcome=gw.SUBMIT_OUTCOME_REJECTED, reject=_local_reject(err))
        except OrderRejectedError as err:
            return gw.SubmitOrderResponse(outcome=gw.SUBMIT_OUTCOME_REJECTED, reject=_broker_reject(err))
        except SubmitOutcomeUnknownError as err:
            view = self._a.order_view(order.client_order_id)
            return gw.SubmitOrderResponse(
                outcome=gw.SUBMIT_OUTCOME_UNKNOWN,
                order=codec.broker_order_proto(view) if view else None,
                reject=gw.RejectReason(code=gw.REJECT_CODE_UNSPECIFIED, message=str(err)),
            )
        view = self._a.order_view(order.client_order_id)
        return gw.SubmitOrderResponse(
            outcome=gw.SUBMIT_OUTCOME_ACCEPTED,
            order=codec.broker_order_proto(view) if view else None,
            recovered_by_lookup=ack.recovered,
            accepted_at=ack.accepted_at,
        )

    async def CancelOrder(self, req: gw.CancelOrderRequest, ctx: grpc.aio.ServicerContext) -> gw.CancelOrderResponse:
        if bool(req.client_order_id) == bool(req.broker_order_id):
            raise _Abort(_status(code_pb2.INVALID_ARGUMENT, "exactly one of client_order_id / broker_order_id"))
        try:
            ack = await self._a.cancel_order(
                req.broker_account_id, client_order_id=req.client_order_id, broker_order_id=req.broker_order_id
            )
        except OrderNotFoundError:
            return gw.CancelOrderResponse(outcome=gw.CANCEL_OUTCOME_NOT_FOUND)
        except OrderAlreadyFinalError:
            return gw.CancelOrderResponse(outcome=gw.CANCEL_OUTCOME_ALREADY_FINAL, order=await self._order(req))
        except InvalidOrderError as err:
            return gw.CancelOrderResponse(outcome=gw.CANCEL_OUTCOME_REJECTED, reject=_local_reject(err))
        view = self._a.order_view(ack.client_order_id)
        return gw.CancelOrderResponse(
            outcome=gw.CANCEL_OUTCOME_REQUESTED, order=codec.broker_order_proto(view) if view else None
        )

    async def ReplaceOrder(self, req: gw.ReplaceOrderRequest, ctx: grpc.aio.ServicerContext) -> gw.ReplaceOrderResponse:
        try:
            ack = await self._a.replace_order(
                req.broker_account_id, req.client_order_id, codec.changes_from_proto(req), req.new_client_order_id
            )
        except OrderNotFoundError:
            return gw.ReplaceOrderResponse(outcome=gw.REPLACE_OUTCOME_NOT_FOUND)
        except InvalidOrderError as err:
            return gw.ReplaceOrderResponse(outcome=gw.REPLACE_OUTCOME_REJECTED, reject=_local_reject(err))
        view = self._a.order_view(ack.new_client_order_id)
        return gw.ReplaceOrderResponse(
            outcome=gw.REPLACE_OUTCOME_ACCEPTED, order=codec.broker_order_proto(view) if view else None
        )

    async def _order(self, req: gw.CancelOrderRequest | gw.GetOrderRequest) -> gw.BrokerOrder | None:
        view = await self._a.get_order(
            req.broker_account_id, client_order_id=req.client_order_id, broker_order_id=req.broker_order_id
        )
        return codec.broker_order_proto(view) if view else None

    async def GetOrder(self, req: gw.GetOrderRequest, ctx: grpc.aio.ServicerContext) -> gw.GetOrderResponse:
        if bool(req.client_order_id) == bool(req.broker_order_id):
            raise _Abort(_status(code_pb2.INVALID_ARGUMENT, "exactly one of client_order_id / broker_order_id"))
        order = await self._order(req)
        return gw.GetOrderResponse(found=order is not None, order=order)

    async def ListOpenOrders(
        self, req: gw.ListOpenOrdersRequest, ctx: grpc.aio.ServicerContext
    ) -> gw.ListOpenOrdersResponse:
        orders = await self._a.list_open_orders(req.broker_account_id)
        return gw.ListOpenOrdersResponse(orders=[codec.broker_order_proto(o) for o in orders])

    async def ListOrders(self, req: gw.ListOrdersRequest, ctx: grpc.aio.ServicerContext) -> gw.ListOrdersResponse:
        orders = await self._a.list_orders(req.broker_account_id)
        selected = sorted((o for o in orders if o.updated_at >= req.since), key=lambda o: o.updated_at)
        return gw.ListOrdersResponse(orders=[codec.broker_order_proto(o) for o in selected])

    async def ListExecutions(
        self, req: gw.ListExecutionsRequest, ctx: grpc.aio.ServicerContext
    ) -> gw.ListExecutionsResponse:
        reports = await self._a.list_executions(req.broker_account_id)
        selected = sorted(
            (r for r in reports if r.event_time >= req.since), key=lambda r: (r.event_time, r.broker_exec_id)
        )
        return gw.ListExecutionsResponse(executions=[codec.execution_proto(r, self._a.environment) for r in selected])

    async def StreamOrderUpdates(
        self, req: gw.StreamOrderUpdatesRequest, ctx: grpc.aio.ServicerContext
    ) -> AsyncIterator[gw.StreamOrderUpdatesResponse]:
        self._a.session_status(req.broker_account_id)
        async with self._hub.subscribe("orders", req.broker_account_id) as queue:
            while True:
                yield gw.StreamOrderUpdatesResponse(update=await queue.get())

    async def StreamExecutions(
        self, req: gw.StreamExecutionsRequest, ctx: grpc.aio.ServicerContext
    ) -> AsyncIterator[gw.StreamExecutionsResponse]:
        self._a.session_status(req.broker_account_id)
        async with self._hub.subscribe("executions", req.broker_account_id) as queue:
            while True:
                yield gw.StreamExecutionsResponse(execution=await queue.get())

    async def GetInstrumentInfo(
        self, req: gw.GetInstrumentInfoRequest, ctx: grpc.aio.ServicerContext
    ) -> gw.GetInstrumentInfoResponse:
        self._a.session_status(req.broker_account_id)
        try:
            inst = await self._a.resolver.resolve(req.instrument.instrument_id)
        except UnknownInstrumentError:
            raise _Abort(_status(code_pb2.NOT_FOUND, "instrument not in the IBKR instrument map")) from None
        return gw.GetInstrumentInfoResponse(
            info=gw.InstrumentInfo(
                instrument=common.Instrument(
                    instrument_id=inst.instrument_id,
                    symbol=inst.symbol,
                    exchange=inst.primary_exchange,
                    currency=inst.currency,
                    ibkr_con_id=str(inst.con_id),
                    asset_class=common.ASSET_CLASS_US_EQUITY,
                ),
                tradable=inst.tradable,
                shortable=inst.shortable,
                min_tick=codec.dec(inst.min_tick),
                lot_size=codec.dec(Decimal(1)),
                status="active" if inst.tradable else "inactive",
                as_of=_now_ns(),
            )
        )


def _wrap_unary(fn: Callable[[Any, Any], Awaitable[Any]]) -> Callable[[Any, Any], Awaitable[Any]]:
    async def handler(request: Any, context: grpc.aio.ServicerContext) -> Any:
        try:
            return await fn(request, context)
        except BrokerUnavailableError as err:
            await context.abort_with_status(_unavailable(err).status)
        except AccountNotFoundError as err:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(err))
        except InvalidOrderError as err:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(err))
        except _Abort as err:
            await context.abort_with_status(err.status)

    return handler


def _wrap_stream(fn: Callable[[Any, Any], AsyncIterator[Any]]) -> Callable[[Any, Any], AsyncIterator[Any]]:
    async def handler(request: Any, context: grpc.aio.ServicerContext) -> AsyncIterator[Any]:
        try:
            async for item in fn(request, context):
                yield item
        except AccountNotFoundError as err:
            await context.abort(grpc.StatusCode.NOT_FOUND, str(err))

    return handler


_STREAMS = {"StreamAccountSnapshots", "StreamOrderUpdates", "StreamExecutions"}
_TYPES: dict[str, tuple[Any, Any]] = {
    "ListAccounts": (gw.ListAccountsRequest, gw.ListAccountsResponse),
    "GetSessionStatus": (gw.GetSessionStatusRequest, gw.GetSessionStatusResponse),
    "GetAccountSnapshot": (gw.GetAccountSnapshotRequest, gw.GetAccountSnapshotResponse),
    "StreamAccountSnapshots": (gw.StreamAccountSnapshotsRequest, gw.StreamAccountSnapshotsResponse),
    "SubmitOrder": (gw.SubmitOrderRequest, gw.SubmitOrderResponse),
    "CancelOrder": (gw.CancelOrderRequest, gw.CancelOrderResponse),
    "ReplaceOrder": (gw.ReplaceOrderRequest, gw.ReplaceOrderResponse),
    "GetOrder": (gw.GetOrderRequest, gw.GetOrderResponse),
    "ListOpenOrders": (gw.ListOpenOrdersRequest, gw.ListOpenOrdersResponse),
    "ListOrders": (gw.ListOrdersRequest, gw.ListOrdersResponse),
    "ListExecutions": (gw.ListExecutionsRequest, gw.ListExecutionsResponse),
    "StreamOrderUpdates": (gw.StreamOrderUpdatesRequest, gw.StreamOrderUpdatesResponse),
    "StreamExecutions": (gw.StreamExecutionsRequest, gw.StreamExecutionsResponse),
    "GetInstrumentInfo": (gw.GetInstrumentInfoRequest, gw.GetInstrumentInfoResponse),
}


def generic_handler(servicer: BrokerGatewayServicer) -> grpc.GenericRpcHandler:
    handlers = {}
    for name, (req_type, resp_type) in _TYPES.items():
        fn = getattr(servicer, name)
        if name in _STREAMS:
            handlers[name] = grpc.unary_stream_rpc_method_handler(
                _wrap_stream(fn),
                request_deserializer=req_type.FromString,
                response_serializer=resp_type.SerializeToString,
            )
        else:
            handlers[name] = grpc.unary_unary_rpc_method_handler(
                _wrap_unary(fn),
                request_deserializer=req_type.FromString,
                response_serializer=resp_type.SerializeToString,
            )
    return grpc.method_handlers_generic_handler(SERVICE_NAME, handlers)


class ServiceTokenInterceptor(grpc.aio.ServerInterceptor):
    """SE-040: every call carries a valid service token whose issuer is allowed for the method."""

    def __init__(self, verifier: Verifier, allowlist: dict[str, list[str]] | None = None) -> None:
        self._verifier = verifier
        self._allow = allowlist or ALLOWLIST

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        method = details.method.rsplit("/", 1)[-1]
        values = [v for k, v in (details.invocation_metadata or ()) if k == "authorization"]
        if len(values) != 1 or not str(values[0]).startswith(SCHEME):
            return _deny(grpc.StatusCode.UNAUTHENTICATED, "service token required")
        try:
            claims = self._verifier.verify(str(values[0])[len(SCHEME) :])
        except TokenError:
            return _deny(grpc.StatusCode.UNAUTHENTICATED, "invalid service token")
        if claims.issuer not in self._allow.get(method, []):
            return _deny(grpc.StatusCode.PERMISSION_DENIED, f"service {claims.issuer!r} may not call {method}")
        return await continuation(details)


def _deny(code: grpc.StatusCode, message: str) -> grpc.RpcMethodHandler:
    async def abort(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(code, message)

    return grpc.unary_unary_rpc_method_handler(abort)


def build_server(servicer: BrokerGatewayServicer, verifier: Verifier, address: str) -> tuple[grpc.aio.Server, int]:
    server = grpc.aio.server(interceptors=[ServiceTokenInterceptor(verifier)])
    server.add_generic_rpc_handlers([generic_handler(servicer)])
    port = server.add_insecure_port(address)
    return server, port
