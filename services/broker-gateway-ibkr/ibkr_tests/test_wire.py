"""The real ``ib_async`` client against the socket-level fake gateway (real time, loopback only).

Exercises IbAsyncPort and the supervisor end to end: handshake and account discovery, resync
requests, heartbeats, 1100/1102 handling, gateway restart, and a gateway whose API is closed
while IBC waits for login (IB-010..IB-012, IB-016).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable

from tb_broker_ibkr.adapter import AdapterConfig, IbkrAdapter
from tb_broker_ibkr.backoff import Backoff
from tb_broker_ibkr.clock import SystemClock
from tb_broker_ibkr.cursor_store import CursorStore
from tb_broker_ibkr.events import RecordingSink
from tb_broker_ibkr.fakes.wire import WireGateway
from tb_broker_ibkr.ib_async_port import IbAsyncPort
from tb_broker_ibkr.instruments import StaticInstrumentResolver
from tb_broker_ibkr.models import Environment, SessionState
from tb_broker_ibkr.port import PortConnectError

from .conftest import AAPL, ACCT, PAPER_CODE, run


async def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.05)


def test_ib_async_port_handshake_and_queries() -> None:
    async def body() -> None:
        gw = WireGateway(managed_accounts=[PAPER_CODE])
        await gw.start()
        port = IbAsyncPort("127.0.0.1", gw.port, client_id=1, connect_timeout=3)
        try:
            assert await port.connect() == [PAPER_CODE]
            assert port.is_connected()
            await port.current_time()
            assert await port.open_orders() == []
            assert await port.completed_orders() == []
            assert await port.executions(None) == []
            assert await port.positions() == []
            assert await port.account_values(PAPER_CODE) == []
            seen = {msg_id for msg_id, _ in gw.requests}
            assert {71, 16, 99, 7, 61, 62, 49} <= seen
            start_api = next(f for m, f in gw.requests if m == 71)
            assert start_api[2] == "1", "client ID 1 is the gateway's master client (IB-017)"
        finally:
            port.disconnect()
            await gw.stop()

    run(body)


def test_ib_async_port_reports_closed_api_as_connect_error() -> None:
    async def body() -> None:
        gw = WireGateway(accepting_api=False)
        await gw.start()
        port = IbAsyncPort("127.0.0.1", gw.port, client_id=1, connect_timeout=1)
        try:
            try:
                await port.connect()
            except PortConnectError:
                pass
            else:
                raise AssertionError("expected PortConnectError")
            assert not port.is_connected()
        finally:
            await gw.stop()

    run(body)


def test_supervisor_over_real_ib_async_session_lifecycle() -> None:
    async def body() -> None:
        gw = WireGateway(managed_accounts=[PAPER_CODE])
        await gw.start()
        sink = RecordingSink()
        port = IbAsyncPort("127.0.0.1", gw.port, client_id=1, connect_timeout=1)
        adapter = IbkrAdapter(
            config=AdapterConfig(
                environment=Environment.PAPER,
                accounts={ACCT: PAPER_CODE},
                heartbeat_interval=0.2,
                heartbeat_timeout=0.5,
            ),
            port=port,
            clock=SystemClock(),
            sink=sink,
            resolver=StaticInstrumentResolver([AAPL]),
            cursor=CursorStore(),
        )
        adapter.supervisor._backoff = Backoff(base=0.2, cap=0.5, rng=random.Random(3))  # noqa: S311
        task = asyncio.ensure_future(adapter.run())
        sup = adapter.supervisor
        try:
            await wait_for(lambda: sup.is_ready)
            assert sink.reconciliations[-1].broker_account_id == ACCT

            gw.send_error(1100, "Connectivity between IB and Trader Workstation has been lost.")
            await wait_for(lambda: sup.state is SessionState.DEGRADED)
            gw.send_error(1102, "Connectivity between IB and Trader Workstation has been restored - data maintained.")
            await wait_for(lambda: sup.is_ready)

            gw.send_error(2104, "Market data farm connection is OK:usfarm")  # informational only
            await asyncio.sleep(0.3)
            assert sup.is_ready

            # Gateway restart while IBC waits for login: API closed, then back.
            gw.accepting_api = False
            gw.drop_all()
            await wait_for(lambda: sup.state is not SessionState.READY)
            await asyncio.sleep(1.5)
            assert not sup.is_ready
            gw.accepting_api = True
            await wait_for(lambda: sup.is_ready, timeout=15)
            assert gw.handshakes >= 2

            # A gateway that stops answering heartbeats is dropped and redialled.
            gw.answer_heartbeats = False
            await wait_for(lambda: any("heartbeat_timeout" in s.degraded_reasons for s in sink.statuses))
            gw.answer_heartbeats = True
            await wait_for(lambda: sup.is_ready, timeout=15)
        finally:
            adapter.stop()
            port.disconnect()
            await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await gw.stop()

    run(body)
