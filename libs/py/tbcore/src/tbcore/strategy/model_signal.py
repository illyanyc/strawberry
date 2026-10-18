"""Model-driven strategies shared by the event-driven backtester and the strategy runtime (SM-011, ML-040..ML-042).

* ``ModelSignalStrategy`` (runtime class ``model_signal``): long when the model's score clears the
  threshold fitted on training data, exit after ``horizon`` bars (intraday also before the close),
  protective stop ``stop_vol_mult`` trailing volatilities below the entry (N5).
* ``MetaFilterStrategy`` (``meta_filter``): wraps a primary rule (``orb`` or ``residual_reversal``) and
  drops its entries the filter model does not take (D23 rule + ML filter, ML-016).
* ``ResidualReversal`` (``residual_reversal``): the swing primary rule on ``tbcore.ml.rules.residual_z``.

Score sources: ``TableScores`` (out-of-fold or holdout decisions from a parquet table, backtests) and
``OnlineModelScores`` (a model bundle on ``OnlineFeatureEngine``, live and shadow). The online source
batches one bar time across the model's instruments because cross-sectional features need the whole
batch; decisions for a bar time are emitted when its last bar arrives.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import polars as pl

from tbcore.features.store import OnlineFeatureEngine
from tbcore.ml.bundle import ModelBundle, feature_vector_id
from tbcore.ml.featuresets import FEATURE_SETS
from tbcore.ml.rules import residual_z
from tbcore.strategy.api import Bar, Fill, Intent, Strategy, StrategyContext, new_ulid
from tbcore.strategy.sizing import fixed_fractional_qty

CENT = Decimal("0.01")
NS = 1_000_000_000


@dataclass(frozen=True)
class Decision:
    instrument_id: str
    take: bool
    score: float
    bar: Bar
    feature_snapshot_id: str = ""
    # Feature values the model scored (online sources only; published as FeatureVector, MN-038).
    features: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredBatch:
    """One online scoring of a bar time across the model's universe (ML-042, ML-043)."""

    ts_open: int
    decisions: list[Decision]
    model_version: str
    feature_set_id: str
    horizon_bars: int
    timeframe: str
    task: str
    # Wall time of the online feature update and of the model scoring (ML-043 measures scoring).
    feature_seconds: float
    score_seconds: float


class ScoreSource(Protocol):
    def decisions(self, bar: Bar) -> list[Decision]: ...


class TableScores:
    """Decisions from a table with columns ``instrument_id``, ``ts_open``, ``score``, ``take``."""

    def __init__(self, path: str | Path | pl.DataFrame) -> None:
        df = path if isinstance(path, pl.DataFrame) else pl.read_parquet(path)
        self._d: dict[tuple[str, int], tuple[float, bool]] = {
            (str(i), int(t)): (float(s), bool(k))
            for i, t, s, k in zip(df["instrument_id"], df["ts_open"], df["score"], df["take"], strict=True)
        }

    def decisions(self, bar: Bar) -> list[Decision]:
        v = self._d.get((bar.instrument_id, bar.ts_open))
        return [Decision(bar.instrument_id, v[1], v[0], bar)] if v else []


