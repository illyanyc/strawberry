"""Monitor service (section 13 ops slice, ADR-0027).

* Forwards every bus alert (``alert.>``) to Alertmanager, which owns routing, grouping, dedupe,
  quiet hours, and Pushover escalation.
* Builds the pre-market checklist (15 minutes before the open) and the end-of-day summary
  (10 minutes after the close) from bus events and sends them as report alerts.
* Exposes ``tb_monitor_last_forward_seconds`` (the monitoring pipeline's own dead-man) and the
  latest reports on the ops port (``/reports``).
* Section 13 analyst feedback slice (ADR-0044): daily ``LiveFeedbackReport`` per configured
  strategy after the close (and on demand at ``/feedback``), retraining triggers, and autonomy
  lowering on critical alarms (``tb_monitor.feedback_runner``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import nats
import uvicorn
from nats.aio.msg import Msg
from nats.js.api import ConsumerConfig, DeliverPolicy
from prometheus_client import Gauge

from tb.alerts.v1 import alerts_pb2
from tb.ledger.v1 import ledger_pb2
from tb.orders.v1 import execution_pb2, orders_pb2
from tb_monitor.alerts import SEVERITIES, post, report_alert, to_alertmanager
from tb_monitor.feedback import FeedbackConfig
from tb_monitor.feedback_runner import FeedbackRunner, load_config
from tb_monitor.reports import Reports
from tbcore.data.calendar import xnys
from tbcore.logging import configure
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.service import ServiceSettings, create_ops_app

log = logging.getLogger("monitor")
LAST_FORWARD = Gauge(
    "tb_monitor_last_forward_seconds", "Unix time the monitor last forwarded an alert or saw an execution heartbeat."
)


class Settings(ServiceSettings):
    nats_url: str = "nats://nats:4222"
    nats_user: str = "monitor"
    alertmanager_url: str = "http://alertmanager:9093"
    premarket_lead_minutes: int = 15
    eod_delay_minutes: int = 10
    # Analyst feedback slice (MN-020..MN-026).
    monitor_feedback_config: str = "/etc/tb/feedback.json"
    monitor_feedback_delay_minutes: int = 20
    monitor_feedback_min_trades: int = 30
    monitor_feedback_min_resolved: int = 100


def session_date(ts: datetime) -> str:
    s, _ = xnys().session_at(ts)
    if s is None:
        prev = xnys().previous_session(ts)
        return prev.date.isoformat() if prev else ts.date().isoformat()
    return s.date.isoformat()


async def run(
    cfg: Settings,
    password: str,
    reports: Reports,
    latest: dict[str, str],
    stop: asyncio.Event,
    holder: dict[str, FeedbackRunner] | None = None,
) -> None:
    opts: dict[str, object] = {"servers": [cfg.nats_url], "name": "monitor", "max_reconnect_attempts": -1}
    if cfg.nats_user:
        opts["user"], opts["password"] = cfg.nats_user, password
    nc = await nats.connect(**opts)  # type: ignore[arg-type]
    js = nc.jetstream()

    def today() -> str:
        return session_date(datetime.now(tz=UTC))

    async def publish(subject: str, msg: object, msg_id: str) -> None:
        data = msg.SerializeToString()  # type: ignore[attr-defined]
        await js.publish(subject, data, headers={"Nats-Msg-Id": msg_id})

    feedback = FeedbackRunner(
        load_config(cfg.monitor_feedback_config),
        FeedbackConfig(min_trades=cfg.monitor_feedback_min_trades, min_resolved=cfg.monitor_feedback_min_resolved),
        publish,
    )
    if holder is not None:
        holder["runner"] = feedback

    async def on_alert(msg: Msg) -> None:
        a = alerts_pb2.Alert()
        a.ParseFromString(msg.data)
        try:
            await asyncio.to_thread(post, cfg.alertmanager_url, [to_alertmanager(a)])
        except Exception as exc:
            log.warning("alertmanager delivery failed; will retry", extra={"error": str(exc)})
            await msg.nak(delay=10)
            return
        reports.on_alert(today(), SEVERITIES.get(a.severity, "warning"))
        with contextlib.suppress(Exception):
            await feedback.on_alert(a)
        LAST_FORWARD.set(time.time())
        await msg.ack()

    def parse(cls: type, data: bytes):  # type: ignore[no-untyped-def]
        m = cls()
        m.ParseFromString(data)
        return m

    async def on_status(msg: Msg) -> None:
        reports.on_status(parse(execution_pb2.TradingStatus, msg.data))
        LAST_FORWARD.set(time.time())

    async def on_recon(msg: Msg) -> None:
        reports.on_recon(today(), parse(ledger_pb2.ReconciliationResult, msg.data))

    async def on_decision(msg: Msg) -> None:
        reports.on_decision(today(), parse(orders_pb2.RiskDecision, msg.data))

    async def on_exec(msg: Msg) -> None:
        x = parse(orders_pb2.ExecutionReport, msg.data)
        reports.on_fill(today(), x)
        feedback.on_execution(x)

    async def on_ledger(msg: Msg) -> None:
        reports.on_ledger_update(today(), parse(ledger_pb2.LedgerUpdate, msg.data))

    # Every JetStream subscription names its stream: the stream lookup ($JS.API.STREAM.NAMES) is not
    # in this user's permissions (least privilege), and without it the subscribe times out.
    await js.subscribe(
        "alert.>",
        stream="ALERTS",
        durable="monitor-alerts",
        cb=on_alert,
        manual_ack=True,
        config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW, ack_wait=30),
    )
    await nc.subscribe("sys.health.execution", cb=on_status)
    new = ConsumerConfig(deliver_policy=DeliverPolicy.NEW)
    for subject, stream, cb in (
        ("recon.result.>", "LEDGER", on_recon),
        ("ord.decision", "ORDERS", on_decision),
        ("exec.>", "ORDERS", on_exec),
        ("ledger.update.>", "LEDGER", on_ledger),
    ):
        await js.subscribe(subject, stream=stream, cb=cb, ordered_consumer=True, config=new)
    sent: set[str] = set()
    while not stop.is_set():
        now = datetime.now(tz=UTC)
        s, _ = xnys().session_at(now)
        if s is not None:
            if s.open - timedelta(minutes=cfg.premarket_lead_minutes) <= now < s.open and f"pre:{s.date}" not in sent:
                ok, text = reports.premarket(s.date.isoformat())
                latest["premarket"] = text
                name = "PreMarketChecklist" if ok else "PreMarketChecklistFailed"
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(post, cfg.alertmanager_url, [report_alert(name, text, now)])
                    sent.add(f"pre:{s.date}")
            if now >= s.close + timedelta(minutes=cfg.eod_delay_minutes) and f"eod:{s.date}" not in sent:
                text = reports.eod(s.date.isoformat())
                latest["eod"] = text
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(post, cfg.alertmanager_url, [report_alert("EndOfDaySummary", text, now)])
                    sent.add(f"eod:{s.date}")
            if now >= s.close + timedelta(minutes=cfg.monitor_feedback_delay_minutes) and f"fb:{s.date}" not in sent:
                try:
                    await feedback.run(now)
                    sent.add(f"fb:{s.date}")
                except Exception as exc:
                    log.warning("live feedback run failed; will retry", extra={"error": str(exc)[:200]})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=30)
    await nc.drain()


def main() -> None:
    cfg = Settings(service="monitor")
    configure(cfg.service, cfg.log_level)
    password = ""
    if cfg.nats_user:
        try:
            password = get_secret(f"nats_{cfg.nats_user}_password", cfg.secrets_dir).decode()
        except SecretNotFoundError:
            raise SystemExit(f"missing secret nats_{cfg.nats_user}_password") from None
    reports, latest = Reports(), {"premarket": "", "eod": ""}
    holder: dict[str, FeedbackRunner] = {}
    app = create_ops_app(cfg)
    app.add_api_route("/reports", lambda: latest, methods=["GET"])
    app.add_api_route("/feedback", lambda: holder["runner"].snapshot() if "runner" in holder else {}, methods=["GET"])
    threading.Thread(
        target=lambda: uvicorn.run(app, host=cfg.ops_host, port=cfg.ops_port, log_config=None, access_log=False),
        daemon=True,
    ).start()
    stop = asyncio.Event()
    asyncio.run(run(cfg, password, reports, latest, stop, holder))


if __name__ == "__main__":
    main()
