"""HTTP access to public and vendor APIs: per-source rate limits, bounded retries with
``Retry-After``, no redirects, and key redaction in every error (SE-012).

Tests inject an ``httpx.MockTransport`` serving recorded fixtures; real-API tests are opt-in.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping
from typing import Any

import httpx

from tbcore.redact import redact

RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A request failed after retries; the message never contains query parameters or keys."""

    def __init__(self, source: str, status: int, detail: str) -> None:
        super().__init__(f"{source}: HTTP {status}: {redact(detail)[:200]}")
        self.source = source
        self.status = status


class SourceUnavailable(RuntimeError):
    """The source is not provided in this deployment (e.g. no Alpaca data account behind
    broker-gateway): reported as ``unavailable``, retried slowly, never alerted as stale."""


class RateLimiter:
    """Token bucket: at most ``per_minute`` requests per rolling minute."""

    def __init__(self, per_minute: float) -> None:
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self.interval == 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


class HttpSource:
    def __init__(
        self,
        name: str,
        base: str,
        per_minute: float,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retries: int = 3,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.limiter = RateLimiter(per_minute)
        self.retries = retries
        self.client = httpx.AsyncClient(
            base_url=base, headers=dict(headers or {}), transport=transport, timeout=timeout, follow_redirects=False
        )
        self.requests = 0
        self.errors = 0

    async def close(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, params: Mapping[str, Any] | None = None, json: Any = None) -> Any:
        last = ""
        status = 0
        for attempt in range(self.retries + 1):
            await self.limiter.wait()
            self.requests += 1
            try:
                resp = await self.client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                self.errors += 1
                last, status = type(exc).__name__, 0
            else:
                if resp.status_code < 300:
                    return resp.json() if resp.content.strip() else None  # FINRA: empty body = no rows
                self.errors += 1
                status, last = resp.status_code, resp.text
                if resp.status_code not in RETRY_STATUS:
                    break
                ra = resp.headers.get("Retry-After", "")
                if ra.isdigit():
                    await asyncio.sleep(min(float(ra), 60.0))
                    continue
            if attempt < self.retries:
                await asyncio.sleep(min(30.0, (2**attempt) * 0.5 + random.random() * 0.5))  # noqa: S311 - jitter
        raise SourceError(self.name, status, last)

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)
