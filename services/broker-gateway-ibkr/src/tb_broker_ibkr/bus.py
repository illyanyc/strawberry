"""NATS JetStream publisher for the adapter's events (D2, ADR-0008, ADR-0018; OP-030, OE-006).

Payloads and ``Nats-Msg-Id`` values match the outbox path and the Go adapters: ``tb.orders.v1.ExecutionReport``
(``exec:<acct>:<execId>``), ``tb.broker.v1.BrokerOrderUpdate`` (``ord:<acct>:<key>``),
``tb.ledger.v1.AccountSnapshot`` (``acct:<acct>:<as_of>``), ``tb.ledger.v1.ReconciliationRequest``
(``recon:<correlation_id>``), ``tb.alerts.v1.Alert``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext

from tb.alerts.v1 import alerts_pb2
from tb.common.v1 import common_pb2
from tb.ledger.v1 import ledger_pb2
from tb_broker_ibkr import codec
from tb_broker_ibkr.codec import subject_token
from tb_broker_ibkr.events import AlertEvent, ReconciliationRequest
from tb_broker_ibkr.models import (
    AccountSnapshot,
    BrokerOrder,
    Environment,
    ExecutionReport,
    SessionStatus,
    Side,
)
from tb_broker_ibkr.schedule import Severity

log = logging.getLogger("tb_broker_ibkr.bus")

SERVICE = "broker-gateway-ibkr"
_SIDES = {Side.BUY: common_pb2.SIDE_BUY, Side.SELL: common_pb2.SIDE_SELL, Side.SELL_SHORT: common_pb2.SIDE_SELL_SHORT}
_SEVERITY = {
    Severity.INFO: alerts_pb2.SEVERITY_INFO,
    Severity.WARNING: alerts_pb2.SEVERITY_WARNING,
    Severity.CRITICAL: alerts_pb2.SEVERITY_CRITICAL,
}


def _json(model: Any) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


class NatsSink:
    """EventSink publishing to JetStream (order path, snapshots, alerts) and core NATS (health)."""

    def __init__(
        self, url: str, user: str, password: str, environment: Environment, *, durable_via_outbox: bool = False
    ) -> None:
        # With the Postgres journal, order updates, executions, snapshots, and reconciliation requests
        # reach the bus through the outbox relay; this sink then publishes only alerts and health.
        self._outbox = durable_via_outbox
        self._url = url
        self._user = user
        self._password = password
        self._env = environment
        self._nc: NATS | None = None
        self._js: JetStreamContext | None = None

    async def connect(self) -> None:
        self._nc = await nats.connect(
            self._url,
            user=self._user,
            password=self._password,
            name=SERVICE,
            max_reconnect_attempts=-1,
            reconnect_time_wait=1,
        )
        self._js = self._nc.jetstream()

    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str]) -> None:
        """Relay publisher (:class:`~tb_broker_ibkr.relay.Publisher`): JetStream with the given headers."""
        if self._js is None:
            raise RuntimeError("NATS not connected")
        await self._js.publish(subject, payload, headers={**headers, "Tb-Source": SERVICE})

    async def _publish(self, subject: str, payload: bytes, msg_id: str, *, durable: bool = True) -> None:
        if self._nc is None or self._js is None:
            raise RuntimeError("NATS not connected")
        headers = {"Nats-Msg-Id": msg_id, "Tb-Source": SERVICE}
        if durable:
            await self._js.publish(subject, payload, headers=headers)
        else:
            await self._nc.publish(subject, payload, headers=headers)

    async def order_update(self, order: BrokerOrder) -> None:
        if self._outbox:
            return
        key = f"{order.broker_order_id}|{order.client_order_id}|{order.state.value}|{order.filled_qty}"
        await self._publish(
            f"ord.update.{subject_token(order.broker_account_id)}",
            _json(order),
            hashlib.sha256(key.encode()).hexdigest(),
        )

    async def execution(self, report: ExecutionReport) -> None:
        if self._outbox:
            return
        # Dedupe key = broker_exec_id, so a re-publish after a crash is dropped by JetStream (OE-006).
        await self._publish(
            f"exec.{subject_token(report.broker_account_id)}",
            codec.execution_proto(report, self._env).SerializeToString(),
            f"exec:{report.broker_account_id}:{report.broker_exec_id}",
        )

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        if self._outbox:
            return
        acct = snapshot.broker_account_id
        await self._publish(
            f"acct.snapshot.{subject_token(acct)}",
            codec.snapshot_proto(snapshot, self._env).SerializeToString(),
            f"acct:{acct}:{snapshot.as_of}",
        )

    async def session_status(self, status: SessionStatus) -> None:
        await self._publish(
            f"sys.health.{SERVICE}", _json(status), f"health|{status.broker_account_id}|{status.since}", durable=False
        )

    async def alert(self, event: AlertEvent) -> None:
        alert = alerts_pb2.Alert(
            alert_id=str(uuid.uuid4()),
            severity=_SEVERITY[event.severity],
            source=SERVICE,
            subject=event.subject,
            message=event.message,
            dedupe_key=event.dedupe_key,
            runbook_url=event.runbook,
            occurred_at=time.time_ns(),
        )
        await self._publish(f"alert.{event.severity.value}", alert.SerializeToString(), alert.alert_id)

    async def resolve_alert(self, dedupe_key: str, message: str) -> None:
        alert = alerts_pb2.Alert(
            alert_id=str(uuid.uuid4()),
            severity=alerts_pb2.SEVERITY_INFO,
            source=SERVICE,
            subject="resolved",
            message=message,
            dedupe_key=dedupe_key,
            occurred_at=time.time_ns(),
        )
        await self._publish("alert.info", alert.SerializeToString(), alert.alert_id)

    async def request_reconciliation(self, request: ReconciliationRequest) -> None:
        if self._outbox:
            return
        corr = uuid.uuid4().hex
        msg = ledger_pb2.ReconciliationRequest(
            broker_account_id=request.broker_account_id,
            reason=request.reason,
            requested_at=time.time_ns(),
            correlation_id=corr,
        )
        await self._publish(
            f"recon.request.{subject_token(request.broker_account_id)}", msg.SerializeToString(), f"recon:{corr}"
        )
