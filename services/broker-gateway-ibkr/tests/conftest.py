"""Shared fixtures: virtual clock, fake gateway, adapter wired to a recording sink."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from tb_broker_ibkr.adapter import AdapterConfig, IbkrAdapter
from tb_broker_ibkr.audit import MemoryAuditLog
from tb_broker_ibkr.clock import FakeClock
from tb_broker_ibkr.cursor_store import CursorStore
from tb_broker_ibkr.events import EventSink, FanoutSink, RecordingSink
from tb_broker_ibkr.fakes.gateway import FakeGateway
from tb_broker_ibkr.instruments import StaticInstrumentResolver
from tb_broker_ibkr.models import Environment, Instrument, OrderRequest, OrderType, SessionState, Side, TimeInForce
from tb_broker_ibkr.schedule import ET

# Fake identifiers only (no real account data in fixtures).
PAPER_CODE = "DU0000001"
ACCT = "ibkr-paper-bot"
ULID = "01J9ZK3M4N5P6Q7R8S9T0V1W2X"
AAPL = Instrument(instrument_id="inst-aapl", con_id=265598, symbol="AAPL", primary_exchange="NASDAQ")
SPY = Instrument(instrument_id="inst-spy", con_id=756733, symbol="SPY", primary_exchange="ARCA")

# A Tuesday at 10:00 ET: regular hours, outside every maintenance window.
TUESDAY_10_ET = datetime(2026, 10, 13, 10, 0, tzinfo=ET)


def cid(short_id: str = "swing01", attempt: int = 0, ulid: str = ULID) -> str:
    return f"tb1-{short_id}-{ulid}-{attempt}"


def order(client_order_id: str | None = None, **changes: Any) -> OrderRequest:
    base: dict[str, Any] = {
        "client_order_id": cid() if client_order_id is None else client_order_id,
        "broker_account_id": ACCT,
        "instrument_id": AAPL.instrument_id,
        "side": Side.BUY,
        "qty": Decimal(10),
        "order_type": OrderType.LIMIT,
        "limit_price": Decimal("180.25"),
        "tif": TimeInForce.DAY,
    }
    base.update(changes)
    return OrderRequest(**base)


@dataclass
class Harness:
    clock: FakeClock
    gateway: FakeGateway
    sink: RecordingSink
    adapter: IbkrAdapter
    cursor: CursorStore
    audit: MemoryAuditLog
    task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.task = asyncio.ensure_future(self.adapter.run())
        await self.clock.settle()

    async def until_ready(self, timeout: float = 120) -> None:
        assert await self.clock.run_until(lambda: self.adapter.supervisor.is_ready, timeout), (
            f"not ready: {self.adapter.supervisor.state} {self.adapter.supervisor.reasons}"
        )

    async def until(self, predicate: Callable[[], bool], timeout: float = 120, step: float = 1.0) -> None:
        assert await self.clock.run_until(predicate, timeout, step)

    async def drive[T](self, aw: Coroutine[Any, Any, T], max_seconds: float = 120, step: float = 0.5) -> T:
        """Run ``aw`` while advancing virtual time (for calls that wait on clock timeouts)."""
        task = asyncio.ensure_future(aw)
        elapsed = 0.0
        await self.clock.settle()
        while not task.done():
            if elapsed > max_seconds:
                task.cancel()
                raise AssertionError(f"call did not finish within {max_seconds}s of virtual time")
            await self.clock.advance(step)
            elapsed += step
        return task.result()

    async def stop(self) -> None:
        self.adapter.stop()
        await self.clock.advance(1)
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    @property
    def state(self) -> SessionState:
        return self.adapter.supervisor.state


def make_harness(
    *,
    start: datetime = TUESDAY_10_ET,
    managed: list[str] | None = None,
    accounts: dict[str, str] | None = None,
    environment: Environment = Environment.PAPER,
    cursor: CursorStore | None = None,
    gateway: FakeGateway | None = None,
    clock: FakeClock | None = None,
    extra_sinks: list[EventSink] | None = None,
    **config: Any,
) -> Harness:
    clock = clock or FakeClock(start)
    gateway = gateway or FakeGateway(clock, managed or [PAPER_CODE])
    sink = RecordingSink()
    cursor = cursor if cursor is not None else CursorStore()
    audit = MemoryAuditLog()
    adapter = IbkrAdapter(
        config=AdapterConfig(environment=environment, accounts=accounts or {ACCT: PAPER_CODE}, **config),
        port=gateway.port(1),
        clock=clock,
        sink=FanoutSink(sink, *(extra_sinks or [])) if extra_sinks else sink,
        resolver=StaticInstrumentResolver([AAPL, SPY]),
        cursor=cursor,
        audit=audit,
    )
    return Harness(clock=clock, gateway=gateway, sink=sink, adapter=adapter, cursor=cursor, audit=audit)


def run(coro_fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(coro_fn())


@pytest.fixture
def harness_factory() -> Callable[..., Harness]:
    return make_harness
