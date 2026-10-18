"""Strategy runtime entrypoint (section 07, ADR-0026).

Consumes final bars (``md.bar.<tf>.<instrument>``) and the hosted strategies' ledger updates
(``ledger.update.<account>``, own owner only), runs the strategies, and publishes intents on the
durable subject ``ord.intent`` with ``Nats-Msg-Id`` = intent ID (execution is idempotent on it,
OE-006). It never talks to brokers. A heartbeat goes to ``sys.health.strategy-runtime`` every
``TB_HEARTBEAT_INTERVAL`` seconds and after each bar (dead-man, RM-017).

Phase 5 (ADR-0051): ``tv-<name>`` strategies consume their webhook signals from
``sig.tv-<name>.<instrument>`` (new messages only: a signal that arrived while the runtime was down is
never acted on late, SM-020).

Model strategies also publish every scoring as ``Signal`` on ``sig.<strategy_id>`` and the scored
features as ``FeatureVector`` on ``feat.<feature_set>.<instrument_id>`` (ML-042, MN-038), and record
the feature and scoring time in ``tb_inference_seconds`` (ML-043). Research deployments are re-read
every ``TB_RESEARCH_RELOAD_SECONDS`` (default 30): added shadow/paper entries are hosted, retired
ones dropped; a research entry is never live and never replaces a seed entry (SM-016).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import nats
import uvicorn
from nats.aio.msg import Msg
from nats.js.api import ConsumerConfig, DeliverPolicy
from prometheus_client import Histogram

from tb.context.v1 import context_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tbcore.context.store import ContextStore
from tbcore.data.lake import Lake
from tbcore.logging import configure
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.service import ServiceSettings, create_ops_app
from tbcore.strategy.host import Runner, feature_subject, feature_vector_messages, signal_messages

log = logging.getLogger("strategy-runtime")
# ML-043: p99 of the "score" stage must stay under 20 ms per batch of the active universe on CPU.
INFERENCE = Histogram(
    "tb_inference_seconds",
    "Online model inference time per bar batch, by stage (features = online feature update, score = model).",
    ["strategy", "stage"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 1.0),
)


def _session_start_ns() -> int:
    """Today's 00:00 New York time: ledger updates from here rebuild intraday positions."""
    ny = ZoneInfo("America/New_York")
    d = datetime.now(tz=ny).date()
    return int(datetime.combine(d, dtime(0, 0), tzinfo=ny).astimezone(UTC).timestamp() * 1e9)


