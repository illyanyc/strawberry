"""Order-path audit events (OE-011, BR-014, SE-021): every submit, cancel, replace, and reject.

Writes through the same ``audit.append`` SECURITY DEFINER function as the Go services
(``libs/go/audit``), so ``make audit-verify`` covers these events. Submit, cancel, and replace are
audited *before* the request is sent and the request fails if the append fails (SE-021); rejects
are audited when they happen. Details are canonical JSON (sorted keys) and redacted (SE-012).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import asyncpg

from tbcore.redact import redact


@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str  # broker.order.submit | .cancel | .replace | .reject
    target: str  # attribution ID (client_order_id) or broker order id
    details: dict[str, Any]
    actor: str = "broker-gateway-ibkr"
    correlation_id: str = ""


class AuditLog(Protocol):
    async def append(self, event: AuditEvent) -> None: ...


@dataclass
class MemoryAuditLog:
    events: list[AuditEvent] = field(default_factory=list)
    fail: bool = False

    async def append(self, event: AuditEvent) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class UnavailableAuditLog:
    """No audit store configured: every audited action fails closed (SE-021)."""

    async def append(self, event: AuditEvent) -> None:
        raise RuntimeError("audit log not configured (TB_DB_USER); refusing audited broker actions")


def canonical_details(details: dict[str, Any]) -> str:
    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [clean(v) for v in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        return redact(str(value))

    return json.dumps(clean(details), sort_keys=True, separators=(",", ":"))


class PostgresAuditLog:
    """``audit.append`` over an asyncpg pool as ``svc_broker_gateway_ibkr`` (member of audit_writer)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> PostgresAuditLog:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=5)  # type: ignore[no-untyped-call]
        return cls(pool)

    async def append(self, event: AuditEvent) -> None:
        async with self._pool.acquire() as conn:  # type: ignore[no-untyped-call]
            await conn.fetchrow(
                "SELECT seq, hash FROM audit.append($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                uuid.uuid4().hex,
                time.time_ns(),
                event.actor,
                event.action,
                redact(event.target),
                "",
                "",
                "",
                "",
                event.correlation_id,
                canonical_details(event.details),
            )

    async def close(self) -> None:
        await self._pool.close()  # type: ignore[no-untyped-call]