class OnlineModelScores:
    """A model bundle scored on the online feature engine (ML-040: feature-set mismatch fails closed)."""

    def __init__(self, bundle: ModelBundle, instruments: list[str]) -> None:
        name = bundle.feature_set_id.split("@")[0]
        fs = FEATURE_SETS[name].resolve(bundle.benchmark)
        bundle.check_features(fs.hash, fs.names, bundle.timeframe)
        self.bundle = bundle
        self.engine = OnlineFeatureEngine(fs)
        self.expected = set(instruments) | {bundle.benchmark}
        self._pending: dict[int, dict[str, Bar]] = {}
        # Called with every scored batch (the runtime publishes signals and measures latency).
        self.listeners: list[Callable[[ScoredBatch], None]] = []

    def decisions(self, bar: Bar) -> list[Decision]:
        batch = self._pending.setdefault(bar.ts_open, {})
        batch[bar.instrument_id] = bar
        if not self.expected <= set(batch):
            return []
        del self._pending[bar.ts_open]
        for t in [t for t in self._pending if t < bar.ts_open]:
            del self._pending[t]  # an incomplete older batch is skipped (missing bar), never guessed
        frame = pl.DataFrame(
            {
                "instrument_id": [b.instrument_id for b in batch.values()],
                "ts_open": pl.Series([b.ts_open for b in batch.values()]).cast(pl.Datetime("ns", "UTC")),
                "ts_close": pl.Series([b.ts_close for b in batch.values()]).cast(pl.Datetime("ns", "UTC")),
                "available_at": pl.Series([b.available_at for b in batch.values()]).cast(pl.Datetime("ns", "UTC")),
                "open": [float(b.open) for b in batch.values()],
                "high": [float(b.high) for b in batch.values()],
                "low": [float(b.low) for b in batch.values()],
                "close": [float(b.close) for b in batch.values()],
                "volume": [float(b.volume) for b in batch.values()],
                "vwap": [float((b.high + b.low + b.close) / 3) for b in batch.values()],
                "trade_count": [0.0 for _ in batch.values()],
            }
        )
        t0 = time.perf_counter()
        feats = self.engine.update(frame).filter(pl.col("instrument_id") != self.bundle.benchmark)
        t1 = time.perf_counter()
        if feats.is_empty():
            return []
        scores = self.bundle.score_frame(feats)
        t2 = time.perf_counter()
        out = []
        for row, s in zip(feats.iter_rows(named=True), scores, strict=True):
            vec = [row[f] for f in self.bundle.features]
            ok = math.isfinite(float(s))
            out.append(
                Decision(
                    str(row["instrument_id"]),
                    ok and float(s) >= self.bundle.threshold,
                    float(s) if ok else math.nan,
                    batch[str(row["instrument_id"])],
                    feature_vector_id(vec),
                    {
                        f: float(v) if v is not None else math.nan
                        for f, v in zip(self.bundle.features, vec, strict=True)
                    },
                )
            )
        if self.listeners:
            sb = ScoredBatch(
                ts_open=bar.ts_open,
                decisions=out,
                model_version=self.bundle.model_version or self.bundle.run_id,
                feature_set_id=self.bundle.feature_set_id,
                horizon_bars=self.bundle.horizon,
                timeframe=self.bundle.timeframe,
                task=self.bundle.task,
                feature_seconds=t1 - t0,
                score_seconds=t2 - t1,
            )
            for fn in self.listeners:
                fn(sb)
        return out


def score_source(params: dict[str, Any], instruments: list[str]) -> ScoreSource:
    if params.get("scores"):
        return TableScores(str(params["scores"]))
    if params.get("bundle"):
        return OnlineModelScores(ModelBundle.load(str(params["bundle"])), instruments)
    raise ValueError("model strategies need params.scores (table) or params.bundle (model)")


@dataclass
class _Held:
    entry_bar_ts: int
    bars: int = 0
    exit_sent: bool = False


@dataclass
class ModelSignalParams:
    horizon: int = 4
    risk_per_trade: Decimal = Decimal("0.005")
    stop_vol_mult: Decimal = Decimal(2)
    vol_window: int = 20
    exit_minutes_before_close: int = 15
    swing: bool = False
    benchmark: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModelSignalParams:
        return cls(
            horizon=int(d.get("horizon", 4)),
            risk_per_trade=Decimal(str(d.get("risk_per_trade", "0.005"))),
            stop_vol_mult=Decimal(str(d.get("stop_vol_mult", "2"))),
            vol_window=int(d.get("vol_window", 20)),
            exit_minutes_before_close=int(d.get("exit_minutes_before_close", 15)),
            swing=bool(d.get("swing", False)),
            benchmark=str(d.get("benchmark", "")),
        )


