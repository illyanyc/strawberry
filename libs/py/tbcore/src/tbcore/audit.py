"""Audit sink for Python services (SE-020, N1): every promotion, holdout access, study launch, and analyst
action is appended to the hash-chained ``audit.events`` through ``audit.append`` (SECURITY DEFINER,
ADR-0006). ``MemoryAudit`` keeps events in process for tests and the dev profile.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import psycopg

from tbcore.strategy.api import new_ulid


@dataclass(frozen=True)
class AuditRecord:
    event_id: str
    occurred_at: int
    actor: str
    action: str
    target: str
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    after_hash: str = ""


class AuditSink(Protocol):
    def append(
        self,
        actor: str,
        action: str,
        target: str,
        details: dict[str, Any] | None = None,
        correlation_id: str = "",
        after_hash: str = "",
    ) -> AuditRecord: ...


def _record(
    actor: str, action: str, target: str, details: dict[str, Any] | None, correlation_id: str, after_hash: str
) -> AuditRecord:
    now = time.time_ns()
    return AuditRecord(
        event_id=new_ulid(now // 1_000_000, f"{actor}|{action}|{target}|{now}"),
        occurred_at=now,
        actor=actor,
        action=action,
        target=target,
        details=dict(details or {}),
        correlation_id=correlation_id,
        after_hash=after_hash,
    )


class MemoryAudit:
    def __init__(self) -> None:
        self.events: list[AuditRecord] = []
        self._lock = threading.Lock()

    def append(
        self,
        actor: str,
        action: str,
        target: str,
        details: dict[str, Any] | None = None,
        correlation_id: str = "",
        after_hash: str = "",
    ) -> AuditRecord:
        r = _record(actor, action, target, details, correlation_id, after_hash)
        with self._lock:
            self.events.append(r)
        return r

    def actions(self, action: str) -> list[AuditRecord]:
        return [e for e in self.events if e.action == action]


class PostgresAudit:
    """Appends through ``audit.append`` (needs the ``audit_writer`` role)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection[Any] | None = None
        self._lock = threading.Lock()

    def _c(self) -> psycopg.Connection[Any]:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._dsn, autocommit=True)
        return self._conn

    def append(
        self,
        actor: str,
        action: str,
        target: str,
        details: dict[str, Any] | None = None,
        correlation_id: str = "",
        after_hash: str = "",
    ) -> AuditRecord:
        r = _record(actor, action, target, details, correlation_id, after_hash)
        with self._lock:
            self._c().execute(
                "SELECT * FROM audit.append(%s, %s, %s, %s, %s, '', %s, '', '', %s, %s)",
                (
                    r.event_id,
                    r.occurred_at,
                    r.actor,
                    r.action,
                    r.target,
                    r.after_hash,
                    r.correlation_id,
                    json.dumps(r.details, sort_keys=True, default=str),
                ),
            )
        return r


def digest(payload: bytes | str) -> str:
    return hashlib.sha256(payload.encode() if isinstance(payload, str) else payload).hexdigest()
