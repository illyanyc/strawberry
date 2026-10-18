"""A deliberately leaky strategy for the leakage suite (BT-065): it reads the next bar's close."""

from __future__ import annotations

from decimal import Decimal

import polars as pl

from tbcore.strategy.api import Bar, Fill, Intent, StrategyContext, new_ulid
from tbcore.strategy.host import CLASSES, register_class

FUTURE: dict[tuple[str, int], Decimal] = {}


def peek_from(bars: pl.DataFrame) -> None:
    """The fixture's "feature" step: every bar's *next* close (a negative shift, the look-ahead the
    point-in-time lint forbids), recomputed from the run's bars like any materialized feature."""
    FUTURE.clear()
    b = bars.sort(["instrument_id", "ts_open"]).with_columns(
        pl.col("close").shift(-1).over("instrument_id").alias("_next")
    )
    for i, ts, nxt in b.select("instrument_id", "ts_open", "_next").iter_rows():
        if nxt is not None:
            FUTURE[(i, ts)] = Decimal(repr(float(nxt)))


class PeekNext:
    def __init__(self, strategy_id: str, params: dict[str, object]) -> None:
        self.strategy_id = strategy_id

    def on_start(self, ctx: StrategyContext) -> None:
        return None

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        nxt = FUTURE.get((bar.instrument_id, bar.ts_open))
        held = ctx.position(bar.instrument_id)
        seed = f"{self.strategy_id}|{bar.instrument_id}|{bar.available_at}"
        ts = bar.available_at // 1_000_000
        if held > 0:
            return [Intent(new_ulid(ts, seed + "x"), bar.instrument_id, "sell", held, "close", "peek exit", bar.close)]
        if nxt is not None and nxt > bar.close * Decimal("1.002"):
            stop = (bar.close * Decimal("0.98")).quantize(Decimal("0.01"))
            return [Intent(new_ulid(ts, seed), bar.instrument_id, "buy", Decimal(10), "open", "peek", bar.close, stop)]
        return []

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return []

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return []


if "peek_next" not in CLASSES:
    register_class("peek_next", lambda sid, params: PeekNext(sid, params))
