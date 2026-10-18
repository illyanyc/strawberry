"""Outbound events: order updates, executions, snapshots, session status, alerts, reconciliation.

:class:`EventSink` is what the adapter and supervisor publish to. Production fans out to NATS
(:mod:`tb_broker_ibkr.bus`), Prometheus metrics, and logs; tests use :class:`RecordingSink`.
Subjects (section 04, ADR-0008): ``ord.update.<acct>``, ``exec.<acct>``, ``acct.snapshot.<acct>``,
``sys.health.broker-gateway-ibkr``, ``alert.<severity>``, ``recon.request.<acct>``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from tb_broker_ibkr.models import AccountSnapshot, BrokerOrder, ExecutionReport, SessionStatus
from tb_broker_ibkr.schedule import Severity

RUNBOOK_2FA = "docs/runbooks/ibkr-weekly-2fa.md"
RUNBOOK_OUTAGE = "docs/runbooks/ibkr-weekly-2fa.md#gateway-down-mid-week"

log = logging.getLogger("tb_broker_ibkr.events")


@dataclass(frozen=True, slots=True)
class AlertEvent:
    severity: Severity
    subject: str
    message: str
    dedupe_key: str
    runbook: str = ""


@dataclass(frozen=True, slots=True)
class ReconciliationRequest:
    broker_account_id: str
    reason: str


class EventSink(Protocol):
    async def order_update(self, order: BrokerOrder) -> None: ...

    async def execution(self, report: ExecutionReport) -> None: ...

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None: ...

    async def session_status(self, status: SessionStatus) -> None: ...

    async def alert(self, event: AlertEvent) -> None: ...

    async def resolve_alert(self, dedupe_key: str, message: str) -> None: ...

    async def request_reconciliation(self, request: ReconciliationRequest) -> None: ...


@dataclass
class RecordingSink:
    """Collects every event in memory (tests and the dev fake-gateway mode)."""

    orders: list[BrokerOrder] = field(default_factory=list)
    executions: list[ExecutionReport] = field(default_factory=list)
    snapshots: list[AccountSnapshot] = field(default_factory=list)
    statuses: list[SessionStatus] = field(default_factory=list)
    alerts: list[AlertEvent] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    reconciliations: list[ReconciliationRequest] = field(default_factory=list)

    async def order_update(self, order: BrokerOrder) -> None:
        self.orders.append(order)

    async def execution(self, report: ExecutionReport) -> None:
        self.executions.append(report)

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        self.snapshots.append(snapshot)

    async def session_status(self, status: SessionStatus) -> None:
        self.statuses.append(status)

    async def alert(self, event: AlertEvent) -> None:
        self.alerts.append(event)

    async def resolve_alert(self, dedupe_key: str, message: str) -> None:
        self.resolved.append(dedupe_key)

    async def request_reconciliation(self, request: ReconciliationRequest) -> None:
        self.reconciliations.append(request)


class FanoutSink:
    """Delivers each event to every sink; one failing sink never blocks the others."""

    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = sinks

    async def _each(self, method: str, *args: object) -> None:
        for sink in self._sinks:
            try:
                await getattr(sink, method)(*args)
            except Exception:
                log.exception("event sink failed", extra={"sink": type(sink).__name__, "event": method})

    async def order_update(self, order: BrokerOrder) -> None:
        await self._each("order_update", order)

    async def execution(self, report: ExecutionReport) -> None:
        await self._each("execution", report)

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        await self._each("account_snapshot", snapshot)

    async def session_status(self, status: SessionStatus) -> None:
        await self._each("session_status", status)

    async def alert(self, event: AlertEvent) -> None:
        await self._each("alert", event)

    async def resolve_alert(self, dedupe_key: str, message: str) -> None:
        await self._each("resolve_alert", dedupe_key, message)

    async def request_reconciliation(self, request: ReconciliationRequest) -> None:
        await self._each("request_reconciliation", request)


class LogSink:
    """Structured log line per alert, status change, and reconciliation request."""

    async def order_update(self, order: BrokerOrder) -> None:
        log.info(
            "order update",
            extra={
                "broker_account_id": order.broker_account_id,
                "client_order_id": order.client_order_id,
                "broker_order_id": order.broker_order_id,
                "state": order.state.value,
                "raw_status": order.raw_status,
            },
        )

    async def execution(self, report: ExecutionReport) -> None:
        log.info(
            "execution",
            extra={
                "broker_account_id": report.broker_account_id,
                "broker_exec_id": report.broker_exec_id,
                "client_order_id": report.client_order_id,
            },
        )

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        return None

    async def session_status(self, status: SessionStatus) -> None:
        log.info(
            "session status",
            extra={
                "broker_account_id": status.broker_account_id,
                "state": status.state.value,
                "reasons": list(status.degraded_reasons),
                "reauth_required": status.reauth_required,
            },
        )

    async def alert(self, event: AlertEvent) -> None:
        level = logging.ERROR if event.severity is Severity.CRITICAL else logging.WARNING
        log.log(level, event.message, extra={"alert": event.subject, "dedupe_key": event.dedupe_key})

    async def resolve_alert(self, dedupe_key: str, message: str) -> None:
        log.info(message, extra={"dedupe_key": dedupe_key, "resolved": True})

    async def request_reconciliation(self, request: ReconciliationRequest) -> None:
        log.info(
            "reconciliation requested",
            extra={"broker_account_id": request.broker_account_id, "reason": request.reason},
        )
