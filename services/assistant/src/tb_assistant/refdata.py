"""Known tickers and instrument IDs for masking and the egress guard (AN-051, AN-052).

Read from ``refdata`` (role ``refdata_reader``) and refreshed hourly; every ticker the platform has
ever mapped is masked, including delisted and renamed ones.
"""

from __future__ import annotations

import time
from typing import Any


class PgSymbols:
    def __init__(self, pool: Any, ttl_seconds: float = 3600.0) -> None:
        self._pool = pool
        self._ttl = ttl_seconds
        self._at = 0.0
        self._symbols: set[str] = set()
        self._ids: set[str] = set()

    async def known(self) -> tuple[set[str], set[str]]:
        if time.monotonic() - self._at > self._ttl or not self._at:
            async with self._pool.acquire() as c:
                syms = await c.fetch("SELECT DISTINCT symbol FROM refdata.symbols")
                ids = await c.fetch("SELECT instrument_id FROM refdata.instruments")
            self._symbols = {r["symbol"] for r in syms if r["symbol"]}
            self._ids = {r["instrument_id"] for r in ids if r["instrument_id"]}
            self._at = time.monotonic()
        return set(self._symbols), set(self._ids)
