"""Entrypoint: one adapter process per environment (paper or live), its gRPC server, and the ops
surface (OP-010)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import IO

import uvicorn
from prometheus_client import CollectorRegistry

from tb_broker_ibkr.adapter import AdapterConfig, IbkrAdapter
from tb_broker_ibkr.audit import AuditLog, PostgresAuditLog, UnavailableAuditLog
from tb_broker_ibkr.bus import NatsSink
from tb_broker_ibkr.clock import SystemClock
from tb_broker_ibkr.config import IbkrSettings, LiveEnablement, NoLiveEnablement, live_gate
from tb_broker_ibkr.cursor_store import FileCursorStore
from tb_broker_ibkr.events import EventSink, FanoutSink, LogSink
from tb_broker_ibkr.fakes.replay import Recorder
from tb_broker_ibkr.grpc_service import AUDIENCE, BrokerGatewayServicer, StreamHub, build_server
from tb_broker_ibkr.ib_async_port import IbAsyncPort
from tb_broker_ibkr.instruments import StaticInstrumentResolver
from tb_broker_ibkr.metrics import PrometheusMetrics
from tb_broker_ibkr.svcauth import Verifier
from tbcore.logging import configure
from tbcore.secrets import get_secret
from tbcore.service import ReadinessCheck, create_ops_app

log = logging.getLogger("tb_broker_ibkr")


def build_adapter(
    settings: IbkrSettings,
    sink: EventSink,
    metrics: PrometheusMetrics,
    audit: AuditLog,
    recording: IO[str] | None,
) -> IbkrAdapter:
    clock = SystemClock()
    recorder = Recorder(recording, clock.now()) if recording is not None else None
    env = settings.ibkr_environment
    orders_port = IbAsyncPort(
        settings.ibkr_host,
        settings.ibkr_port,
        settings.ibkr_orders_client_id,
        connect_timeout=settings.ibkr_connect_timeout,
        recorder=recorder,
    )
    md_port = IbAsyncPort(
        settings.ibkr_host,
        settings.ibkr_port,
        settings.ibkr_marketdata_client_id,
        connect_timeout=settings.ibkr_connect_timeout,
    )
    return IbkrAdapter(
        config=AdapterConfig(
            environment=env,
            accounts=settings.account_map(),
            order_ref_max_len=settings.ibkr_order_ref_max_len,
            submit_timeout=settings.ibkr_submit_timeout,
            commission_wait=settings.ibkr_commission_wait,
            snapshot_interval=settings.ibkr_snapshot_interval,
        ),
        port=orders_port,
        md_port=md_port,
        clock=clock,
        sink=sink,
        resolver=StaticInstrumentResolver.from_file(settings.ibkr_instruments_path),
        cursor=FileCursorStore(Path(settings.ibkr_state_dir) / f"cursor-{env.value}.json"),
        schedule=settings.schedule(),
        metrics=metrics,
        audit=audit,
    )


async def serve(settings: IbkrSettings, enablement: LiveEnablement | None = None) -> None:
    registry = CollectorRegistry()
    env = settings.ibkr_environment
    metrics = PrometheusMetrics(registry, env.value)
    accounts = settings.account_map()
    checks: dict[str, ReadinessCheck] = {}
    closers: list[object] = []

    ref: list[IbkrAdapter] = []
    hub = StreamHub(env, lambda: ref[0].supervisor.state)
    sinks: list[EventSink] = [LogSink(), metrics, hub]
    if settings.nats_user:
        password = get_secret(f"nats_{settings.nats_user}_password", settings.secrets_dir).decode()
        nats_sink = NatsSink(settings.nats_url, settings.nats_user, password, env)
        await nats_sink.connect()
        sinks.append(nats_sink)
        closers.append(nats_sink)

        async def nats_connected() -> None:
            if not nats_sink.is_connected():
                raise RuntimeError("nats disconnected")

        checks["nats"] = nats_connected

    audit: AuditLog
    if settings.db_user:
        password = get_secret(f"pg_{settings.db_user}_password", settings.secrets_dir).decode()
        postgres_audit = await PostgresAuditLog.connect(settings.db_dsn(password))
        closers.append(postgres_audit)
        audit = postgres_audit
    else:
        log.error("TB_DB_USER not set: no audit log, every order request will be refused (SE-021)")
        audit = UnavailableAuditLog()

    recording: IO[str] | None = None
    if settings.ibkr_record_path:
        recording = Path(settings.ibkr_record_path).open("a", encoding="utf-8")  # noqa: SIM115
    adapter = build_adapter(settings, FanoutSink(*sinks), metrics, audit, recording)
    ref.append(adapter)

    blocked = live_gate(settings, enablement or NoLiveEnablement(), accounts)
    if blocked is not None:
        log.warning("live IBKR adapter stopped", extra={"reason": blocked})
        await adapter.supervisor.mark_stopped(blocked)

    async def gateway_session() -> None:
        if not adapter.supervisor.is_ready:
            raise RuntimeError(adapter.supervisor.state.value)

    checks["gateway_session"] = gateway_session

    callers = [c.strip() for c in settings.grpc_callers.split(",") if c.strip()]
    verifier = Verifier.from_secrets(AUDIENCE, callers, settings.secrets_dir)
    grpc_server, _ = build_server(BrokerGatewayServicer(adapter, hub), verifier, settings.grpc_addr)
    await grpc_server.start()

    ops = uvicorn.Server(
        uvicorn.Config(
            create_ops_app(settings, checks, registry=registry),
            host=settings.ops_host,
            port=settings.ops_port,
            log_config=None,
            access_log=False,
        )
    )
    tasks = [asyncio.ensure_future(ops.serve()), asyncio.ensure_future(grpc_server.wait_for_termination())]
    if blocked is None:
        tasks.append(asyncio.ensure_future(adapter.run()))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        adapter.stop()
        await grpc_server.stop(5)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for closer in closers:
            with contextlib.suppress(Exception):
                await closer.close()  # type: ignore[attr-defined]
        if recording is not None:
            recording.close()


def main() -> None:
    settings = IbkrSettings()
    configure(settings.service, settings.log_level)
    log.info(
        "starting IBKR adapter",
        extra={"environment": settings.ibkr_environment.value, "gateway": f"{settings.ibkr_host}:{settings.ibkr_port}"},
    )
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
