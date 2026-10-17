"""Injectable clock so session timing (backoff, maintenance windows, timeouts) is testable."""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(seconds, 0))


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_ns(ts: datetime) -> int:
    """Exact UTC nanoseconds; naive datetimes are taken as UTC."""
    aware = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return (aware - _EPOCH) // timedelta(microseconds=1) * 1000


async def with_timeout[T](clock: Clock, aw: Awaitable[T], seconds: float) -> T:
    """Await ``aw`` but raise :class:`TimeoutError` after ``seconds`` of *clock* time."""
    task: asyncio.Future[T] = asyncio.ensure_future(aw)
    timer: asyncio.Future[None] = asyncio.ensure_future(clock.sleep(seconds))
    try:
        waiting: set[asyncio.Future[Any]] = {task, timer}
        done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return task.result()
        task.cancel()
        raise TimeoutError(f"timed out after {seconds}s")
    finally:
        timer.cancel()


class FakeClock:
    """Virtual time for tests: ``sleep`` blocks until :meth:`advance` moves time past its deadline."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        self._now = start.astimezone(UTC)
        self._sleepers: list[tuple[datetime, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._now + timedelta(seconds=seconds), next(self._seq), fut))
        await fut

    async def settle(self, rounds: int = 20) -> None:
        """Let ready tasks run without moving time."""
        for _ in range(rounds):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        """Move time forward by ``seconds``, waking sleepers in deadline order."""
        target = self._now + timedelta(seconds=seconds)
        await self.settle()
        while self._sleepers and self._sleepers[0][0] <= target:
            deadline, _, fut = heapq.heappop(self._sleepers)
            self._now = max(self._now, deadline)
            if not fut.done():
                fut.set_result(None)
            await self.settle()
        self._now = target
        await self.settle()

    async def run_until(self, predicate: object, timeout: float, step: float = 1.0) -> bool:
        """Advance in ``step`` increments until ``predicate()`` is true or ``timeout`` elapses."""
        assert callable(predicate)
        elapsed = 0.0
        while elapsed <= timeout:
            if predicate():
                return True
            await self.advance(step)
            elapsed += step
        return bool(predicate())
