"""NATS JetStream publisher for the adapter's events (D2, ADR-0008; OP-030, OE-006).

Payloads are protobuf where a contract exists (``tb.orders.v1.ExecutionReport``, ``tb.alerts.v1.Alert``)
and canonical JSON of the domain model otherwise, until section 04's ``BrokerGateway`` contract
lands. Every message carries ``Nats-Msg-Id`` (JetStream dedupe) and the correlation ID.
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
from tb.orders.v1 import orders_pb2
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


def subject_token(broker_account_id: str) -> str:
    """NATS subject tokens cannot contain '.', '*', '>' or whitespace."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in broker_account_id)


def execution_proto(report: ExecutionReport, environment: Environment) -> orders_pb2.ExecutionReport:
    return orders_pb2.ExecutionReport(
        broker_account_id=report.broker_account_id,
        broker_exec_id=report.broker_exec_id,
        client_order_id=report.client_order_id,
        broker_order_id=report.broker_order_id,
        instrument_id=report.instrument_id,
        side=_SIDES[report.side],
        qty=common_pb2.Decimal(value=str(report.qty)),
        price=common_pb2.Decimal(value=str(report.price)),
        fees=common_pb2.Decimal(value=str(report.fees)) if report.fees is not None else None,
        event_time=report.event_time,
        mode=common_pb2.MODE_LIVE if environment is Environment.LIVE else common_pb2.MODE_PAPER,
        ingested_at=report.ingested_at,
    )


def _json(model: Any) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


class NatsSink:
    """EventSink publishing to JetStream (order path, snapshots, alerts) and core NATS (health)."""

    def __init__(self, url: str, user: str, password: str, environment: Environment) -> None:
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

    async def _publish(self, subject: str, payload: bytes, msg_id: str, *, durable: bool = True) -> None:
        if self._nc is None or self._js is None:
            raise RuntimeError("NATS not connected")
        headers = {"Nats-Msg-Id": msg_id, "Tb-Source": SERVICE}
        if durable:
            await self._js.publish(subject, payload, headers=headers)
        else:
            await self._nc.publish(subject, payload, headers=headers)

    async def order_update(self, order: BrokerOrder) -> None:
        key = f"{order.broker_order_id}|{order.client_order_id}|{order.state.value}|{order.filled_qty}"
        await self._publish(
            f"ord.update.{subject_token(order.broker_account_id)}",
            _json(order),
            hashlib.sha256(key.encode()).hexdigest(),
        )

    async def execution(self, report: ExecutionReport) -> None:
        # Dedupe key = broker_exec_id, so a re-publish after a crash is dropped by JetStream (OE-006).
        await self._publish(
            f"exec.{subject_token(report.broker_account_id)}",
            execution_proto(report, self._env).SerializeToString(),
            f"exec|{report.broker_account_id}|{report.broker_exec_id}",
        )

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        await self._publish(
            f"acct.snapshot.{subject_token(snapshot.broker_account_id)}",
            _json(snapshot),
            f"snap|{snapshot.broker_account_id}|{snapshot.as_of}",
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
        payload = json.dumps({"broker_account_id": request.broker_account_id, "reason": request.reason}).encode()
        await self._publish(
            f"recon.request.{subject_token(request.broker_account_id)}",
            payload,
            f"recon|{request.broker_account_id}|{time.time_ns()}",
        )
