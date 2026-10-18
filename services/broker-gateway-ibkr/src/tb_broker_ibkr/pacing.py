"""Historical-data pacing and market-data line budget (IB-050, IB-051).

IBKR's documented limits for small bars (<= 30 s), applied to every historical request because
violations silently throttle or disconnect:

* at most 60 requests in any 10 minutes (we schedule at 80%: 48);
* no identical request within 15 seconds;
* at most 6 requests for the same contract, exchange, and data type within 2 seconds;
* at most 50 simultaneously open requests.

The scheduler waits rather than violating a limit. A pacing error from IBKR (code 162 with
"pacing violation", 100, 420) freezes all historical requests for ``penalty`` seconds.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tb_broker_ibkr.clock import Clock
from tb_broker_ibkr.port import HistoricalRequest


@dataclass(frozen=True, slots=True)
class PacingLimits:
    max_requests: int = 48
    window: timedelta = timedelta(minutes=10)
    identical_cooldown: timedelta = timedelta(seconds=15)
    per_contract_max: int = 6
    per_contract_window: timedelta = timedelta(seconds=2)
    max_open: int = 50
    penalty: timedelta = timedelta(seconds=60)


@dataclass
class HistoricalPacer:
    clock: Clock
    limits: PacingLimits = field(default_factory=PacingLimits)
    _sent: deque[datetime] = field(default_factory=deque)
    _identical: dict[HistoricalRequest, datetime] = field(default_factory=dict)
    _per_contract: dict[tuple[int, str, str], deque[datetime]] = field(default_factory=dict)
    _open: int = 0
    _frozen_until: datetime | None = None

    def penalize(self) -> None:
        self._frozen_until = self.clock.now() + self.limits.penalty

    def _wait_needed(self, request: HistoricalRequest, now: datetime) -> float:
        lim = self.limits
        waits = [0.0]
        if self._frozen_until and now < self._frozen_until:
            waits.append((self._frozen_until - now).total_seconds())
        while self._sent and now - self._sent[0] >= lim.window:
            self._sent.popleft()
        if len(self._sent) >= lim.max_requests:
            waits.append((self._sent[0] + lim.window - now).total_seconds())
        last = self._identical.get(request)
        if last is not None and now - last < lim.identical_cooldown:
            waits.append((last + lim.identical_cooldown - now).total_seconds())
        key = (request.con_id, request.exchange, request.what_to_show)
        recent = self._per_contract.setdefault(key, deque())
        while recent and now - recent[0] >= lim.per_contract_window:
            recent.popleft()
        if len(recent) >= lim.per_contract_max:
            waits.append((recent[0] + lim.per_contract_window - now).total_seconds())
        if self._open >= lim.max_open:
            waits.append(0.5)
        return max(waits)

    @asynccontextmanager
    async def slot(self, request: HistoricalRequest) -> AsyncIterator[None]:
        """Wait until ``request`` may be sent without violating any limit, then hold an open slot."""
        while True:
            now = self.clock.now()
            wait = self._wait_needed(request, now)
            if wait <= 0:
                break
            await self.clock.sleep(wait)
        now = self.clock.now()
        self._sent.append(now)
        self._identical[request] = now
        self._per_contract[(request.con_id, request.exchange, request.what_to_show)].append(now)
        self._open += 1
        try:
            yield
        finally:
            self._open -= 1


class MarketDataLineLimitError(RuntimeError):
    """The account's market-data line budget is exhausted (IB-051)."""


@dataclass
class MarketDataLines:
    """Counts streaming subscriptions against the account's line allowance (default 100)."""

    limit: int = 100
    in_use: int = 0

    def acquire(self, n: int = 1) -> None:
        if self.in_use + n > self.limit:
            raise MarketDataLineLimitError(f"{self.in_use}/{self.limit} market data lines in use")
        self.in_use += n

    def release(self, n: int = 1) -> None:
        self.in_use = max(0, self.in_use - n)
