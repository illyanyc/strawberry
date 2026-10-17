"""Prometheus metrics for the IBKR adapter (OP-060; IB-014, IB-015 alert delivery).

Session alerts are classified by the adapter (maintenance-aware) and exported as
``tb_ibkr_alert_active``; Prometheus rules page on it through Alertmanager -> Pushover, which is the
owner-facing delivery path that exists today (``infra/observability/prometheus/rules/ibkr.yml``).
"""

from __future__ import annotations

from datetime import datetime

from prometheus_client import CollectorRegistry, Counter, Gauge

from tb_broker_ibkr.events import AlertEvent, ReconciliationRequest
from tb_broker_ibkr.models import AccountSnapshot, BrokerOrder, ExecutionReport, SessionState, SessionStatus


class PrometheusMetrics:
    """Implements :class:`~tb_broker_ibkr.session.SessionMetrics` and :class:`~tb_broker_ibkr.events.EventSink`."""

    def __init__(self, registry: CollectorRegistry, environment: str) -> None:
        self._env = environment
        self._state = Gauge(
            "tb_ibkr_session_state",
            "1 for the current state of each IB Gateway connection.",
            ["environment", "connection", "state"],
            registry=registry,
        )
        self._ready = Gauge(
            "tb_ibkr_session_ready",
            "1 when the connection is ready (orders accepted).",
            ["environment", "connection"],
            registry=registry,
        )
        self._reauth = Gauge(
            "tb_ibkr_reauth_required",
            "1 when the gateway most likely needs the owner to log in / approve 2FA.",
            ["environment", "connection"],
            registry=registry,
        )
        self._reconnects = Counter(
            "tb_ibkr_connect_attempts",
            "API connection attempts.",
            ["environment", "connection"],
            registry=registry,
        )
        self._heartbeat = Gauge(
            "tb_ibkr_last_heartbeat_timestamp_seconds",
            "Time of the last successful gateway heartbeat.",
            ["environment", "connection"],
            registry=registry,
        )
        self._alerts = Gauge(
            "tb_ibkr_alert_active",
            "1 while an adapter alert is active.",
            ["environment", "subject", "severity"],
            registry=registry,
        )
        self._orders = Counter(
            "tb_ibkr_order_updates", "Order updates published.", ["environment", "state"], registry=registry
        )
        self._executions = Counter(
            "tb_ibkr_executions", "Executions published.", ["environment", "owner"], registry=registry
        )
        self._margin = Gauge(
            "tb_ibkr_margin_deficit",
            "1 when the account reports negative excess liquidity.",
            ["environment", "broker_account_id"],
            registry=registry,
        )
        self._active: dict[str, tuple[str, str]] = {}

    # ---- SessionMetrics --------------------------------------------------------------------------
    def set_state(self, connection: str, state: SessionState) -> None:
        for s in SessionState:
            self._state.labels(self._env, connection, s.value).set(1 if s is state else 0)
        self._ready.labels(self._env, connection).set(1 if state is SessionState.READY else 0)

    def set_reauth_required(self, connection: str, value: bool) -> None:
        self._reauth.labels(self._env, connection).set(1 if value else 0)

    def reconnect_attempt(self, connection: str) -> None:
        self._reconnects.labels(self._env, connection).inc()

    def heartbeat(self, connection: str, when: datetime) -> None:
        self._heartbeat.labels(self._env, connection).set(when.timestamp())

    # ---- EventSink -----------------------------------------------------------------------------
    async def order_update(self, order: BrokerOrder) -> None:
        self._orders.labels(self._env, order.state.value).inc()

    async def execution(self, report: ExecutionReport) -> None:
        self._executions.labels(self._env, report.owner.value).inc()

    async def account_snapshot(self, snapshot: AccountSnapshot) -> None:
        self._margin.labels(self._env, snapshot.broker_account_id).set(1 if snapshot.margin_deficit else 0)

    async def session_status(self, status: SessionStatus) -> None:
        return None

    async def alert(self, event: AlertEvent) -> None:
        previous = self._active.get(event.dedupe_key)
        if previous is not None:
            self._alerts.labels(self._env, *previous).set(0)
        self._active[event.dedupe_key] = (event.subject, event.severity.value)
        self._alerts.labels(self._env, event.subject, event.severity.value).set(1)

    async def resolve_alert(self, dedupe_key: str, message: str) -> None:
        previous = self._active.pop(dedupe_key, None)
        if previous is not None:
            self._alerts.labels(self._env, *previous).set(0)

    async def request_reconciliation(self, request: ReconciliationRequest) -> None:
        return None