def _intent(
    sid: str, bar: Bar, side: str, qty: Decimal, kind: str, reason: str, stop: Decimal | None, tif: str
) -> Intent:
    ts_ms = bar.available_at // 1_000_000
    seed = f"{sid}|{bar.instrument_id}|{bar.available_at}|{kind}"
    return Intent(
        intent_id=new_ulid(ts_ms, seed),
        instrument_id=bar.instrument_id,
        side=side,
        qty=qty,
        kind=kind,
        reason=reason,
        reference_price=bar.close,
        stop_loss=stop,
        protective_tif=tif,
        correlation_id=new_ulid(ts_ms, seed + "|corr"),
    )


class ModelSignalStrategy:
    def __init__(self, strategy_id: str, params: ModelSignalParams, source: ScoreSource) -> None:
        self.strategy_id = strategy_id
        self.p = params
        self.source = source
        self._held: dict[str, _Held] = {}
        self._closes: dict[str, deque[float]] = {}
        self.last_decisions: list[Decision] = []

    def on_start(self, ctx: StrategyContext) -> None:
        self._held.clear()

    def _vol(self, inst: str) -> float:
        c = np.array(self._closes.get(inst, ()), dtype=np.float64)
        if c.size < 3:
            return math.nan
        return float(np.std(np.diff(np.log(c)), ddof=1))

    def _late(self, ctx: StrategyContext, bar: Bar) -> bool:
        if self.p.swing:
            return False
        s = ctx.session()
        if s is None:
            return True
        close_at = int(s.close.timestamp()) * NS - self.p.exit_minutes_before_close * 60 * NS
        return bar.ts_close >= close_at

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        if bar.available_at > ctx.now:  # DF-002
            return []
        self._closes.setdefault(bar.instrument_id, deque(maxlen=self.p.vol_window + 1)).append(float(bar.close))
        out: list[Intent] = []
        tif = "gtc" if self.p.swing else "day"
        if bar.instrument_id != self.p.benchmark:
            h = self._held.get(bar.instrument_id)
            held = ctx.position(bar.instrument_id)
            if h is not None and held > 0 and bar.ts_open > h.entry_bar_ts:
                h.bars += 1
                late = self._late(ctx, bar)
                if not h.exit_sent and (h.bars >= self.p.horizon or late):
                    h.exit_sent = True
                    out.append(_intent(self.strategy_id, bar, "sell", held, "close", "model horizon exit", None, tif))
            elif h is not None and held <= 0 and bar.ts_open > h.entry_bar_ts + NS:
                del self._held[bar.instrument_id]  # stopped out or never filled
        self.last_decisions = self.source.decisions(bar)
        for d in self.last_decisions:
            if not d.take or d.instrument_id == self.p.benchmark:
                continue
            if ctx.position(d.instrument_id) != 0 or d.instrument_id in self._held or self._late(ctx, d.bar):
                continue
            vol = self._vol(d.instrument_id)
            if not math.isfinite(vol) or vol <= 0:
                continue
            entry = d.bar.close
            drop = Decimal(str(vol)) * self.p.stop_vol_mult * Decimal(str(math.sqrt(self.p.horizon)))
            stop = (entry * (1 - drop)).quantize(CENT, rounding=ROUND_FLOOR)
            if stop >= entry or stop <= 0:
                continue
            qty = fixed_fractional_qty(ctx.equity_basis(), self.p.risk_per_trade, entry, stop, ctx.cash())
            if qty <= 0:
                continue
            self._held[d.instrument_id] = _Held(entry_bar_ts=d.bar.ts_open)
            out.append(
                _intent(
                    self.strategy_id, d.bar, "buy", qty, "open", f"model score {d.score:.4f} >= threshold", stop, tif
                )
            )
        return out

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return []

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return []


