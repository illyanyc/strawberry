# mypy: disable-error-code="no-untyped-call,attr-defined"
"""Postgres journal and audit sink against the real `analyst` and `audit` migrations, as `svc_assistant`
(AN-003, AN-055, SE-032). Runs when ``TB_TEST_PG_SUPER_DSN`` points at a throwaway server (CI provides one)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import psycopg
import pytest
from tb_assistant.audit import AuditRecord, PgAudit
from tb_assistant.guarded import LLMCallRecord
from tb_assistant.journal import LoopRecord, PgJournal
from tb_assistant.tools import ToolCallRecord

from tb.analyst.v1 import analyst_pb2

SUPER = os.environ.get("TB_TEST_PG_SUPER_DSN", "")
MIGRATIONS = Path(__file__).resolve().parents[3] / "libs" / "go" / "db" / "migrations"
NOW = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)


@pytest.fixture
def pg() -> Iterator[tuple[str, str]]:
    """A throwaway database with the analyst and audit schemas; yields (svc_assistant DSN, superuser DSN)."""
    if not SUPER:
        pytest.skip("TB_TEST_PG_SUPER_DSN not set (Postgres integration tests)")
    name = f"tb_analyst_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(SUPER, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        for role in ("svc_assistant", "analyst_owner", "audit_owner", "audit_writer", "audit_reader"):
            exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            login = "LOGIN PASSWORD 'pw'" if role.startswith("svc_") else "NOLOGIN"
            admin.execute(f'{"ALTER" if exists else "CREATE"} ROLE "{role}" {login}')
        admin.execute('GRANT "audit_writer" TO "svc_assistant"')
    base, _, query = SUPER.partition("?")
    root = base.rsplit("/", 1)[0]
    db_dsn = f"{root}/{name}" + (f"?{query}" if query else "")
    with psycopg.connect(db_dsn, autocommit=True) as db:
        for schema, owner in (("audit", "audit_owner"), ("analyst", "analyst_owner")):
            db.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{owner}"')
            for sql in sorted((MIGRATIONS / schema).glob("*.up.sql")):
                db.execute(f'SET ROLE "{owner}"')
                db.execute(sql.read_text())
                db.execute("RESET ROLE")
    host = root.split("//", 1)[1].split("@", 1)[1]
    try:
        yield f"postgresql://svc_assistant:pw@{host}/{name}", db_dsn
    finally:
        with psycopg.connect(SUPER, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _llm(call_id: str, cost: float, at: datetime) -> LLMCallRecord:
    return LLMCallRecord(
        call_id=call_id,
        role="planner",
        provider="fake",
        model="heuristic-v1",
        prompt_sha256="0" * 64,
        prompt='{"masked": "I001 D-3"}',
        response="{}",
        input_tokens=10,
        output_tokens=5,
        cost_usd=cost,
        latency_ms=1.0,
        ok=True,
        error="",
        lineage_id="L1",
        round_id="R1",
        created_at=at,
    )


async def test_an003_an055_pg_journal_round_trips_as_svc_assistant(pg: tuple[str, str]) -> None:
    pool = await asyncpg.create_pool(pg[0], min_size=1, max_size=2)
    try:
        j = PgJournal(pool)
        assert await j.mark_trigger("ml.run.completed:r1") and not await j.mark_trigger("ml.run.completed:r1")

        rnd = analyst_pb2.AnalystRound(round_id="R1", lineage_id="L1", seq=1, started_at=1, summary="s")
        rnd.decision = analyst_pb2.ROUND_DECISION_CONTINUE
        await j.save_round(rnd)
        rnd.decision, rnd.completed_at = analyst_pb2.ROUND_DECISION_PLATEAU, 2
        await j.save_round(rnd)
        got = await j.get_round("R1")
        assert got is not None and got.decision == analyst_pb2.ROUND_DECISION_PLATEAU and got.summary == "s"
        assert [r.round_id for r in await j.list_rounds("L1")] == ["R1"]
        assert await j.list_rounds("L1", decision=analyst_pb2.ROUND_DECISION_CONTINUE) == []

        rec = LoopRecord(lineage_id="L1", rounds=3, pending={"job1": ["R1", "A1"]}, best_dsr_history=[0.4, 0.5])
        await j.save_loop(rec)
        assert await j.get_loop("L1") == rec
        assert [x.lineage_id for x in await j.list_loops()] == ["L1"]

        await j.record_llm_call(_llm("c1", 0.25, NOW))
        await j.record_llm_call(_llm("c2", 0.50, NOW - timedelta(days=2)))
        assert await j.llm_spend(NOW - timedelta(days=1)) == pytest.approx(0.25)
        await j.record_tool_call(ToolCallRecord("get_run", "L1", "R1", 1, "ok", "a" * 64, "b" * 64, "", NOW))

        p = analyst_pb2.CodeProposal(proposal_id="P1", lineage_id="L1")
        await j.save_proposal(p)
        assert [x.proposal_id for x in await j.list_proposals("L1")] == ["P1"]

        conv = analyst_pb2.Conversation(conversation_id="C1")
        await j.save_conversation(conv)
        assert (await j.get_conversation("C1")) is not None
        assert await j.purge_conversations(datetime.now(tz=UTC) + timedelta(seconds=5)) == 1

        async with pool.acquire() as c:
            # AN-055: the call logs are append-only, even for their writer.
            with pytest.raises(asyncpg.PostgresError):
                await c.execute("DELETE FROM analyst.llm_calls WHERE call_id = 'c1'")
            with pytest.raises(asyncpg.PostgresError):
                await c.execute("UPDATE analyst.tool_calls SET outcome = 'denied'")
            # SE-032: the audit log is reachable only through audit.append.
            with pytest.raises(asyncpg.PostgresError):
                await c.fetch("SELECT * FROM audit.events")
    finally:
        await pool.close()


async def test_se020_pg_audit_appends_through_audit_append(pg: tuple[str, str]) -> None:
    pool = await asyncpg.create_pool(pg[0], min_size=1, max_size=2)
    try:
        await PgAudit(pool).append(
            AuditRecord("analyst", "analyst.round.completed", "L1", {"round_id": "R1", "decision": "PLATEAU"}, "R1")
        )
    finally:
        await pool.close()
    with psycopg.connect(pg[1]) as db:
        rows = db.execute("SELECT actor, action, target FROM audit.events").fetchall()
    assert rows == [("analyst", "analyst.round.completed", "L1")]
