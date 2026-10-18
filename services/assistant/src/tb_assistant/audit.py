"""Audit events for analyst actions (N1, AN-003, AN-060; SE-021, SE-023).

Every tool call (allowed or denied), round, approval, autonomy change, and code-proposal review is
appended through the ``audit.append`` SECURITY DEFINER function, so ``make audit-verify`` covers
them. Details are canonical JSON, redacted (SE-012), and carry input/output hashes rather than
payloads.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from tbcore.redact import redact


@dataclass(frozen=True)
class AuditRecord:
    actor: str  # "analyst" (loop and tools) or "assistant" (chat)
    action: str  # analyst.tool.<name>, analyst.tool.denied, analyst.round.completed, ...
    target: str
    details: dict[str, Any]
    correlation_id: str = ""


class AuditSink(Protocol):
    async def append(self, rec: AuditRecord) -> None: ...


@dataclass
class MemoryAudit:
    records: list[AuditRecord] = field(default_factory=list)
    fail: bool = False

    async def append(self, rec: AuditRecord) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(rec)

    def actions(self, prefix: str = "") -> list[str]:
        return [r.action for r in self.records if r.action.startswith(prefix)]


def canonical(details: dict[str, Any]) -> str:
    def clean(v: Any) -> Any:
        if isinstance(v, str):
            return redact(v)
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [clean(x) for x in v]
        return v

    return json.dumps(clean(details), sort_keys=True, separators=(",", ":"), default=str)


class PgAudit:
    """Appends through ``audit.append`` (role ``audit_writer``)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def append(self, rec: AuditRecord) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT * FROM audit.append($1, $2, $3, $4, $5, '', '', '', '', $6, $7)",
                str(uuid.uuid4()),
                time.time_ns(),
                rec.actor,
                rec.action,
                rec.target,
                rec.correlation_id,
                canonical(rec.details),
            )
