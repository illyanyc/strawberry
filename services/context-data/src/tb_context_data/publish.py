"""Publishing context versions on the bus (``ctx.<kind>``, stream CONTEXT) and source health.

marketdata's sink persists everything on ``ctx.>`` to the lake (ADR-0032). Each version carries
``Nats-Msg-Id`` = :attr:`Out.msg_id`, so producer retries inside the stream's duplicate window are
dropped and later repeats are removed by compaction (CX-003).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from tb.marketdata.v1 import marketdata_pb2
from tb_context_data.records import Out

log = logging.getLogger("context-data")


class Publisher(Protocol):
    async def publish(self, versions: Sequence[Out]) -> int: ...

    async def health(self, source: str, last_success_age_ms: int, error_rate: float, state: str) -> None: ...


class NatsPublisher:
    def __init__(self, js: Any, nc: Any) -> None:
        self.js = js
        self.nc = nc

    async def publish(self, versions: Sequence[Out]) -> int:
        now = time.time_ns()
        for v in versions:
            await self.js.publish(
                f"ctx.{v.kind}",
                v.event(now).SerializeToString(),
                stream="CONTEXT",
                headers={"Nats-Msg-Id": v.msg_id, "Tb-Correlation-Id": v.msg_id},
            )
        return len(versions)

    async def health(self, source: str, last_success_age_ms: int, error_rate: float, state: str) -> None:
        msg = marketdata_pb2.SourceHealth(
            source=source,
            last_message_age_ms=last_success_age_ms,
            error_rate=error_rate,
            breaker_state=state,
            as_of=time.time_ns(),
        )
        await self.nc.publish(f"sys.health.ctx.{source}", msg.SerializeToString())


@dataclass
class MemoryPublisher:
    """Collects versions (tests and dry runs)."""

    published: list[Out] = field(default_factory=list)
    healths: list[tuple[str, int, float, str]] = field(default_factory=list)

    async def publish(self, versions: Sequence[Out]) -> int:
        self.published.extend(versions)
        return len(versions)

    async def health(self, source: str, last_success_age_ms: int, error_rate: float, state: str) -> None:
        self.healths.append((source, last_success_age_ms, error_rate, state))