async def run(
    runner: Runner,
    url: str,
    user: str,
    password: str,
    runtime_id: str,
    interval: float,
    stop: asyncio.Event,
    lake_dir: str = "",
    seed_ids: set[str] | None = None,
    research: str = "",
    reload_seconds: float = 30.0,
) -> None:
    opts: dict[str, object] = {"servers": [url], "name": runtime_id, "max_reconnect_attempts": -1}
    if user:
        opts["user"], opts["password"] = user, password
    nc = await nats.connect(**opts)  # type: ignore[arg-type]
    js = nc.jetstream()
    lock = asyncio.Lock()

    async def beat() -> None:
        await nc.publish(
            "sys.health.strategy-runtime", runner.heartbeat(runtime_id, time.time_ns()).SerializeToString()
        )

    async def on_update(msg: Msg) -> None:
        upd = ledger_pb2.LedgerUpdate()
        upd.ParseFromString(msg.data)
        async with lock:
            runner.on_ledger_update(upd)
        await msg.ack()

    async def publish_scored() -> None:
        for h, sb in runner.drain_scored():
            INFERENCE.labels(h.strategy_id, "features").observe(sb.feature_seconds)
            INFERENCE.labels(h.strategy_id, "score").observe(sb.score_seconds)
            for sig in signal_messages(h, sb):
                await js.publish(
                    f"sig.{h.strategy_id}", sig.SerializeToString(), headers={"Nats-Msg-Id": sig.signal_id}
                )
            for fv in feature_vector_messages(sb):
                await js.publish(
                    feature_subject(sb.feature_set_id, fv.instrument_id),
                    fv.SerializeToString(),
                    headers={"Nats-Msg-Id": f"fv|{h.strategy_id}|{fv.instrument_id}|{fv.as_of}"},
                )

    async def publish_intents(intents: list[orders_pb2.OrderIntent]) -> None:
        for it in intents:
            await js.publish(
                "ord.intent",
                it.SerializeToString(),
                headers={"Nats-Msg-Id": it.intent_id, "Tb-Correlation-Id": it.correlation_id},
            )
            log.info(
                "intent",
                extra={
                    "correlation_id": it.correlation_id,
                    "strategy": it.strategy_id,
                    "side": it.side,
                    "qty": it.quantity.value,
                },
            )

    async def on_bar(msg: Msg) -> None:
        bar = marketdata_pb2.Bar()
        bar.ParseFromString(msg.data)
        async with lock:
            intents = runner.on_bar(bar)
        await publish_intents(intents)
        try:
            await publish_scored()
        except Exception as exc:  # monitoring inputs never block intents
            log.warning("signal publish failed", extra={"err": str(exc)[:200]})
        await beat()

    async def on_signal(msg: Msg) -> None:
        sg = signals_pb2.Signal()
        sg.ParseFromString(msg.data)
        async with lock:
            dropped = runner.signals_dropped
            intents = runner.on_signal(sg, time.time_ns())
            if runner.signals_dropped > dropped:
                log.info(
                    "webhook signal dropped", extra={"strategy": sg.strategy_id, "correlation_id": sg.correlation_id}
                )
        await publish_intents(intents)

    async def on_context(msg: Msg) -> None:
        ev = context_pb2.ContextEvent()
        ev.ParseFromString(msg.data)
        async with lock:
            runner.on_context_event(ev)

    def load_events() -> None:
        if not lake_dir:
            return
        try:
            n = runner.load_events(ContextStore(Lake(lake_dir)), time.time_ns())
            log.info("blackout events loaded", extra={"events": n})
        except Exception as exc:  # the bus subscription still feeds new events
            log.warning("blackout events not loaded from the lake", extra={"err": str(exc)[:200]})

    start = _session_start_ns()
    subscribed_accounts: set[str] = set()
    subscribed_subjects: set[str] = set()

    async def subscribe_new() -> None:
        # Streams are named explicitly: this user may not call $JS.API.STREAM.NAMES (least privilege),
        # and an unnamed subscribe would time out on that lookup.
        for acct in runner.accounts():
            if acct in subscribed_accounts:
                continue
            subscribed_accounts.add(acct)
            await js.subscribe(
                f"ledger.update.{acct}",
                stream="LEDGER",
                cb=on_update,
                ordered_consumer=True,
                config=ConsumerConfig(
                    deliver_policy=DeliverPolicy.BY_START_TIME,
                    opt_start_time=datetime.fromtimestamp(start / 1e9, tz=UTC),
                ),
            )
        for subj in runner.subjects():
            if subj in subscribed_subjects:
                continue
            subscribed_subjects.add(subj)
            await js.subscribe(
                subj,
                stream="MARKETDATA",
                cb=on_bar,
                ordered_consumer=True,
                config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
            )

        for subj in runner.signal_subjects():
            if subj in subscribed_subjects:
                continue
            subscribed_subjects.add(subj)
            await js.subscribe(
                subj,
                stream="SIGNALS",
                cb=on_signal,
                ordered_consumer=True,
                config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
            )

    await subscribe_new()
    if runner.uses_events():
        # CX-016: scheduled events for EventBlackout; history from the lake, updates from the bus.
        load_events()
        for subj in ("ctx.calendar", "ctx.earnings"):
            await js.subscribe(
                subj,
                stream="CONTEXT",
                cb=on_context,
                ordered_consumer=True,
                config=ConsumerConfig(deliver_policy=DeliverPolicy.ALL),
            )
    log.info(
        "strategy runtime started",
        extra={"strategies": [h.strategy_id for h in runner.hosted], "subjects": len(runner.subjects())},
    )
    last_load = time.monotonic()
    last_reload, research_mtime = time.monotonic(), _mtime(research)
    while not stop.is_set():
        await beat()
        if research and time.monotonic() - last_reload >= reload_seconds:
            last_reload = time.monotonic()
            mtime = _mtime(research)
            if mtime != research_mtime:
                research_mtime = mtime
                async with lock:
                    added, removed = reload_research(runner, research, seed_ids or set())
                if added or removed:
                    await subscribe_new()
                    log.info("research deployments reloaded", extra={"added": added, "removed": removed})
        if runner.uses_events() and time.monotonic() - last_load > 3600:
            last_load = time.monotonic()
            async with lock:
                load_events()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    await nc.drain()


