"""Entrypoint: one adapter process per environment (paper or live), its gRPC server, and the ops
surface (OP-010)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import IO, Any

import asyncpg
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
from tb_broker_ibkr.instruments import IbkrContractResolver, StaticInstrumentResolver
from tb_broker_ibkr.journal import Journal, PostgresJournal, PostgresLiveEnablement
from tb_broker_ibkr.metrics import PrometheusMetrics
from tb_broker_ibkr.models import Environment
from tb_broker_ibkr.relay import OutboxRelay
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
    journal: Journal,
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
        resolver=IbkrContractResolver(StaticInstrumentResolver.from_file(settings.ibkr_instruments_path), orders_port),
        cursor=journal,
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
    background: list[asyncio.Future[None]] = []

    audit: AuditLog
    journal: Journal
    pool: asyncpg.Pool | None = None
    if settings.db_user:
        password = get_secret(f"pg_{settings.db_user}_password", settings.secrets_dir).decode()
        pool = await asyncpg.create_pool(settings.db_dsn(password), min_size=1, max_size=6, command_timeout=5)  # type: ignore[no-untyped-call]
        audit = PostgresAuditLog(pool)
        pg_journal = PostgresJournal(pool, env)
        await pg_journal.load()
        journal = pg_journal
        if enablement is None:
            enablement = await PostgresLiveEnablement.load(pool)
    else:
        log.error("TB_DB_USER not set: no audit log or outbox; every order request will be refused (SE-021)")
        audit = UnavailableAuditLog()
        journal = FileCursorStore(Path(settings.ibkr_state_dir) / f"cursor-{env.value}.json")

    nats_sink: NatsSink | None = None
    if settings.nats_user:
        password = get_secret(f"nats_{settings.nats_user}_password", settings.secrets_dir).decode()
        nats_sink = NatsSink(settings.nats_url, settings.nats_user, password, env, durable_via_outbox=journal.publishes)
        await nats_sink.connect()
        sinks.append(nats_sink)
        closers.append(nats_sink)
        if pool is not None and isinstance(journal, PostgresJournal):
            relay = OutboxRelay(pool, nats_sink)
            journal.set_kick(relay.kick)
            background.append(asyncio.ensure_future(relay.run()))

        async def nats_connected() -> None:
            if not nats_sink.is_connected():
                raise RuntimeError("nats disconnected")

        checks["nats"] = nats_connected

    recording: IO[str] | None = None
    if settings.ibkr_record_path:
        recording = Path(settings.ibkr_record_path).open("a", encoding="utf-8")  # noqa: SIM115
    adapter = build_adapter(settings, FanoutSink(*sinks), metrics, audit, journal, recording)
    ref.append(adapter)

    blocked = live_gate(settings, enablement or NoLiveEnablement(), accounts)
    if blocked is not None:
        log.warning("live IBKR adapter stopped", extra={"reason": blocked})
        await adapter.supervisor.mark_stopped(blocked)
    elif pool is not None and settings.ibkr_environment is Environment.LIVE:
        background.append(asyncio.ensure_future(watch_live_enablement(settings, adapter, pool, accounts)))

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
    tasks: list[asyncio.Future[Any]] = [
        asyncio.ensure_future(ops.serve()),
        asyncio.ensure_future(grpc_server.wait_for_termination()),
        *background,
    ]
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
        if pool is not None:
            await pool.close()  # type: ignore[no-untyped-call]
        if recording is not None:
            recording.close()


async def watch_live_enablement(
    settings: IbkrSettings, adapter: IbkrAdapter, pool: asyncpg.Pool, accounts: dict[str, str], interval: float = 60
) -> None:
    """IB-007: a revoked live enablement stops the live adapter within ``interval`` seconds."""
    while True:
        await asyncio.sleep(interval)
        try:
            gate = await PostgresLiveEnablement.load(pool)
        except Exception:
            log.warning("live enablement re-check failed", exc_info=True)
            continue
        reason = live_gate(settings, gate, accounts)
        if reason is not None:
            log.error("live enablement revoked; stopping the live IBKR adapter", extra={"reason": reason})
            await adapter.supervisor.mark_stopped(reason)
            adapter.disconnect()
            return


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
