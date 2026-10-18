"""Postgres journal, outbox relay, and live enablement against a real database (IB-031, IB-007,
BR-008, BR-013; ADR-0023). Skipped unless ``TB_TEST_PG_SUPER_DSN`` points at a throwaway server
(CI provides one). Each test gets a fresh database with the real migrations from
``libs/go/db/migrations`` applied as the schema owners.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from tb_broker_ibkr.events import ReconciliationRequest
from tb_broker_ibkr.journal import PostgresJournal, PostgresLiveEnablement
from tb_broker_ibkr.models import Environment, ExecutionReport, OwnerKind, Side
from tb_broker_ibkr.relay import OutboxRelay

from tb.broker.v1 import gateway_pb2 as gw
from tb.ledger.v1 import ledger_pb2 as ledger
from tb.orders.v1 import orders_pb2 as orders

from .conftest import ACCT, cid, make_harness, order, run

SUPER = os.environ.get("TB_TEST_PG_SUPER_DSN", "")
MIGRATIONS = Path(__file__).resolve().parents[3] / "libs" / "go" / "db" / "migrations"
pytestmark = pytest.mark.skipif(not SUPER, reason="TB_TEST_PG_SUPER_DSN not set (Postgres integration tests)")


def report(exec_id: str = "0000e0d5.1.01.01", qty: str = "4") -> ExecutionReport:
    return ExecutionReport(
        broker_account_id=ACCT,
        broker_exec_id=exec_id,
        client_order_id=cid(),
        broker_order_id="1849274651",
        owner=OwnerKind.STRATEGY,
        strategy_short_id="swing01",
        instrument_id="inst-aapl",
        con_id=265598,
        symbol="AAPL",
        side=Side.BUY,
        qty=Decimal(qty),
        price=Decimal("180.25"),
        cum_qty=Decimal(qty),
        fees=Decimal("0.20"),
        event_time=1_760_000_000_000_000_000,
        ingested_at=1_760_000_000_100_000_000,
    )


async def fresh_db() -> tuple[str, str, Callable[[], Awaitable[None]]]:
    """Create a database with the broker and broker_ibkr schemas; return the IBKR role's DSN."""
    name = f"tb_ibkr_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(SUPER)
    await admin.execute(f'CREATE DATABASE "{name}"')
    for role in ("svc_broker_gateway", "svc_broker_gateway_ibkr", "svc_execution", "broker_owner", "broker_ibkr_owner"):
        exists = await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
        login = "LOGIN PASSWORD 'pw'" if role.startswith("svc_") else "NOLOGIN"
        await admin.execute(f'{"ALTER" if exists else "CREATE"} ROLE "{role}" {login}')
    await admin.close()
    base = SUPER.rsplit("/", 1)[0]
    query = "?" + SUPER.split("?", 1)[1] if "?" in SUPER else ""
    db = await asyncpg.connect(f"{base}/{name}{query}")
    for schema, owner in (("broker", "broker_owner"), ("broker_ibkr", "broker_ibkr_owner")):
        await db.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{owner}"')
        for sql in sorted((MIGRATIONS / schema).glob("*.up.sql")):
            await db.execute(f'SET ROLE "{owner}"; {sql.read_text()}; RESET ROLE;')
    await db.close()
    user_dsn = f"{base.split('//', 1)[0]}//svc_broker_gateway_ibkr:pw@{base.split('@', 1)[1]}/{name}{query}"

    async def drop() -> None:
        admin = await asyncpg.connect(SUPER)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

    return user_dsn, f"{base}/{name}{query}", drop


class RecordingPublisher:
    def __init__(self, fail_on: str = "") -> None:
        self.sent: list[tuple[str, bytes, dict[str, str]]] = []
        self.fail_on = fail_on

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        if self.fail_on and subject.startswith(self.fail_on):
            raise ConnectionError("nats down")
        self.sent.append((subject, payload, headers))


ADMIN_DSN: dict[str, str] = {}


def with_db(body: Callable[[asyncpg.Pool], Awaitable[None]]) -> None:
    async def main() -> None:
        dsn, admin_dsn, drop = await fresh_db()
        ADMIN_DSN["current"] = admin_dsn
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        try:
            await body(pool)
        finally:
            await pool.close()
            await drop()

    run(main)


async def outbox_rows(pool: asyncpg.Pool) -> list[Any]:
    async with pool.acquire() as conn:
        return list(
            await conn.fetch("SELECT subject, msg_id, payload, published_at FROM broker_ibkr.outbox ORDER BY id")
        )


def test_br008_execution_recorded_and_enqueued_exactly_once() -> None:
    async def body(pool: asyncpg.Pool) -> None:
        journal = PostgresJournal(pool, Environment.PAPER)
        assert await journal.record_execution(report())
        assert not await journal.record_execution(report())
        rows = await outbox_rows(pool)
        assert [(r["subject"], r["msg_id"]) for r in rows] == [(f"exec.{ACCT}", f"exec:{ACCT}:0000e0d5.1.01.01")]
        msg = orders.ExecutionReport.FromString(bytes(rows[0]["payload"]))
        assert (msg.broker_exec_id, msg.qty.value, msg.fees.value, msg.perm_id) == (
            "0000e0d5.1.01.01",
            "4",
            "0.20",
            "1849274651",
        )
        restarted = PostgresJournal(pool, Environment.PAPER)
        await restarted.load()
        assert restarted.seen(ACCT, "0000e0d5.1.01.01")
        assert restarted.last_exec_time(ACCT) == report().event_time
        assert not await restarted.record_execution(report())

    with_db(body)


