"""Ticker → instrument_id resolution as of a record's event time (CX-010, DF-006), from the lake's
``ref/`` mirrors written by marketdata (the instrument master is never written here)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import polars as pl

from tbcore.data.lake import Lake

NS = 1_000_000_000


@dataclass
class Resolver:
    lake: Lake
    ttl_s: float = 300.0
    _symbols: pl.DataFrame | None = None
    _loaded: float = 0.0
    unmapped: dict[str, int] = field(default_factory=dict)

    def _table(self) -> pl.DataFrame:
        if self._symbols is None or time.monotonic() - self._loaded > self.ttl_s:
            try:
                self._symbols = self.lake.ref("symbols")
            except FileNotFoundError:
                self._symbols = pl.DataFrame(
                    schema={"instrument_id": pl.Utf8, "symbol": pl.Utf8, "valid_from": pl.Int64, "valid_to": pl.Int64}
                )
            self._loaded = time.monotonic()
        return self._symbols

    def resolve(self, symbol: str, at_ns: int) -> str | None:
        """instrument_id whose ticker was ``symbol`` at ``at_ns`` (None when unknown)."""
        t = self._table()
        hit = t.filter(
            (pl.col("symbol") == symbol.upper())
            & (pl.col("valid_from") <= at_ns)
            & ((pl.col("valid_to") == 0) | (pl.col("valid_to") > at_ns))
        )
        if hit.is_empty():
            self.unmapped[symbol.upper()] = self.unmapped.get(symbol.upper(), 0) + 1
            return None
        return str(hit["instrument_id"][0])

    def universe_symbols(self, universe: str, at_ns: int, override: list[str] | None = None) -> list[str]:
        """Current tickers of the point-in-time universe (or the configured override)."""
        if override:
            return sorted({s.upper() for s in override})
        try:
            ids = set(self.lake.universe(universe, at_ns))
        except FileNotFoundError:
            return []
        t = self._table().filter(
            pl.col("instrument_id").is_in(list(ids))
            & (pl.col("valid_from") <= at_ns)
            & ((pl.col("valid_to") == 0) | (pl.col("valid_to") > at_ns))
        )
        return sorted(t["symbol"].unique().to_list())
