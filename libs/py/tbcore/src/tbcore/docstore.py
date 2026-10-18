"""Protobuf document store for research records (ADR-0037): runs, jobs, lineages, cycles, studies, trials,
candidates, gate reports, and holdout records, each stored as its contract message.

One table per schema (``research.docs``, ``holdout.docs``) keyed by (kind, id) with lineage and parent
columns for listing. There is no delete: the trial ledger only grows (EX-I1), and the database forbids
deletes (migration trigger). ``MemoryDocStore`` has the same semantics for tests and the dev profile.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol, TypeVar

import psycopg
from google.protobuf.message import Message

M = TypeVar("M", bound=Message)


class DocStore(Protocol):
    def put(
        self, kind: str, doc_id: str, msg: Message, lineage_id: str = "", parent_id: str = "", created_at: int = 0
    ) -> None: ...

    def get(self, kind: str, doc_id: str, cls: type[M]) -> M | None: ...

    def list(
        self, kind: str, cls: type[M], lineage_id: str | None = None, parent_id: str | None = None, limit: int = 0
    ) -> list[M]: ...

    def count(self, kind: str, lineage_id: str | None = None) -> int: ...


class MemoryDocStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[str, str, int, bytes]] = {}
        self._lock = threading.Lock()

    def put(
        self, kind: str, doc_id: str, msg: Message, lineage_id: str = "", parent_id: str = "", created_at: int = 0
    ) -> None:
        with self._lock:
            prev = self._rows.get((kind, doc_id))
            ts = prev[2] if prev else (created_at or time.time_ns())
            self._rows[(kind, doc_id)] = (lineage_id, parent_id, ts, msg.SerializeToString())

    def get(self, kind: str, doc_id: str, cls: type[M]) -> M | None:
        with self._lock:
            r = self._rows.get((kind, doc_id))
        return cls.FromString(r[3]) if r else None

    def list(
        self, kind: str, cls: type[M], lineage_id: str | None = None, parent_id: str | None = None, limit: int = 0
    ) -> list[M]:
        with self._lock:
            rows = [
                (v[2], k[1], v[3])
                for k, v in self._rows.items()
                if k[0] == kind
                and (lineage_id is None or v[0] == lineage_id)
                and (parent_id is None or v[1] == parent_id)
            ]
        rows.sort()
        if limit:
            rows = rows[-limit:]
        return [cls.FromString(r[2]) for r in rows]

    def count(self, kind: str, lineage_id: str | None = None) -> int:
        with self._lock:
            return sum(1 for k, v in self._rows.items() if k[0] == kind and (lineage_id is None or v[0] == lineage_id))


class PostgresDocStore:
    """``<schema>.docs`` (migrations ``research``/``holdout``); upsert keeps the first ``created_at``."""

    def __init__(self, dsn: str, schema: str = "research") -> None:
        if schema not in ("research", "holdout"):
            raise ValueError(f"unknown schema {schema!r}")
        self._dsn, self._schema = dsn, schema
        self._conn: psycopg.Connection[Any] | None = None
        self._lock = threading.Lock()

    def _c(self) -> psycopg.Connection[Any]:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def put(
        self, kind: str, doc_id: str, msg: Message, lineage_id: str = "", parent_id: str = "", created_at: int = 0
    ) -> None:
        now = time.time_ns()
        sql = (
            f"INSERT INTO {self._schema}.docs (kind, id, lineage_id, parent_id, created_at, updated_at, payload) "  # noqa: S608 - schema is allowlisted
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (kind, id) DO UPDATE SET "
            "lineage_id = EXCLUDED.lineage_id, parent_id = EXCLUDED.parent_id, updated_at = EXCLUDED.updated_at, "
            "payload = EXCLUDED.payload"
        )
        with self._lock:
            self._c().execute(
                sql, (kind, doc_id, lineage_id, parent_id, created_at or now, now, msg.SerializeToString())
            )

    def get(self, kind: str, doc_id: str, cls: type[M]) -> M | None:
        with self._lock:
            row = (
                self._c()
                .execute(f"SELECT payload FROM {self._schema}.docs WHERE kind = %s AND id = %s", (kind, doc_id))  # noqa: S608
                .fetchone()
            )
        return cls.FromString(bytes(row[0])) if row else None

    def list(
        self, kind: str, cls: type[M], lineage_id: str | None = None, parent_id: str | None = None, limit: int = 0
    ) -> list[M]:
        where, args = ["kind = %s"], [kind]
        if lineage_id is not None:
            where.append("lineage_id = %s")
            args.append(lineage_id)
        if parent_id is not None:
            where.append("parent_id = %s")
            args.append(parent_id)
        sql = f"SELECT payload FROM {self._schema}.docs WHERE {' AND '.join(where)} ORDER BY created_at, id"  # noqa: S608
        with self._lock:
            rows = self._c().execute(sql, args).fetchall()
        if limit:
            rows = rows[-limit:]
        return [cls.FromString(bytes(r[0])) for r in rows]

    def count(self, kind: str, lineage_id: str | None = None) -> int:
        sql = f"SELECT count(*) FROM {self._schema}.docs WHERE kind = %s"  # noqa: S608
        args: list[str] = [kind]
        if lineage_id is not None:
            sql += " AND lineage_id = %s"
            args.append(lineage_id)
        with self._lock:
            row = self._c().execute(sql, args).fetchone()
        return int(row[0]) if row else 0
