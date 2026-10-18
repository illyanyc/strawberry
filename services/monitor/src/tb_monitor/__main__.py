"""Monitor service (section 13: ops slice ADR-0027, analyst feedback slice ADR-0044, full ADR-0045).

* Forwards every bus alert (``alert.>``) to Alertmanager, which owns routing, grouping, dedupe, quiet
  hours, and Pushover escalation; keeps the alert inbox (acks, silences).
* Builds the pre-market checklist and the end-of-day summary and stores them as daily reports.
* Records live and paper streams to per-session files (``tb_monitor.recorder``, ADR-0046), replays them at
  start, and feeds the full-monitoring core: performance bands, drawdown, drift, divergence (daily parity
  on the closed session), execution quality, health, containment through execution, graduation evidence.
* Serves ``MonitorService`` over gRPC for api-gateway (and ``GetLiveFeedback`` for the assistant).
* Live feedback to the analyst (``tb_monitor.feedback_runner``), the pipeline's own dead-man gauge.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import grpc
import nats
import uvicorn
from nats.aio.msg import Msg
from nats.js.api import ConsumerConfig, DeliverPolicy
from prometheus_client import Counter, Gauge

from tb.alerts.v1 import alerts_pb2
from tb.ledger.v1 import ledger_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2, orders_pb2
from tb_monitor import recorder as rec_mod
from tb_monitor.alertbox import Inbox
from tb_monitor.alerts import SEVERITIES, post, report_alert, to_alertmanager
from tb_monitor.containment import Containment, GrpcExecutor
from tb_monitor.core import K_DAILY, Monitor
from tb_monitor.feedback import FeedbackConfig
from tb_monitor.feedback_runner import FeedbackRunner, load_config
from tb_monitor.reports import Reports
from tb_monitor.service import ALLOW, SERVICE, MonitorServicer
from tb_monitor.strategies import load as load_strategies
from tbcore.audit import AuditSink, MemoryAudit, PostgresAudit
from tbcore.data.calendar import xnys
from tbcore.docstore import DocStore, MemoryDocStore, PostgresDocStore
from tbcore.logging import configure
from tbcore.rpc import Client, serve
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.service import ServiceSettings, create_ops_app
from tbcore.svcauth import Signer, Verifier

log = logging.getLogger("monitor")
LAST_FORWARD = Gauge(
    "tb_monitor_last_forward_seconds", "Unix time the monitor last forwarded an alert or saw an execution heartbeat."
)
HEALTH = Gauge("tb_strategy_health_score", "Strategy health score 0..100 (MN-031).", ["strategy"])
STATUS = Gauge(
    "tb_strategy_health_status",
    "Strategy health status (1 ok, 2 warning, 3 breach, 4 severe, 5 insufficient).",
    ["strategy"],
)
COMPONENT = Gauge("tb_strategy_component_value", "Health component value (MN-031).", ["strategy", "component"])
COMPONENT_SCORE = Gauge(
    "tb_strategy_component_score", "Health component score 0..100 (MN-031).", ["strategy", "component"]
)
DRAWDOWN = Gauge("tb_strategy_drawdown_pct", "Drawdown since deployment, % (MN-032).", ["strategy"])
DRAWDOWN_P95 = Gauge(
    "tb_strategy_drawdown_p95_pct", "Backtest p95 drawdown over the live horizon, % (MN-032).", ["strategy"]
)
SHORTFALL = Gauge("tb_strategy_shortfall_bps", "Implementation shortfall, bps (MN-033).", ["strategy", "kind"])
PSI = Gauge("tb_strategy_feature_psi", "Feature PSI vs the training reference (MN-038).", ["strategy", "feature"])
PENDING = Gauge("tb_monitor_containment_pending", "Containment actions decided but not executed (MN-035).")
ACTIONS = Counter("tb_monitor_containment_actions_total", "Containment actions (MN-035).", ["kind", "executed"])
REC_LAST = Gauge("tb_monitor_recorder_last_write_seconds", "Unix time the recorder last wrote a message (MN-045).")
REC_MSGS = Counter("tb_monitor_recorder_messages_total", "Messages recorded (MN-045).", ["class"])
REC_COMPLETE = Gauge("tb_monitor_recorder_session_complete", "1 when the last closed session's recording is complete.")


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
    # Full monitoring (ADR-0045, ADR-0046).
    monitor_grpc_addr: str = ""
    execution_addr: str = ""
    strategy_registry: str = "/etc/tb/strategies.json"
    research_registry: str = ""
    recordings_dir: str = ""
    recorder_retention_days: int = 180
    monitor_history_sessions: int = 60
    monitor_eval_seconds: float = 15.0
    monitor_parity_delay_minutes: int = 30
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "platform"
    db_user: str = ""


def session_date(ts: datetime) -> str:
    s, _ = xnys().session_at(ts)
    if s is None:
        prev = xnys().previous_session(ts)
        return prev.date.isoformat() if prev else ts.date().isoformat()
    return s.date.isoformat()


def build_store(cfg: Settings) -> tuple[DocStore, AuditSink]:
    if not cfg.db_user:
        return MemoryDocStore(), MemoryAudit()
    pw = get_secret(f"pg_{cfg.db_user}_password", cfg.secrets_dir).decode()
    dsn = f"postgresql://{cfg.db_user}:{pw}@{cfg.db_host}:{cfg.db_port}/{cfg.db_name}"
    return PostgresDocStore(dsn, "monitor"), PostgresAudit(dsn)


def replay_history(core: Monitor, rec: rec_mod.SessionRecorder, sessions: int) -> int:
    """Rebuild the books from the last ``sessions`` recorded sessions (ADR-0046); returns messages."""
    dates = rec.sessions()[-sessions:]
    n = 0
    for d in dates:
        for m in rec_mod.iter_sessions(rec.dir, [d]):
            core.on_message(m.subject, m.data)
            n += 1
        if d < session_date(datetime.now(tz=UTC)):
            core.close_session(d)
    return n


def export_metrics(core: Monitor, hs: list[mp.StrategyHealth]) -> None:
    for h in hs:
        HEALTH.labels(h.strategy_id).set(h.score)
        STATUS.labels(h.strategy_id).set(h.status)
        for c in h.components:
            COMPONENT.labels(h.strategy_id, c.name).set(c.value)
            COMPONENT_SCORE.labels(h.strategy_id, c.name).set(c.score)
        perf = core.store.get("performance", h.strategy_id, mp.PerformanceReport)
        if perf is not None:
            DRAWDOWN.labels(h.strategy_id).set(perf.max_drawdown_pct)
            DRAWDOWN_P95.labels(h.strategy_id).set(perf.drawdown_p95_pct)
        exe = core.store.get("execution", h.strategy_id, mp.ExecutionQualityReport)
        if exe is not None:
            SHORTFALL.labels(h.strategy_id, "live").set(exe.shortfall_bps)
            SHORTFALL.labels(h.strategy_id, "modeled").set(exe.modeled_shortfall_bps)
        drift = core.store.get("drift", h.strategy_id, mp.DriftReport)
        if drift is not None:
            for f in drift.features[:10]:
                PSI.labels(h.strategy_id, f.feature).set(f.psi)
    PENDING.set(
        sum(
            1
            for a in core.containment.actions(limit=10_000)
            if not a.executed and a.kind != mp.CONTAINMENT_ACTION_KIND_ALERT
        )
    )


async def run(
    cfg: Settings,
    password: str,
    reports: Reports,
    latest: dict[str, str],
    stop: asyncio.Event,
    holder: dict[str, Any] | None = None,
) -> None:
    opts: dict[str, object] = {"servers": [cfg.nats_url], "name": "monitor", "max_reconnect_attempts": -1}
    if cfg.nats_user:
        opts["user"], opts["password"] = cfg.nats_user, password
    nc = await nats.connect(**opts)  # type: ignore[arg-type]
    js = nc.jetstream()
    loop = asyncio.get_running_loop()

    def today() -> str:
        return session_date(datetime.now(tz=UTC))

    async def apublish(subject: str, msg: Any, msg_id: str) -> None:
        data = msg.SerializeToString()
        await js.publish(subject, data, headers={"Nats-Msg-Id": msg_id})

    def publish(subject: str, msg: Any, msg_id: str) -> None:
        """Synchronous publish for the core (called from worker threads or the loop)."""
        if subject.startswith("monitor.containment."):
            ACTIONS.labels(mp.ContainmentActionKind.Name(msg.kind), str(msg.executed).lower()).inc()
        fut = asyncio.run_coroutine_threadsafe(apublish(subject, msg, msg_id), loop)
        with contextlib.suppress(Exception):
            if threading.current_thread() is not threading.main_thread():
                fut.result(timeout=10)

    store, audit = build_store(cfg)
    strategies = load_strategies(cfg.strategy_registry, cfg.research_registry, cfg.monitor_feedback_config)
    executor = None
    exec_client = None
    if cfg.execution_addr:
        signer = Signer.from_secrets("monitor", cfg.secrets_dir)
        svc = execution_pb2.DESCRIPTOR.services_by_name["ExecutionService"]
        exec_client = Client(grpc.insecure_channel(cfg.execution_addr), svc, signer, "execution", timeout=10.0)
        executor = GrpcExecutor(exec_client)
    recorder = (
        rec_mod.SessionRecorder(cfg.recordings_dir, rec_mod.xnys_session, cfg.recorder_retention_days)
        if cfg.recordings_dir
        else None
    )
    core = Monitor(
        strategies,
        store,
        Containment(store, audit, executor, publish),
        publish,
        recorder,
        session_of=lambda t: session_date(datetime.fromtimestamp(t / 1e9, tz=UTC)),
    )
    inbox = Inbox(store, audit, cfg.alertmanager_url)
    if recorder is not None:
        n = await asyncio.to_thread(replay_history, core, recorder, cfg.monitor_history_sessions)
        log.info("history replayed", extra={"messages": n, "sessions": len(recorder.sessions())})

    async def apublish_report(subject: str, msg: object, msg_id: str) -> None:
        await apublish(subject, msg, msg_id)

    feedback = FeedbackRunner(
        load_config(cfg.monitor_feedback_config),
        FeedbackConfig(min_trades=cfg.monitor_feedback_min_trades, min_resolved=cfg.monitor_feedback_min_resolved),
        apublish_report,
    )
    if holder is not None:
        holder["runner"], holder["core"] = feedback, core
    if cfg.monitor_grpc_addr:
        verifier = Verifier.from_secrets("monitor", ["api-gateway", "assistant", "execution"], cfg.secrets_dir)
        server, port = serve(
            cfg.monitor_grpc_addr, [(SERVICE, MonitorServicer(core, inbox), ALLOW)], verifier, workers=8
        )
        server.start()
        log.info("MonitorService listening", extra={"port": port, "store": "postgres" if cfg.db_user else "memory"})

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
            await asyncio.to_thread(inbox.on_alert, a)
            if a.severity == alerts_pb2.SEVERITY_CRITICAL:
                core.on_alert_critical(f"{a.subject} {a.dedupe_key} {a.message}")
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

    def on_recorded(subject: str, data: bytes) -> None:
        REC_LAST.set(time.time())
        REC_MSGS.labels(rec_mod.subject_class(subject)).inc()
        core.on_message(subject, data)

    rec_task = (
        asyncio.create_task(rec_mod.run(js, recorder, stop, on_message=on_recorded)) if recorder is not None else None
    )

    def evaluate() -> list[mp.StrategyHealth]:
        if exec_client is not None:
            with contextlib.suppress(Exception):
                core.set_modes(
                    list(
                        exec_client.call(
                            "ListStrategies", execution_pb2.ListStrategiesRequest(), timeout=5.0
                        ).strategies
                    )
                )
        core.containment.retry_pending()
        hs = core.evaluate_all()
        export_metrics(core, hs)
        return hs

    sent: set[str] = set()
    last_sync = 0.0
    while not stop.is_set():
        now = datetime.now(tz=UTC)
        try:
            await asyncio.to_thread(evaluate)
        except Exception:
            log.exception("evaluation failed")
        if time.monotonic() - last_sync > 60:
            last_sync = time.monotonic()
            with contextlib.suppress(Exception):
                await asyncio.to_thread(inbox.sync_prometheus)
        s, _ = xnys().session_at(now)
        if s is not None:
            d = s.date.isoformat()
            if s.open - timedelta(minutes=cfg.premarket_lead_minutes) <= now < s.open and f"pre:{d}" not in sent:
                ok, text = reports.premarket(d)
                latest["premarket"] = text
                store.put(
                    K_DAILY, d, mp.DailyReport(date=d, premarket_ok=ok, premarket=text, updated_at=time.time_ns())
                )
                name = "PreMarketChecklist" if ok else "PreMarketChecklistFailed"
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(post, cfg.alertmanager_url, [report_alert(name, text, now)])
                    sent.add(f"pre:{d}")
            if now >= s.close + timedelta(minutes=cfg.eod_delay_minutes) and f"eod:{d}" not in sent:
                text = reports.eod(d)
                latest["eod"] = text
                dr = store.get(K_DAILY, d, mp.DailyReport) or mp.DailyReport(date=d)
                dr.eod, dr.updated_at = text, time.time_ns()
                store.put(K_DAILY, d, dr)
                core.close_session(d)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(post, cfg.alertmanager_url, [report_alert("EndOfDaySummary", text, now)])
                    sent.add(f"eod:{d}")
            if now >= s.close + timedelta(minutes=cfg.monitor_feedback_delay_minutes) and f"fb:{d}" not in sent:
                try:
                    for rep in await feedback.run(now):
                        store.put("feedback", rep.strategy_id, rep, rep.strategy_id)
                    sent.add(f"fb:{d}")
                except Exception as exc:
                    log.warning("live feedback run failed; will retry", extra={"error": str(exc)[:200]})
            if (
                recorder is not None
                and now >= s.close + timedelta(minutes=cfg.monitor_parity_delay_minutes)
                and f"par:{d}" not in sent
            ):
                recorder.rollover(time.time_ns())  # closes the session file; the next message reopens
                REC_COMPLETE.set(1 if recorder.manifest(d).get("complete") else 0)
                for sid, m in core.strategies.items():
                    if m.mode in ("paper", "live") and m.entry.get("runtime"):
                        try:
                            await asyncio.to_thread(core.run_divergence, sid, d)
                        except Exception:
                            log.exception("daily parity failed", extra={"strategy": sid})
                with contextlib.suppress(Exception):
                    recorder.prune(now.date())
                sent.add(f"par:{d}")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=cfg.monitor_eval_seconds)
    if rec_task is not None:
        await rec_task
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
    holder: dict[str, Any] = {}
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