def _mtime(path: str) -> float:
    try:
        return Path(path).stat().st_mtime if path else 0.0
    except OSError:
        return 0.0


def research_entries(research: str, seed_ids: set[str]) -> list[dict[str, Any]]:
    """Shadow and paper entries of the optimizer's research deployments (EX-056); a research entry can
    never host a live strategy (N4, SM-004) or replace a seed entry."""
    if not research or not Path(research).exists():
        return []
    extra = json.loads(Path(research).read_text(encoding="utf-8")).get("strategies", [])
    return [e for e in extra if e.get("mode") in ("shadow", "paper") and e.get("strategy_id") not in seed_ids]


def load_runner(registry: str, research: str = "") -> Runner:
    """The seed registry plus the optimizer's research deployments (EX-056)."""
    data: dict[str, Any] = json.loads(Path(registry).read_text(encoding="utf-8"))
    seed_ids = {e["strategy_id"] for e in data.get("strategies", [])}
    data["strategies"] = [*data.get("strategies", []), *research_entries(research, seed_ids)]
    return Runner.from_registry_data(data)


def reload_research(runner: Runner, research: str, seed_ids: set[str]) -> tuple[list[str], list[str]]:
    """SM-016: host research entries added since the last read and drop retired ones; seed entries are
    never touched. A malformed file keeps the current set (fail closed: no new strategies)."""
    try:
        entries = research_entries(research, seed_ids)
    except (OSError, ValueError) as exc:
        log.warning("research deployments not reloaded", extra={"err": str(exc)[:200]})
        return [], []
    want = {e["strategy_id"] for e in entries}
    hosted_research = {h.strategy_id for h in runner.hosted} - seed_ids
    removed = runner.remove(hosted_research - want)
    try:
        added = runner.add_entries([e for e in entries if e["strategy_id"] not in hosted_research])
    except Exception as exc:  # one broken entry (missing bundle, bad class) must not stop the others
        log.warning("research deployment not hosted", extra={"err": str(exc)[:200]})
        added = []
    return added, removed


def main() -> None:
    settings = ServiceSettings(service="strategy-runtime")
    configure(settings.service, settings.log_level)
    registry = os.environ.get("TB_STRATEGY_REGISTRY", "/etc/tb/strategies.json")
    research = os.environ.get("TB_RESEARCH_REGISTRY", "")
    runner = load_runner(registry, research)
    seed_ids = {e["strategy_id"] for e in json.loads(Path(registry).read_text(encoding="utf-8")).get("strategies", [])}
    user = os.environ.get("TB_NATS_USER", "strategy_runtime")
    secrets_dir = settings.secrets_dir
    password = ""
    if user:
        try:
            password = get_secret(f"nats_{user}_password", secrets_dir).decode()
        except SecretNotFoundError:
            raise SystemExit(f"missing secret nats_{user}_password") from None
    if os.environ.get("TB_OPS_DISABLED") != "1":
        app = create_ops_app(settings)
        threading.Thread(
            target=lambda: uvicorn.run(
                app, host=settings.ops_host, port=settings.ops_port, log_config=None, access_log=False
            ),
            daemon=True,
        ).start()
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(
        run(
            runner,
            os.environ.get("TB_NATS_URL", "nats://nats:4222"),
            user,
            password,
            os.environ.get("TB_RUNTIME_ID", "strategy-runtime"),
            float(os.environ.get("TB_HEARTBEAT_INTERVAL", "5")),
            stop,
            os.environ.get("TB_LAKE_DIR", ""),
            seed_ids,
            research,
            float(os.environ.get("TB_RESEARCH_RELOAD_SECONDS", "30")),
        )
    )


if __name__ == "__main__":
    main()
