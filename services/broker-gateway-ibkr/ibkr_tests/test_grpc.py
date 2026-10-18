"""The ``tb.broker.v1.BrokerGatewayService`` gRPC surface over the fake gateway.

BR-004 (error model), BR-005 (IBKR reject codes), BR-007, BR-019, SE-040 (service tokens and
per-method allowlist), OE-011 / BR-014 (audit on submit, cancel, reject; fail closed).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import grpc
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.rpc import error_details_pb2
from grpc_status import rpc_status
from tb_broker_ibkr.fakes.gateway import GatewayState
from tb_broker_ibkr.grpc_service import AUDIENCE, SERVICE_NAME, BrokerGatewayServicer, StreamHub, build_server
from tb_broker_ibkr.models import Environment
from tb_broker_ibkr.svcauth import SCHEME, Verifier, mint

from tb.broker.v1 import gateway_pb2 as gw
from tb.common.v1 import common_pb2 as common
from tb.orders.v1 import orders_pb2 as orders

from .conftest import AAPL, ACCT, PAPER_CODE, Harness, cid, make_harness, run

KEYS = {name: Ed25519PrivateKey.generate() for name in ("execution", "ledger", "intruder")}


class Client:
    def __init__(self, channel: grpc.aio.Channel, caller: str = "execution") -> None:
        self._ch = channel
        self.caller = caller

    def _meta(self) -> list[tuple[str, str]]:
        return [("authorization", SCHEME + mint(self.caller, AUDIENCE, KEYS[self.caller]))]

    async def call(self, method: str, request: Any, response_type: Any) -> Any:
        fn = self._ch.unary_unary(
            f"/{SERVICE_NAME}/{method}",
            request_serializer=type(request).SerializeToString,
            response_deserializer=response_type.FromString,
        )
        return await fn(request, metadata=self._meta())

    def stream(self, method: str, request: Any, response_type: Any) -> Any:
        fn = self._ch.unary_stream(
            f"/{SERVICE_NAME}/{method}",
            request_serializer=type(request).SerializeToString,
            response_deserializer=response_type.FromString,
        )
        return fn(request, metadata=self._meta())


def order_request(**changes: Any) -> gw.SubmitOrderRequest:
    fields: dict[str, Any] = {
        "client_order_id": cid(),
        "broker_account_id": ACCT,
        "instrument": common.Instrument(
            instrument_id=AAPL.instrument_id, symbol="AAPL", ibkr_con_id=str(AAPL.con_id), exchange="NASDAQ"
        ),
        "side": common.SIDE_BUY,
        "qty": common.Decimal(value="10"),
        "type": orders.ORDER_TYPE_LIMIT,
        "limit_price": common.Decimal(value="180.25"),
        "tif": orders.TIME_IN_FORCE_DAY,
    }
    fields.update(changes)
    return gw.SubmitOrderRequest(order=gw.BrokerOrderRequest(**fields))


def with_server(
    body: Callable[[Harness, Client, Callable[[str], Client]], Awaitable[None]], *, ready: bool = True
) -> None:
    async def main() -> None:
        ref: list[Harness] = []
        hub = StreamHub(Environment.PAPER, lambda: ref[0].adapter.supervisor.state)
        h = make_harness(extra_sinks=[hub])
        ref.append(h)
        if ready:
            await h.start()
            await h.until_ready()
        verifier = Verifier(AUDIENCE, {n: k.public_key() for n, k in KEYS.items() if n != "intruder"})
        server, port = build_server(BrokerGatewayServicer(h.adapter, hub), verifier, "127.0.0.1:0")
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        try:
            await body(h, Client(channel), lambda caller: Client(channel, caller))
        finally:
            await channel.close()
            await server.stop(None)
            if ready:
                await h.stop()

    run(main)


def test_list_accounts_capabilities_and_session() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        resp = await c.call("ListAccounts", gw.ListAccountsRequest(), gw.ListAccountsResponse)
        (acct,) = resp.accounts
        assert acct.broker_account_id == ACCT and acct.broker == common.BROKER_IBKR
        assert acct.session.state == gw.SESSION_STATE_READY
        assert acct.capabilities.max_client_order_id_len == 44
        assert not acct.capabilities.supports_bracket and acct.capabilities.supports_replace
        assert PAPER_CODE not in str(resp), "never expose the IBKR account number"

    with_server(body)


def test_br007_submit_and_retry_are_one_order() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        first = await c.call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
        assert first.outcome == gw.SUBMIT_OUTCOME_ACCEPTED
        assert first.order.perm_id and first.order.client_order_id == cid()
        assert first.order.instrument.ibkr_con_id == str(AAPL.con_id)
        again = await c.call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
        assert again.outcome == gw.SUBMIT_OUTCOME_ACCEPTED and again.recovered_by_lookup
        assert len(h.gateway.placements) == 1
        assert [e.action for e in h.audit.events] == ["broker.order.submit"]

    with_server(body)


def test_br003_bad_attribution_is_a_local_reject_and_audited() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        resp = await c.call("SubmitOrder", order_request(client_order_id="nope"), gw.SubmitOrderResponse)
        assert resp.outcome == gw.SUBMIT_OUTCOME_REJECTED
        assert resp.reject.code == gw.REJECT_CODE_INVALID_ATTRIBUTION and resp.reject.local
        assert h.gateway.placements == []
        assert [e.action for e in h.audit.events] == ["broker.order.reject"]

    with_server(body)


def test_br005_ibkr_margin_reject_maps_to_buying_power() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        h.gateway.reject_next = (201, "Order rejected - reason:YOUR ORDER IS NOT ACCEPTED. ... EQUITY WITH LOAN VALUE")
        resp = await c.call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
        assert resp.outcome == gw.SUBMIT_OUTCOME_REJECTED
        assert resp.reject.code == gw.REJECT_CODE_INSUFFICIENT_BUYING_POWER
        assert resp.reject.broker_code == "201" and not resp.reject.local
        await h.clock.settle()
        assert [e.action for e in h.audit.events] == ["broker.order.submit", "broker.order.reject"]

    with_server(body)


def test_br004_not_ready_is_failed_precondition_with_error_info() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        h.gateway.go_down(GatewayState.DOWN)
        await h.clock.settle()
        try:
            await c.call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
        except grpc.aio.AioRpcError as err:
            assert err.code() is grpc.StatusCode.FAILED_PRECONDITION
            status = rpc_status.from_call(err)
            assert status is not None
            info = error_details_pb2.ErrorInfo()
            assert status.details[0].Unpack(info)
            assert (info.domain, info.reason) == ("tb.broker.v1", "BROKER_UNAVAILABLE")
            assert info.metadata["session_state"] == "SESSION_STATE_DISCONNECTED"
        else:
            raise AssertionError("expected FAILED_PRECONDITION")

    with_server(body)


def test_se040_tokens_and_allowlist() -> None:
    async def body(h: Harness, c: Client, as_caller: Callable[[str], Client]) -> None:
        for caller, code in (
            ("ledger", grpc.StatusCode.PERMISSION_DENIED),
            ("intruder", grpc.StatusCode.UNAUTHENTICATED),
        ):
            try:
                await as_caller(caller).call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
            except grpc.aio.AioRpcError as err:
                assert err.code() is code
            else:
                raise AssertionError(f"{caller} must not submit")
        resp = await as_caller("ledger").call(
            "ListOrders", gw.ListOrdersRequest(broker_account_id=ACCT), gw.ListOrdersResponse
        )
        assert list(resp.orders) == []
        assert h.gateway.placements == []

    with_server(body)


def test_unknown_account_is_not_found() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        try:
            await c.call(
                "GetSessionStatus",
                gw.GetSessionStatusRequest(broker_account_id="ibkr-paper-x"),
                gw.GetSessionStatusResponse,
            )
        except grpc.aio.AioRpcError as err:
            assert err.code() is grpc.StatusCode.NOT_FOUND
        else:
            raise AssertionError("expected NOT_FOUND")

    with_server(body)


def test_cancel_get_order_and_streams() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        stream = c.stream(
            "StreamExecutions", gw.StreamExecutionsRequest(broker_account_id=ACCT), gw.StreamExecutionsResponse
        )
        updates = c.stream(
            "StreamOrderUpdates", gw.StreamOrderUpdatesRequest(broker_account_id=ACCT), gw.StreamOrderUpdatesResponse
        )
        await asyncio.sleep(0.1)
        sub = await c.call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
        perm = int(sub.order.perm_id)
        h.gateway.fill(perm, Decimal(4), Decimal("180.25"))
        first = await asyncio.wait_for(stream.read(), 5)
        assert first.execution.qty.value == "4" and first.execution.perm_id == str(perm)
        update = await asyncio.wait_for(updates.read(), 5)
        assert update.update.order.client_order_id == cid()
        cancel = await c.call(
            "CancelOrder", gw.CancelOrderRequest(broker_account_id=ACCT, client_order_id=cid()), gw.CancelOrderResponse
        )
        assert cancel.outcome == gw.CANCEL_OUTCOME_REQUESTED
        await h.clock.settle()
        again = await c.call(
            "CancelOrder", gw.CancelOrderRequest(broker_account_id=ACCT, client_order_id=cid()), gw.CancelOrderResponse
        )
        assert again.outcome == gw.CANCEL_OUTCOME_ALREADY_FINAL
        got = await c.call(
            "GetOrder", gw.GetOrderRequest(broker_account_id=ACCT, broker_order_id=str(perm)), gw.GetOrderResponse
        )
        assert got.found and got.order.state == orders.ORDER_STATE_CANCELED and got.order.filled_qty.value == "4"
        execs = await c.call(
            "ListExecutions", gw.ListExecutionsRequest(broker_account_id=ACCT), gw.ListExecutionsResponse
        )
        assert [e.qty.value for e in execs.executions] == ["4"]
        stream.cancel()
        updates.cancel()
        assert [e.action for e in h.audit.events] == ["broker.order.submit", "broker.order.cancel"]

    with_server(body)


def test_br019_manual_orders_visible_over_grpc() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        manual = h.gateway.place_external_order(PAPER_CODE, AAPL.con_id, "AAPL", "SELL", Decimal(2))
        await h.clock.settle()
        resp = await c.call("ListOrders", gw.ListOrdersRequest(broker_account_id=ACCT), gw.ListOrdersResponse)
        (order,) = resp.orders
        assert order.perm_id == str(manual.perm_id) and order.client_order_id == ""

    with_server(body)


def test_se021_audit_failure_blocks_the_order() -> None:
    async def body(h: Harness, c: Client, _: Any) -> None:
        h.audit.fail = True
        try:
            await c.call("SubmitOrder", order_request(), gw.SubmitOrderResponse)
        except grpc.aio.AioRpcError as err:
            assert err.code() is grpc.StatusCode.UNKNOWN
        else:
            raise AssertionError("submit must fail when the audit append fails")
        assert h.gateway.placements == []

    with_server(body)
