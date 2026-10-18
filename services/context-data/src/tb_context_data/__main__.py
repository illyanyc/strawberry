"""context-data entrypoint (section 09, ADR-0032).

``python -m tb_context_data``                       serve: scheduled jobs + ops surface
``python -m tb_context_data backfill SOURCE START END``  historical load of one source
``python -m tb_context_data spike v6``              FMP earnings timing / entitlement probe (V6)

Context versions go to ``ctx.<kind>`` (stream CONTEXT); marketdata persists them to the lake.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import threading
import time
from datetime import date
from pathlib import Path

import nats
import uvicorn
from prometheus_client import CollectorRegistry

from tb.alerts.v1 import alerts_pb2
from tb_context_data.app import backfill, build_engine, build_sources, secret
from tb_context_data.config import Settings
from tb_context_data.publish import NatsPublisher
from tb_context_data.resolve import Resolver
from tb_context_data.spike_v6 import run_spike_v6
from tbcore.data.lake import Lake
from tbcore.logging import configure
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.service import create_ops_app
from tbcore.strategy.api import new_ulid

log = logging.getLogger("context-data")
RUNBOOK = "https://github.com/illyanyc/strawberry/blob/main/docs/runbooks/context-data.md"


async def _connect(s: Settings) -> tuple[object, object]:
    try:
        pw = get_secret(f"nats_{s.nats_user}_password", s.secrets_dir).decode()
    except SecretNotFoundError:
        raise SystemExit(f"missing secret nats_{s.nats_user}_password") from None
    nc = await nats.connect(
        servers=[s.nats_url], user=s.nats_user, password=pw, name="context-data", max_reconnect_attempts=-1
    )
    return nc, nc.jetstream()


BUS: dict[str, object] = {}


async def bus_ready() -> None:
    nc = BUS.get("nc")
    if nc is None or not getattr(nc, "is_connected", False):
        raise RuntimeError("bus not connected")


async def serve(s: Settings, registry: CollectorRegistry, stop: asyncio.Event) -> None:
    nc, js = await _connect(s)
    BUS["nc"] = nc
    pub = NatsPublisher(js, nc)
    lake = Lake(s.lake_dir)
    src = build_sources(s, Resolver(lake))
    engine, _ = build_engine(s, pub, registry, src, lake)

    async def alert(key: str, subject: str, message: str) -> None:
        a = alerts_pb2.Alert(
            alert_id=new_ulid(),
            severity=alerts_pb2.SEVERITY_WARNING,
            source="context-data",
            subject=subject,
            message=message,
            dedupe_key=key,
            runbook_url=RUNBOOK,
            occurred_at=time.time_ns(),
        )
        await nc.publish("alert.warning", a.SerializeToString())  # type: ignore[attr-defined]

    engine.alert = alert
    log.info("context-data running", extra={"jobs": [j.name for j in engine.jobs], "disabled": src.disabled})
    try:
        await engine.loop(stop)
    finally:
        for h in src.http:
            await h.close()
        await nc.drain()  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    s = Settings()
    configure(s.service, s.log_level)
    if args[:2] == ["spike", "v6"]:
        report = asyncio.run(run_spike_v6(secret("fmp_api_key", s), s.ctx_fmp_base))
        out = Path(args[2]) if len(args) > 2 else Path("spike-v6-report.json")
        out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(json.dumps(report["summary"], indent=2))
        return
    registry = CollectorRegistry()
    if args[:1] == ["backfill"]:
        if len(args) != 4:
            raise SystemExit(
                "usage: backfill SOURCE START END "
                "(SOURCE: calendar|earnings|fundamentals|macro|short_interest|filings|news|regime)"
            )
        asyncio.run(_backfill(s, registry, args[1], date.fromisoformat(args[2]), date.fromisoformat(args[3])))
        return
    app = create_ops_app(s, checks={"bus": bus_ready}, registry=registry)
    threading.Thread(
        target=lambda: uvicorn.run(app, host=s.ops_host, port=s.ops_port, log_config=None, access_log=False),
        daemon=True,
    ).start()
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(serve(s, registry, stop))


async def _backfill(s: Settings, registry: CollectorRegistry, source: str, start: date, end: date) -> None:
    nc, js = await _connect(s)
    lake = Lake(s.lake_dir)
    src = build_sources(s, Resolver(lake))
    engine, pipeline = build_engine(s, NatsPublisher(js, nc), registry, src, lake)
    n = await backfill(s, src, engine, pipeline, lake, source, start, end)
    log.info("backfill done", extra={"source": source, "versions": n, "disabled": src.disabled})
    await nc.drain()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
