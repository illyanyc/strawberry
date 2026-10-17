"""Outbox relay for ``broker_ibkr.outbox`` (D2, ADR-0018, ADR-0023): the Python twin of
``libs/go/outbox.Relay``.

Rows are taken in id order with ``FOR UPDATE SKIP LOCKED``, published to JetStream with
``Nats-Msg-Id`` = ``msg_id`` (and ``Tb-Correlation-Id``), and marked ``published_at`` in the same
transaction; publication stops at the first failure to keep per-table order. A crash after publish but
before commit re-publishes with the same ``msg_id``, which JetStream's dedupe window drops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

import asyncpg

log = logging.getLogger("tb_broker_ibkr.relay")


class Publisher(Protocol):
    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None: ...


class OutboxRelay:
    def __init__(
        self,
        pool: asyncpg.Pool,
        publisher: Publisher,
        table: str = "broker_ibkr.outbox",
        interval: float = 0.25,
        batch: int = 200,
    ) -> None:
        if table != "broker_ibkr.outbox":
            raise ValueError(f"unexpected outbox table {table!r}")
        self._pool = pool
        self._pub = publisher
        self._table = table
        self._interval = interval
        self._batch = batch
        self._kick = asyncio.Event()
        self.published = 0
        self.failures = 0

    def kick(self) -> None:
        self._kick.set()

    async def drain(self) -> int:
        """One pass; returns the number of rows published."""
        published = 0
        async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[no-untyped-call]
            rows = await conn.fetch(
                f"SELECT id, subject, msg_id, payload, correlation_id FROM {self._table} "  # noqa: S608 - fixed table
                "WHERE published_at IS NULL ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED",
                self._batch,
            )
            for row in rows:
                headers = {"Nats-Msg-Id": row["msg_id"]}
                if row["correlation_id"]:
                    headers["Tb-Correlation-Id"] = row["correlation_id"]
                try:
                    await self._pub.publish(row["subject"], bytes(row["payload"]), headers)
                except Exception as exc:
                    self.failures += 1
                    await conn.execute(
                        f"UPDATE {self._table} SET attempts = attempts + 1, last_error = $2 WHERE id = $1",  # noqa: S608
                        row["id"],
                        str(exc)[:500],
                    )
                    log.warning("outbox publish failed", extra={"subject": row["subject"], "error": type(exc).__name__})
                    break
                await conn.execute(
                    f"UPDATE {self._table} SET published_at = now(), attempts = attempts + 1 WHERE id = $1",  # noqa: S608
                    row["id"],
                )
                published += 1
        self.published += published
        return published

    async def run(self) -> None:
        while True:
            try:
                while await self.drain() >= self._batch:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("outbox relay pass failed", exc_info=True)
            self._kick.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._kick.wait(), self._interval)
