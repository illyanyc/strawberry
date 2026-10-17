"""Feature definitions, feature sets, and the shared computation path (section 03, ADR-0021).

A feature is a pure function that returns a Polars expression evaluated per instrument over a frame
of bars sorted by time (rules DF-003, DF-004, DF-013):

* it sees only the frame the framework passes (bars already filtered to ``available_at <= as_of``),
  never storage;
* the value at a row depends only on that row and the previous ``lookback - 1`` rows of the same
  instrument (finite memory), so offline materialization over the full history and the online
  engine over a ring buffer of ``lookback`` rows produce the same numbers;
* it is invariant to the price scale (returns, ratios, z-scores), so backward-adjusted prices as of
  any later date give the same value as prices adjusted as of the decision time.

``compute`` is the single code path used by ``materialize`` (offline) and ``OnlineFeatureEngine``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl

Kind = Literal["series", "context", "cross_sectional", "calendar"]

#: Columns every bar frame passed to ``compute`` must have.
BAR_COLUMNS = ["instrument_id", "ts_open", "ts_close", "available_at", "open", "high", "low", "close", "volume", "vwap"]


@dataclass(frozen=True)
class FeatureDef:
    name: str
    version: int
    inputs: tuple[str, ...]
    lookback: int
    kind: Kind
    fn: Callable[..., Any]
    timeframe: str | None = None
    # For context features: the context role whose bars feed fn (e.g. "market", "sector").
    role: str | None = None
    source_hash: str = ""
    # frame=True: fn(prepared_frame) -> pl.Series aligned with the frame (multi-step features such as
    # session aggregates); otherwise fn() -> pl.Expr evaluated per instrument.
    frame: bool = False

    def spec(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "inputs": list(self.inputs),
            "lookback": self.lookback,
            "kind": self.kind,
            "role": self.role,
            "timeframe": self.timeframe,
            "source": self.source_hash,
        }


REGISTRY: dict[str, FeatureDef] = {}


def feature(
    name: str,
    version: int,
    inputs: Sequence[str],
    lookback: int,
    timeframe: str | None = None,
    kind: Kind = "series",
    role: str | None = None,
    frame: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a feature. ``lookback`` is the number of bars (including the current one) the value
    depends on; it bounds the online ring buffer and the offline warm-up padding."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        src = hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()[:16]
        key = f"{name}@{version}"
        if key in REGISTRY and REGISTRY[key].source_hash != src:
            raise ValueError(f"feature {key} redefined with different code; bump the version")
        REGISTRY[key] = FeatureDef(name, version, tuple(inputs), lookback, kind, fn, timeframe, role, src, frame)
        return fn

    return deco


@dataclass(frozen=True)
class FeatureSet:
    name: str
    version: int
    features: tuple[FeatureDef, ...]
    # Context roles → instrument_id (e.g. {"market": <SPY id>}); "sector" is resolved per instrument.
    context: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, name: str, version: int, keys: Sequence[str], context: dict[str, str] | None = None) -> FeatureSet:
        return cls(name, version, tuple(REGISTRY[k] for k in keys), dict(context or {}))

    @property
    def hash(self) -> str:
        """Content hash: name, version, every feature's spec and code hash, context roles."""
        payload = {
            "name": self.name,
            "version": self.version,
            "features": [f.spec() for f in self.features],
            "context": sorted(self.context),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def max_lookback(self) -> int:
        return max(f.lookback for f in self.features)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.features]


def _ny(col: str) -> pl.Expr:
    return pl.col(col).dt.convert_time_zone("America/New_York")


def prepare(bars: pl.DataFrame) -> pl.DataFrame:
    """Add the derived columns features may use: exchange-local session date and regular-hours flag."""
    local = _ny("ts_open")
    minutes = local.dt.hour().cast(pl.Int32) * 60 + local.dt.minute().cast(pl.Int32)
    return bars.sort(["instrument_id", "ts_open"]).with_columns(
        local.dt.date().alias("session_date"),
        ((minutes >= 570) & (minutes < 960)).alias("is_regular"),
        (minutes - 570).alias("minute_of_session"),
        local.dt.weekday().alias("weekday"),
    )


def compute(
    fs: FeatureSet,
    bars: pl.DataFrame,
    sector_of: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Compute every feature of ``fs`` for every row of ``bars`` (adjusted bars of the universe and
    of the context instruments). Returns instrument_id, ts_open, available_at and one column per
    feature. This is the only computation path (offline and online, DF-004)."""
    if bars.is_empty():
        return pl.DataFrame(
            schema={"instrument_id": pl.Utf8, "ts_open": bars.schema.get("ts_open", pl.Datetime("ns", "UTC"))}
        )
    df = prepare(bars)
    series = [f for f in fs.features if f.kind in ("series", "calendar") and not f.frame]
    if series:
        df = df.with_columns([f.fn().over("instrument_id").alias(f.name) for f in series])
    for f in (x for x in fs.features if x.kind in ("series", "calendar") and x.frame):
        df = df.with_columns(f.fn(df).alias(f.name))
    for f in (x for x in fs.features if x.kind == "context"):
        ctx = df.select("instrument_id", "ts_open", f.fn().over("instrument_id").alias("_v"))
        if f.role == "sector":
            mapping = pl.DataFrame(
                {"instrument_id": list((sector_of or {}).keys()), "_ctx": list((sector_of or {}).values())},
                schema={"instrument_id": pl.Utf8, "_ctx": pl.Utf8},
            )
            df = df.join(mapping, on="instrument_id", how="left")
            df = df.join(ctx.rename({"instrument_id": "_ctx", "_v": f.name}), on=["_ctx", "ts_open"], how="left").drop(
                "_ctx"
            )
        else:
            ctx_id = fs.context.get(f.role or "")
            vals = ctx.filter(pl.col("instrument_id") == ctx_id).select("ts_open", pl.col("_v").alias(f.name))
            df = df.join(vals, on="ts_open", how="left")
    xs = [f for f in fs.features if f.kind == "cross_sectional"]
    if xs:
        ctx_ids = set(fs.context.values()) | set((sector_of or {}).values())
        universe = ~pl.col("instrument_id").is_in(list(ctx_ids))
        df = df.with_columns(
            [
                pl.when(universe)
                .then(f.fn().over("ts_open", pl.col("instrument_id").is_in(list(ctx_ids))))
                .alias(f.name)
                for f in xs
            ]
        )
    return df.select(["instrument_id", "ts_open", "available_at", *fs.names]).sort(["instrument_id", "ts_open"])
