"""The service entrypoint end to end: real ib_async against the wire fake, gRPC and ops servers,
fail-closed audit (SE-021), and a live adapter held in ``stopped`` (BR-013, OE-010)."""

from __future__ import annotations

import asyncio
import base64
import socket
import urllib.request
from pathlib import Path

import grpc
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from google.rpc import error_details_pb2
from grpc_status import rpc_status
from tb_broker_ibkr.__main__ import serve
from tb_broker_ibkr.config import IbkrSettings
from tb_broker_ibkr.fakes.wire import WireGateway
from tb_broker_ibkr.grpc_service import AUDIENCE, SERVICE_NAME
from tb_broker_ibkr.svcauth import SCHEME, mint

from tb.broker.v1 import gateway_pb2 as gw

from .conftest import PAPER_CODE, run
from .test_grpc import order_request

KEY = Ed25519PrivateKey.generate()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def secrets_dir(tmp_path: Path) -> Path:
    raw = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    (tmp_path / "svc_execution_ed25519.pub").write_text(base64.b64encode(raw).decode() + "\n")
    return tmp_path


async def http_status(port: int, path: str) -> int:
    def get() -> int:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as resp:
                return int(resp.status)
        except urllib.error.HTTPError as err:
            return int(err.code)
        except OSError:
            return 0

    return await asyncio.to_thread(get)


async def call(port: int, method: str, request: object, response_type: object) -> object:
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as ch:
        fn = ch.unary_unary(
            f"/{SERVICE_NAME}/{method}",
            request_serializer=type(request).SerializeToString,  # type: ignore[attr-defined]
            response_deserializer=response_type.FromString,  # type: ignore[attr-defined]
        )
        return await fn(request, metadata=[("authorization", SCHEME + mint("execution", AUDIENCE, KEY))])


async def until(predicate_coro: object, timeout: float = 15) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate_coro():  # type: ignore[operator]
            return
        await asyncio.sleep(0.1)
    raise AssertionError("timed out")


def test_entrypoint_paper_session_grpc_and_fail_closed_audit(tmp_path: Path) -> None:
    async def body() -> None:
        wire = WireGateway(managed_accounts=[PAPER_CODE])
        await wire.start()
        ops_port, grpc_port = free_port(), free_port()
        settings = IbkrSettings(
            ibkr_accounts=f"bot={PAPER_CODE}",
            ibkr_host="127.0.0.1",
            ibkr_port=wire.port,
            ibkr_connect_timeout=2,
            ibkr_state_dir=str(tmp_path / "state"),
            ops_host="127.0.0.1",
            ops_port=ops_port,
            grpc_addr=f"127.0.0.1:{grpc_port}",
            secrets_dir=str(secrets_dir(tmp_path)),
        )
        task = asyncio.ensure_future(serve(settings))
        try:

            async def ready() -> bool:
                return await http_status(ops_port, "/readyz") == 200

            await until(ready)
            metrics = await asyncio.to_thread(
                lambda: urllib.request.urlopen(f"http://127.0.0.1:{ops_port}/metrics", timeout=2).read().decode()
            )
            assert 'tb_ibkr_session_ready{connection="orders",environment="paper"} 1.0' in metrics
            accounts = await call(grpc_port, "ListAccounts", gw.ListAccountsRequest(), gw.ListAccountsResponse)
            assert accounts.accounts[0].session.state == gw.SESSION_STATE_READY  # type: ignore[attr-defined]
            # No TB_DB_USER: the audit log is unavailable, so the submit fails and nothing is sent.
            try:
                await call(grpc_port, "SubmitOrder", order_request(), gw.SubmitOrderResponse)
            except grpc.aio.AioRpcError as err:
                assert err.code() is grpc.StatusCode.UNKNOWN
            else:
                raise AssertionError("submit must fail closed without an audit log")
            assert all(msg_id != 3 for msg_id, _ in wire.requests), "no placeOrder reached the gateway"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await wire.stop()

    run(body)


def test_entrypoint_live_without_gates_stays_stopped(tmp_path: Path) -> None:
    async def body() -> None:
        wire = WireGateway(managed_accounts=["U0000001"])
        await wire.start()
        ops_port, grpc_port = free_port(), free_port()
        settings = IbkrSettings(
            ibkr_environment="live",  # type: ignore[arg-type]
            ibkr_accounts="bot=U0000001",
            ibkr_host="127.0.0.1",
            ibkr_port=wire.port,
            ibkr_state_dir=str(tmp_path / "state"),
            ops_host="127.0.0.1",
            ops_port=ops_port,
            grpc_addr=f"127.0.0.1:{grpc_port}",
            secrets_dir=str(secrets_dir(tmp_path)),
            trading_live_enabled=True,
        )
        task = asyncio.ensure_future(serve(settings))
        try:

            async def up() -> bool:
                return await http_status(ops_port, "/healthz") == 200

            await until(up)
            assert await http_status(ops_port, "/readyz") == 503
            req = order_request(broker_account_id="ibkr-live-bot")
            try:
                await call(grpc_port, "SubmitOrder", req, gw.SubmitOrderResponse)
            except grpc.aio.AioRpcError as err:
                assert err.code() is grpc.StatusCode.FAILED_PRECONDITION
                info = error_details_pb2.ErrorInfo()
                status = rpc_status.from_call(err)
                assert status is not None and status.details[0].Unpack(info)
                assert info.metadata["session_state"] == "SESSION_STATE_STOPPED"
            else:
                raise AssertionError("a stopped live adapter must refuse orders")
            assert wire.handshakes == 0, "a stopped live adapter never connects to the gateway"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await wire.stop()

    run(body)