def test_order_snapshot_recon_and_ready_marker() -> None:
    async def body(pool: asyncpg.Pool) -> None:
        h = make_harness()
        journal = PostgresJournal(pool, Environment.PAPER)
        await h.start()
        await h.until_ready()
        await h.drive(h.adapter.submit_order(order()))
        live = await h.adapter.get_order(ACCT, client_order_id=cid())
        assert live is not None
        await journal.record_order(live)
        await journal.record_order(live)  # identical event: deduplicated
        await journal.record_snapshot(h.sink.snapshots[-1])
        await journal.record_reconciliation(ReconciliationRequest(ACCT, "reconnect"))
        await journal.mark_ready(42)
        rows = await outbox_rows(pool)
        assert [r["subject"] for r in rows] == [f"ord.update.{ACCT}", f"acct.snapshot.{ACCT}", f"recon.request.{ACCT}"]
        update = gw.BrokerOrderUpdate.FromString(bytes(rows[0]["payload"]))
        assert update.order.client_order_id == cid() and update.order.perm_id
        assert ledger.AccountSnapshot.FromString(bytes(rows[1]["payload"])).broker_account_id == ACCT
        recon = ledger.ReconciliationRequest.FromString(bytes(rows[2]["payload"]))
        assert recon.reason == "reconnect" and rows[2]["msg_id"] == f"recon:{recon.correlation_id}"
        reloaded = PostgresJournal(pool, Environment.PAPER)
        await reloaded.load()
        assert reloaded.last_ready_at == 42
        await h.stop()

    with_db(body)


def test_relay_publishes_in_order_with_dedupe_headers_and_stops_on_failure() -> None:
    async def body(pool: asyncpg.Pool) -> None:
        journal = PostgresJournal(pool, Environment.PAPER)
        for i in range(3):
            await journal.record_execution(report(f"0000e0d5.{i}.01.01"))
        await journal.record_reconciliation(ReconciliationRequest(ACCT, "reconnect"))
        failing = RecordingPublisher(fail_on="recon.")
        relay = OutboxRelay(pool, failing)
        assert await relay.drain() == 3
        assert [h["Nats-Msg-Id"] for _, _, h in failing.sent] == [f"exec:{ACCT}:0000e0d5.{i}.01.01" for i in range(3)]
        rows = await outbox_rows(pool)
        assert [r["published_at"] is not None for r in rows] == [True, True, True, False]
        async with pool.acquire() as conn:
            last_error = await conn.fetchval("SELECT last_error FROM broker_ibkr.outbox WHERE published_at IS NULL")
        assert "nats down" in last_error
        ok = RecordingPublisher()
        assert await OutboxRelay(pool, ok).drain() == 1
        assert ok.sent[0][2]["Tb-Correlation-Id"]

    with_db(body)


def test_two_relays_never_publish_the_same_row() -> None:
    async def body(pool: asyncpg.Pool) -> None:
        journal = PostgresJournal(pool, Environment.PAPER)
        for i in range(40):
            await journal.record_execution(report(f"0000e0d5.{i}.01.01"))
        a, b = RecordingPublisher(), RecordingPublisher()
        await asyncio.gather(OutboxRelay(pool, a, batch=5).drain(), OutboxRelay(pool, b, batch=5).drain())
        while True:
            n = await OutboxRelay(pool, a, batch=10).drain() + await OutboxRelay(pool, b, batch=10).drain()
            if n == 0:
                break
        ids = [h["Nats-Msg-Id"] for _, _, h in a.sent + b.sent]
        assert len(ids) == 40 and len(set(ids)) == 40

    with_db(body)


def test_ib031_adapter_with_postgres_journal_publishes_each_fill_once_across_restarts() -> None:
    async def body(pool: asyncpg.Pool) -> None:
        journal = PostgresJournal(pool, Environment.PAPER)
        h = make_harness(cursor=journal)
        await h.start()
        await h.until_ready()
        ack = await h.drive(h.adapter.submit_order(order()))
        h.gateway.fill(int(ack.broker_order_id), Decimal(10), Decimal("180.25"))
        for _ in range(100):  # the journal write is real I/O, not virtual time
            if any(r["subject"].startswith("exec.") for r in await outbox_rows(pool)):
                break
            await asyncio.sleep(0.02)
        await h.stop()
        restarted = PostgresJournal(pool, Environment.PAPER)
        await restarted.load()
        h2 = make_harness(cursor=restarted, gateway=h.gateway, clock=h.clock)
        await h2.start()
        await h2.until_ready()
        await h2.stop()
        rows = [r for r in await outbox_rows(pool) if r["subject"].startswith("exec.")]
        assert len(rows) == 1, "one execution, one outbox row, across an adapter restart"
        assert h2.sink.executions == []
        recon = [r for r in await outbox_rows(pool) if r["subject"].startswith("recon.request.")]
        assert len(recon) == 2, "each resync requests reconciliation through the outbox"

    with_db(body)


def test_ib007_live_enablement_from_audited_rows() -> None:
    async def body(pool: asyncpg.Pool) -> None:
        dsn_admin = await asyncpg.connect(ADMIN_DSN["current"])
        try:
            await dsn_admin.execute(
                "INSERT INTO broker.live_enablement (broker_account_id, enabled, enabled_by, audit_seq) VALUES "
                "('ibkr-live-bot', true, 'owner', 7), ('ibkr-live-off', false, 'owner', 8), "
                "('ibkr-live-noaudit', true, 'x', 0)"
            )
        finally:
            await dsn_admin.close()
        gate = await PostgresLiveEnablement.load(pool)
        assert gate.is_live_enabled("ibkr-live-bot")
        assert not gate.is_live_enabled("ibkr-live-off")
        assert not gate.is_live_enabled("ibkr-live-noaudit")

    with_db(body)
