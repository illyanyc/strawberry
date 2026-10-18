"""Job engine: per-source schedules, snapshot stamping, publishing, freshness, and health (CX-019).

Each job fetches a window, the :class:`~tb_context_data.records.Versioner` stamps new versions
(CX-004), and the publisher sends them on ``ctx.<kind>``. A job that has not succeeded within
``every * TB_CTX_STALE_FACTOR`` is stale: its health turns ``stale`` on ``sys.health.ctx.<source>``
and a warning alert is raised once (section 13 routes it).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from prometheus_client import CollectorRegistry, Counter, Gauge

from tb_context_data.http import SourceUnavailable
from tb_context_data.publish import Publisher
from tb_context_data.records import Record, Versioner

log = logging.getLogger("context-data")
NS = 1_000_000_000


@dataclass
class Job:
    name: str
    source: str
    every_s: float
    run: Callable[[int], Awaitable[list[Record]]]
    last_success: float = 0.0
    last_attempt: float = 0.0
    failures: int = 0
    stale_alerted: bool = False
    unavailable: bool = False


class Metrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.versions = Counter(
            "tb_context_versions_total", "Context versions published.", ["kind", "source"], registry=registry
        )
        self.runs = Counter(
            "tb_context_job_runs_total", "Context job runs by outcome.", ["ctx_job", "outcome"], registry=registry
        )
        self.last_success = Gauge(
            "tb_context_job_last_success_timestamp_seconds",
            "Last successful run of a context job.",
            ["ctx_job"],
            registry=registry,
        )
        self.stale = Gauge(
            "tb_context_source_stale",
            "1 when a context job missed its freshness limit (CX-019).",
            ["ctx_job"],
            registry=registry,
        )


AlertFn = Callable[[str, str, str], Awaitable[None]]


@dataclass
class Engine:
    publisher: Publisher
    versioner: Versioner
    metrics: Metrics
    jobs: list[Job] = field(default_factory=list)
    stale_factor: float = 3.0
    alert: AlertFn | None = None
    now: Callable[[], float] = time.time

    async def run_job(self, job: Job) -> int:
        job.last_attempt = self.now()
        fetched = int(job.last_attempt * NS)
        try:
            records = await job.run(fetched)
            outs = self.versioner.stamp(records, fetched)
            n = await self.publisher.publish(outs)
        except SourceUnavailable as exc:
            if not job.unavailable:
                log.warning("context source unavailable", extra={"job": job.name, "err": str(exc)[:200]})
            job.unavailable = True
            self.metrics.runs.labels(job.name, "unavailable").inc()
            await self.publisher.health(job.name, -1, 0.0, "unavailable")
            return 0
        except Exception as exc:
            job.failures += 1
            self.metrics.runs.labels(job.name, "error").inc()
            log.warning("context job failed", extra={"job": job.name, "err": str(exc)[:300], "failures": job.failures})
            await self._health(job)
            return 0
        job.failures, job.last_success, job.stale_alerted, job.unavailable = 0, self.now(), False, False
        for o in outs:
            self.metrics.versions.labels(o.kind, o.source).inc()
        self.metrics.runs.labels(job.name, "ok").inc()
        self.metrics.last_success.labels(job.name).set(job.last_success)
        log.info("context job ok", extra={"job": job.name, "records": len(records), "versions": n})
        await self._health(job)
        return n

    def is_stale(self, job: Job) -> bool:
        ref = job.last_success or 0.0
        return self.now() - ref > job.every_s * self.stale_factor if ref else job.failures > 0

    async def _health(self, job: Job) -> None:
        stale = self.is_stale(job)
        self.metrics.stale.labels(job.name).set(1 if stale else 0)
        age_ms = int((self.now() - job.last_success) * 1000) if job.last_success else -1
        state = "stale" if stale else ("degraded" if job.failures else "ok")
        try:
            await self.publisher.health(job.name, age_ms, float(job.failures), state)
        except Exception as exc:  # health is best effort; the metric still reports staleness
            log.warning("health publish failed", extra={"job": job.name, "err": str(exc)[:200]})
        if stale and not job.stale_alerted and self.alert is not None:
            job.stale_alerted = True
            await self.alert(
                f"ctx-stale-{job.name}",
                f"Context source {job.name} is stale",
                f"No successful {job.name} run for over {job.every_s * self.stale_factor:.0f} s (CX-019).",
            )

    async def loop(self, stop: asyncio.Event) -> None:
        """Run every job on its interval (first run immediately, small jitter) until ``stop``."""

        async def one(job: Job) -> None:
            await asyncio.sleep(random.random() * 2)  # noqa: S311 - jitter
            while not stop.is_set():
                await self.run_job(job)
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=max(job.every_s, 3600.0) if job.unavailable else job.every_s
                    )
                except TimeoutError:
                    continue

        await asyncio.gather(*(one(j) for j in self.jobs))
