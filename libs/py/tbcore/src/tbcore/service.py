"""Python service template (rule OP-010): typed settings, JSON logs, and an ops app exposing
``/healthz``, ``/readyz``, and ``/metrics``.

Placeholder services call :func:`run`; real services build on :func:`create_ops_app` and register
readiness checks.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    gc_collector,
    generate_latest,
    platform_collector,
    process_collector,
)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from tbcore import __version__
from tbcore.logging import configure

ReadinessCheck = Callable[[], Awaitable[None]]


class ServiceSettings(BaseSettings):
    """Settings shared by every Python service, read from ``TB_*`` environment variables."""

    model_config = SettingsConfigDict(env_prefix="TB_", extra="ignore")

    service: str
    environment: str = "local"
    log_level: str = Field(default="info", pattern="^(debug|info|warning|error)$")
    ops_host: str = "0.0.0.0"  # noqa: S104 - binds inside the container on the internal network only
    ops_port: int = 8081
    secrets_dir: str = "/run/secrets"


def create_ops_app(
    settings: ServiceSettings,
    checks: dict[str, ReadinessCheck] | None = None,
    registry: CollectorRegistry | None = None,
) -> FastAPI:
    """Build the ops app with health, readiness, and Prometheus metrics endpoints.

    Services pass their own ``registry`` to export service metrics alongside the defaults.
    """
    registry = registry if registry is not None else CollectorRegistry()
    process_collector.ProcessCollector(registry=registry)
    platform_collector.PlatformCollector(registry=registry)
    gc_collector.GCCollector(registry=registry)
    build = Gauge("tb_build_info", "Build information.", ["service", "version"], registry=registry)
    build.labels(settings.service, __version__).set(1)
    started = Gauge("tb_process_start_time_seconds", "Service start time.", registry=registry)
    started.set(time.time())
    checks = checks or {}

    app = FastAPI(title=f"{settings.service} ops", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": settings.service}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        results: dict[str, str] = {}
        for name, check in checks.items():
            try:
                await check()
                results[name] = "ok"
            except Exception:
                results[name] = "fail"
        ready = all(v == "ok" for v in results.values())
        body = {"ready": ready, "service": settings.service, "checks": results}
        return JSONResponse(body, status_code=200 if ready else 503)

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return app


def run(settings: ServiceSettings | None = None) -> None:
    """Run a placeholder service: only the ops surface, until its section is built."""
    settings = settings or ServiceSettings()  # type: ignore[call-arg]
    log = configure(settings.service, settings.log_level)
    log.info("starting placeholder service", extra={"environment": settings.environment})
    uvicorn.run(
        create_ops_app(settings),
        host=settings.ops_host,
        port=settings.ops_port,
        log_config=None,
        access_log=False,
    )
