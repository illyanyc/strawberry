"""Durable record of what the adapter has published (IB-031, BR-008, ADR-0023).

Two implementations of :class:`Journal`:

* :class:`~tb_broker_ibkr.cursor_store.CursorStore` / ``FileCursorStore``: dedupe cursor only;
  ``publishes`` is False, so the event sinks publish to NATS directly (at-least-once).
* :class:`PostgresJournal`: schema ``broker_ibkr``; each new execution, distinct order event,
  snapshot, and reconciliation request is written with its outbox row in one transaction, and
  :class:`~tb_broker_ibkr.relay.OutboxRelay` publishes the outbox to JetStream with
  ``Nats-Msg-Id`` = the row's ``msg_id`` (exactly once by key). ``publishes`` is True.

Message IDs and payloads match the Go adapters (``services/broker-gateway/internal/execstore``):
``exec:<acct>:<execId>`` (``tb.orders.v1.ExecutionReport``), ``ord:<acct>:<key>``
(``tb.broker.v1.BrokerOrderUpdate``), ``acct:<acct>:<as_of>`` (``tb.ledger.v1.AccountSnapshot``),
``recon:<correlation_id>`` (``tb.ledger.v1.ReconciliationRequest``).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import asyncpg

from tb.ledger.v1 import ledger_pb2 as ledger
from tb_broker_ibkr import codec
from tb_broker_ibkr.events import ReconciliationRequest
from tb_broker_ibkr.models import AccountSnapshot, BrokerOrder, Environment, ExecutionReport

_NS_PER_DAY = 86_400 * 1_000_000_000


class Journal(Protocol):
    # True when durable bus publication happens through this journal's outbox.
    publishes: bool
    last_ready_at: int

    def seen(self, account: str, exec_id: str) -> bool: ...

    def seen_with_base(self, account: str, base: str) -> list[str]: ...

    def last_exec_time(self, account: str) -> int: ...

    async def record_execution(self, report: ExecutionReport) -> bool:
        """Record (and, if ``publishes``, enqueue) a new execution; False if already recorded."""
        ...

    async def record_order(self, order: BrokerOrder) -> None: ...

    async def record_snapshot(self, snapshot: AccountSnapshot) -> None: ...

    async def record_reconciliation(self, request: ReconciliationRequest) -> None: ...

    async def mark_ready(self, when: int) -> None: ...


class PostgresJournal:
    """``broker_ibkr`` executions/order events/outbox as ``svc_broker_gateway_ibkr``."""

    publishes = True

    def __init__(
        self,
        pool: asyncpg.Pool,
        environment: Environment,
        retention_days: int = 8,
        kick: Callable[[], None] = lambda: None,
    ) -> None:
        self._pool = pool
        self._env = environment
        self._retention_ns = retention_days * _NS_PER_DAY
        self._kick = kick
        self._seen: dict[str, dict[str, int]] = {}
        self.last_ready_at = 0

    def set_kick(self, kick: Callable[[], None]) -> None:
        self._kick = kick

    async def load(self) -> None:
        """Warm the in-memory dedupe cache; the table's primary key stays the authority."""
        async with self._pool.acquire() as conn:  # type: ignore[no-untyped-call]
            rows = await conn.fetch(
                "SELECT broker_account_id, broker_exec_id, event_time FROM broker_ibkr.executions e "
                "WHERE event_time >= (SELECT max(event_time) FROM broker_ibkr.executions x "
                "WHERE x.broker_account_id = e.broker_account_id) - $1",
                self._retention_ns,
            )
            ready = await conn.fetchval("SELECT value FROM broker_ibkr.state WHERE key = 'last_ready_at'")
        for r in rows:
            self._seen.setdefault(r["broker_account_id"], {})[r["broker_exec_id"]] = int(r["event_time"])
        self.last_ready_at = int(ready or 0)

    def seen(self, account: str, exec_id: str) -> bool:
        return exec_id in self._seen.get(account, {})

    def seen_with_base(self, account: str, base: str) -> list[str]:
        prefix = base + "."
        return [e for e in self._seen.get(account, {}) if e.startswith(prefix)]

    def last_exec_time(self, account: str) -> int:
        return max(self._seen.get(account, {}).values(), default=0)

    async def _tx_insert_then_enqueue(
        self, insert_sql: str, args: tuple[Any, ...], outbox: tuple[str, str, bytes]
    ) -> bool:
        async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[no-untyped-call]
            inserted = await conn.fetchval(insert_sql, *args)
            if inserted is None:
                return False
            await conn.execute("INSERT INTO broker_ibkr.outbox (subject, msg_id, payload) VALUES ($1, $2, $3)", *outbox)
        self._kick()
        return True

    async def record_execution(self, report: ExecutionReport) -> bool:
        acct = report.broker_account_id
        payload = codec.execution_proto(report, self._env).SerializeToString()
        new = await self._tx_insert_then_enqueue(
            """INSERT INTO broker_ibkr.executions (broker_account_id, broker_exec_id, broker_order_id, client_order_id,
                   perm_id, symbol, instrument_id, side, qty, price, fees, event_time, payload)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::numeric,$10::numeric,$11::numeric,$12,$13)
               ON CONFLICT DO NOTHING RETURNING 1""",
            (
                acct,
                report.broker_exec_id,
                report.broker_order_id,
                report.client_order_id,
                report.broker_order_id,
                report.symbol,
                report.instrument_id,
                report.side.value,
                str(report.qty),
                str(report.price),
                None if report.fees is None else str(report.fees),
                report.event_time,
                payload,
            ),
            (f"exec.{codec.subject_token(acct)}", f"exec:{acct}:{report.broker_exec_id}", payload),
        )
        self._seen.setdefault(acct, {})[report.broker_exec_id] = report.event_time
        return new

    async def record_order(self, order: BrokerOrder) -> None:
        acct = order.broker_account_id
        key = codec.order_dedupe_key(order)
        payload = codec.order_update_proto(order).SerializeToString()
        await self._tx_insert_then_enqueue(
            """INSERT INTO broker_ibkr.order_events (broker_account_id, dedupe_key, broker_order_id, client_order_id,
                   broker_status, state, filled_qty, event_time, payload)
               VALUES ($1,$2,$3,$4,$5,$6,$7::numeric,$8,$9) ON CONFLICT DO NOTHING RETURNING 1""",
            (
                acct,
                key,
                order.broker_order_id,
                order.client_order_id,
                order.raw_status,
                order.state.value,
                str(order.filled_qty),
                order.updated_at,
                payload,
            ),
            (f"ord.update.{codec.subject_token(acct)}", f"ord:{acct}:{key}", payload),
        )

    async def _enqueue(self, subject: str, msg_id: str, payload: bytes, correlation_id: str = "") -> None:
        async with self._pool.acquire() as conn:  # type: ignore[no-untyped-call]
            await conn.execute(
                "INSERT INTO broker_ibkr.outbox (subject, msg_id, payload, correlation_id) VALUES ($1, $2, $3, $4)",
                subject,
                msg_id,
                payload,
                correlation_id,
            )
        self._kick()

    async def record_snapshot(self, snapshot: AccountSnapshot) -> None:
        acct = snapshot.broker_account_id
        payload = codec.snapshot_proto(snapshot, self._env).SerializeToString()
        await self._enqueue(f"acct.snapshot.{codec.subject_token(acct)}", f"acct:{acct}:{snapshot.as_of}", payload)

    async def record_reconciliation(self, request: ReconciliationRequest) -> None:
        corr = uuid.uuid4().hex
        msg = ledger.ReconciliationRequest(
            broker_account_id=request.broker_account_id,
            reason=request.reason,
            requested_at=time.time_ns(),
            correlation_id=corr,
        )
        await self._enqueue(
            f"recon.request.{codec.subject_token(request.broker_account_id)}",
            f"recon:{corr}",
            msg.SerializeToString(),
            corr,
        )

    async def mark_ready(self, when: int) -> None:
        async with self._pool.acquire() as conn:  # type: ignore[no-untyped-call]
            await conn.execute(
                """INSERT INTO broker_ibkr.state (key, value) VALUES ('last_ready_at', $1)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                when,
            )
        self.last_ready_at = when


class PostgresLiveEnablement:
    """IB-007 / BR-013: accounts with an enabled ``broker.live_enablement`` row (audited owner action)."""

    def __init__(self, enabled: set[str], orders: set[str] | None = None) -> None:
        self._enabled = enabled
        self._orders = enabled if orders is None else orders

    @classmethod
    async def load(cls, pool: asyncpg.Pool) -> PostgresLiveEnablement:
        async with pool.acquire() as conn:  # type: ignore[no-untyped-call]
            rows = await conn.fetch(
                "SELECT broker_account_id, orders_enabled FROM broker.live_enablement WHERE enabled AND audit_seq > 0"
            )
        enabled = {r["broker_account_id"] for r in rows}
        orders = {r["broker_account_id"] for r in rows if r["orders_enabled"]}
        return cls(enabled, orders)

    def is_live_enabled(self, broker_account_id: str) -> bool:
        return broker_account_id in self._enabled

    def orders_enabled(self, broker_account_id: str) -> bool:
        return broker_account_id in self._orders
