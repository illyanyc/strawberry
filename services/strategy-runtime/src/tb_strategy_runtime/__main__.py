"""Strategy runtime entrypoint (section 07, ADR-0026).

Consumes final bars (``md.bar.<tf>.<instrument>``) and the hosted strategies' ledger updates
(``ledger.update.<account>``, own owner only), runs the strategies, and publishes intents on the
durable subject ``ord.intent`` with ``Nats-Msg-Id`` = intent ID (execution is idempotent on it,
OE-006). It never talks to brokers. A heartbeat goes to ``sys.health.strategy-runtime`` every
``TB_HEARTBEAT_INTERVAL`` seconds and after each bar (dead-man, RM-017).
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

from tb.context.v1 import context_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tbcore.context.store import ContextStore
from tbcore.data.lake import Lake
from tbcore.logging import configure
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.service import ServiceSettings, create_ops_app
from tbcore.strategy.host import Runner

log = logging.getLogger("strategy-runtime")


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

    async def on_bar(msg: Msg) -> None:
        bar = marketdata_pb2.Bar()
        bar.ParseFromString(msg.data)
        async with lock:
            intents = runner.on_bar(bar)
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
        await beat()

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
    # Streams are named explicitly: this user may not call $JS.API.STREAM.NAMES (least privilege),
    # and an unnamed subscribe would time out on that lookup.
    for acct in runner.accounts():
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
        await js.subscribe(
            subj,
            stream="MARKETDATA",
            cb=on_bar,
            ordered_consumer=True,
            config=ConsumerConfig(deliver_policy=DeliverPolicy.NEW),
        )
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
    while not stop.is_set():
        await beat()
        if runner.uses_events() and time.monotonic() - last_load > 3600:
            last_load = time.monotonic()
            async with lock:
                load_events()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
    await nc.drain()


def load_runner(registry: str, research: str = "") -> Runner:
    """The seed registry plus the optimizer's research deployments (EX-056): shadow and paper entries
    only; a research entry can never host a live strategy (N4, SM-004)."""
    data: dict[str, Any] = json.loads(Path(registry).read_text(encoding="utf-8"))
    if research and Path(research).exists():
        extra = json.loads(Path(research).read_text(encoding="utf-8")).get("strategies", [])
        known = {e["strategy_id"] for e in data.get("strategies", [])}
        data["strategies"] = [
            *data.get("strategies", []),
            *[e for e in extra if e.get("mode") in ("shadow", "paper") and e.get("strategy_id") not in known],
        ]
    return Runner.from_registry_data(data)


def main() -> None:
    settings = ServiceSettings(service="strategy-runtime")
    configure(settings.service, settings.log_level)
    registry = os.environ.get("TB_STRATEGY_REGISTRY", "/etc/tb/strategies.json")
    runner = load_runner(registry, os.environ.get("TB_RESEARCH_REGISTRY", ""))
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
        )
    )


if __name__ == "__main__":
    main()
