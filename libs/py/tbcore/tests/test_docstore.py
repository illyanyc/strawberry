"""Document store and audit sink: memory and Postgres behave alike; research records are append-only
(ADR-0037, EX-I1, SE-020). The Postgres cases run when ``TB_TEST_PG_SUPER_DSN`` points at a throwaway
server (CI provides one) and apply the real migrations from ``libs/go/db/migrations``."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.audit import MemoryAudit, PostgresAudit
from tbcore.docstore import DocStore, MemoryDocStore, PostgresDocStore

SUPER = os.environ.get("TB_TEST_PG_SUPER_DSN", "")
MIGRATIONS = Path(__file__).resolve().parents[4] / "libs" / "go" / "db" / "migrations"


@pytest.fixture
def pg_dsn() -> Iterator[str]:
    if not SUPER:
        pytest.skip("TB_TEST_PG_SUPER_DSN not set (Postgres integration tests)")
    name = f"tb_research_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(SUPER, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        for role in (
            "svc_ml",
            "svc_optimizer",
            "svc_holdout",
            "research_owner",
            "holdout_owner",
            "audit_owner",
            "audit_writer",
            "audit_reader",
        ):
            exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            login = "LOGIN PASSWORD 'pw'" if role.startswith("svc_") else "NOLOGIN"
            admin.execute(f'{"ALTER" if exists else "CREATE"} ROLE "{role}" {login}')
        admin.execute('GRANT "audit_writer" TO "svc_optimizer"')
    base, _, query = SUPER.partition("?")
    root = base.rsplit("/", 1)[0]
    with psycopg.connect(f"{root}/{name}?{query}", autocommit=True) as db:
        for schema, owner in (("audit", "audit_owner"), ("research", "research_owner"), ("holdout", "holdout_owner")):
            db.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{owner}"')
            for sql in sorted((MIGRATIONS / schema).glob("*.up.sql")):
                db.execute(f'SET ROLE "{owner}"')
                db.execute(sql.read_text())
                db.execute("RESET ROLE")
    scheme, rest = root.split("//", 1)
    host = rest.split("@", 1)[1]
    yield f"{scheme}//svc_optimizer:pw@{host}/{name}?{query}"
    with psycopg.connect(SUPER, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _exercise(store: DocStore) -> None:
    t1 = op.Trial(trial_id="t1", lineage_id="L", status=op.TRIAL_STATUS_RUNNING)
    store.put("trial", "t1", t1, lineage_id="L", parent_id="s1", created_at=1)
    store.put("trial", "t2", op.Trial(trial_id="t2", lineage_id="L"), lineage_id="L", parent_id="s2", created_at=2)
    store.put("trial", "t3", op.Trial(trial_id="t3", lineage_id="M"), lineage_id="M", created_at=3)
    t1.status = op.TRIAL_STATUS_ABANDONED
    store.put("trial", "t1", t1, lineage_id="L", parent_id="s1")
    got = store.get("trial", "t1", op.Trial)
    assert got is not None and got.status == op.TRIAL_STATUS_ABANDONED
    assert [t.trial_id for t in store.list("trial", op.Trial, lineage_id="L")] == ["t1", "t2"]
    assert [t.trial_id for t in store.list("trial", op.Trial, parent_id="s2")] == ["t2"]
    assert store.count("trial", lineage_id="L") == 2 and store.count("trial") == 3
    assert store.get("trial", "nope", op.Trial) is None


def test_ex_i1_memory_docstore() -> None:
    _exercise(MemoryDocStore())


def test_ex_i1_postgres_docstore_and_append_only(pg_dsn: str) -> None:
    store = PostgresDocStore(pg_dsn, "research")
    _exercise(store)
    with (
        psycopg.connect(pg_dsn, autocommit=True) as c,
        pytest.raises(psycopg.Error, match=r"permission denied|append-only"),
    ):
        c.execute("DELETE FROM research.docs")


def test_se020_audit_sinks(pg_dsn: str) -> None:
    mem = MemoryAudit()
    mem.append("owner", "optimizer.promote", "cand-1", {"to": "paper"})
    assert mem.actions("optimizer.promote")[0].target == "cand-1"
    pg = PostgresAudit(pg_dsn)
    r = pg.append("analyst", "optimizer.denied", "cand-1", {"reason": "level"})
    with psycopg.connect(SUPER.rsplit("/", 1)[0] + "/" + pg_dsn.rsplit("/", 1)[1], autocommit=True) as c:
        row = c.execute("SELECT actor, action, target FROM audit.events WHERE event_id = %s", (r.event_id,)).fetchone()
    assert row == ("analyst", "optimizer.denied", "cand-1")