@dataclass
class ResidualReversalParams:
    benchmark: str
    lookback: int = 5
    vol_window: int = 20
    z_entry: float = 1.0
    holding: int = 5
    risk_per_trade: Decimal = Decimal("0.005")
    stop_vol_mult: Decimal = Decimal(2)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ResidualReversalParams:
        return cls(
            benchmark=str(d["benchmark"]),
            lookback=int(d.get("lookback", 5)),
            vol_window=int(d.get("vol_window", 20)),
            z_entry=float(d.get("z_entry", 1.0)),
            holding=int(d.get("holding", 5)),
            risk_per_trade=Decimal(str(d.get("risk_per_trade", "0.005"))),
            stop_vol_mult=Decimal(str(d.get("stop_vol_mult", "2"))),
        )


class ResidualReversal:
    """Swing rule: buy when the residual z-score is at or below ``-z_entry``; hold ``holding`` sessions;
    GTC protective stop (OE-014). The benchmark must be among the strategy's instruments."""

    def __init__(self, strategy_id: str, params: ResidualReversalParams) -> None:
        self.strategy_id = strategy_id
        self.p = params
        need = max(params.lookback, params.vol_window) + 1
        self._c: dict[str, deque[float]] = {}
        self._need = need
        self._held: dict[str, _Held] = {}

    def on_start(self, ctx: StrategyContext) -> None:
        self._held.clear()

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        if bar.available_at > ctx.now:
            return []
        self._c.setdefault(bar.instrument_id, deque(maxlen=self._need)).append(float(bar.close))
        if bar.instrument_id == self.p.benchmark:
            return []
        held = ctx.position(bar.instrument_id)
        h = self._held.get(bar.instrument_id)
        if h is not None and held > 0 and bar.ts_open > h.entry_bar_ts:
            h.bars += 1
            if not h.exit_sent and h.bars >= self.p.holding:
                h.exit_sent = True
                return [_intent(self.strategy_id, bar, "sell", held, "close", "holding period exit", None, "gtc")]
            return []
        if h is not None and held <= 0 and bar.ts_open > h.entry_bar_ts:
            del self._held[bar.instrument_id]
        if held != 0 or bar.instrument_id in self._held:
            return []
        c = np.array(self._c[bar.instrument_id], dtype=np.float64)
        m = np.array(self._c.get(self.p.benchmark, ()), dtype=np.float64)
        n = min(c.size, m.size)
        z = residual_z(c[-n:], m[-n:], self.p.lookback, self.p.vol_window) if n else math.nan
        if not math.isfinite(z) or z > -self.p.z_entry:
            return []
        r = np.diff(np.log(c))
        vol = float(np.std(r[-self.p.vol_window :], ddof=1)) if r.size >= 3 else math.nan
        if not math.isfinite(vol) or vol <= 0:
            return []
        entry = bar.close
        drop = Decimal(str(vol)) * self.p.stop_vol_mult * Decimal(str(math.sqrt(self.p.holding)))
        stop = (entry * (1 - drop)).quantize(CENT, rounding=ROUND_FLOOR)
        if stop >= entry or stop <= 0:
            return []
        qty = fixed_fractional_qty(ctx.equity_basis(), self.p.risk_per_trade, entry, stop, ctx.cash())
        if qty <= 0:
            return []
        self._held[bar.instrument_id] = _Held(entry_bar_ts=bar.ts_open)
        return [_intent(self.strategy_id, bar, "buy", qty, "open", f"residual z {z:.2f}", stop, "gtc")]

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return []

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return []


@dataclass
class MetaFilterStrategy:
    """Drops the primary's entries that the filter does not take (exits always pass through)."""

    strategy_id: str
    primary: Strategy
    source: ScoreSource
    blocked: int = 0
    _taken: dict[tuple[str, int], bool] = field(default_factory=dict)

    def on_start(self, ctx: StrategyContext) -> None:
        self.primary.on_start(ctx)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        for d in self.source.decisions(bar):
            self._taken[(d.instrument_id, d.bar.ts_open)] = d.take
        out = []
        for it in self.primary.on_bar(ctx, bar):
            if it.kind == "open" and not self._taken.get((it.instrument_id, bar.ts_open), False):
                self.blocked += 1
                continue
            out.append(it)
        return out

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return self.primary.on_fill(ctx, fill)

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return self.primary.on_session_close(ctx)
