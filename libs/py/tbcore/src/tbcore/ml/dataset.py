"""Dataset builder: features as of each decision bar + labels + metadata → an immutable snapshot (ML-004).

Features come from ``tbcore.features.compute`` (the single offline/online path, DF-004) and labels from
``tbcore.labels`` (ML-001, ML-002). A row's ``decision_time`` is its decision bar's ``available_at``; its
label ends at ``t1`` and is known at ``label_available_at``. Rows whose label is not known before the
period end (the holdout boundary at most) are dropped, so no label peeks past it (ML-020, EX-010).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import polars as pl

from tb.ml.v1 import training_pb2 as tp
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.vectorized import VectorData
from tbcore.data.lake import Lake
from tbcore.features.core import FeatureSet, compute
from tbcore.labels import residual_returns, triple_barrier
from tbcore.ml import featuresets, synthetic
from tbcore.ml.config import DAILY, bar_ns, to_dict
from tbcore.ml.rules import orb_trades, residual_reversal_signals

NS = 1_000_000_000
FINALITY_NS = 60 * NS
EPOCH = datetime(1970, 1, 1, tzinfo=UTC).date()
META_COLS = (
    "instrument_id",
    "ts_open",
    "decision_time",
    "era",
    "bar_idx",
    "end_bar",
    "y",
    "ret",
    "t1",
    "label_available_at",
    "vol",
    "regime",
    "tod",
    "ret_beyond",
)


@dataclass
class Dataset:
    frame: pl.DataFrame  # META_COLS + feature columns, sorted by decision_time
    features: list[str]
    task: str
    manifest: tp.DatasetManifest
    bars: pl.DataFrame  # universe + benchmark bars (int ns) up to the period end, for evaluation
    benchmark: str
    feature_set: FeatureSet

    @property
    def snapshot_id(self) -> str:
        return self.manifest.snapshot_id

    def vector_data(self) -> VectorData:
        u = self.bars.filter(pl.col("instrument_id") != self.benchmark)
        return VectorData.from_bars(u)

    def with_features(self, features: list[str]) -> Dataset:
        """Same rows and snapshot, fewer features (ablation drop, ML-025)."""
        m = tp.DatasetManifest()
        m.CopyFrom(self.manifest)
        del m.features[:]
        m.features.extend(features)
        return Dataset(
            self.frame.select([*META_COLS, *features]),
            features,
            self.task,
            m,
            self.bars,
            self.benchmark,
            self.feature_set,
        )


def to_ns(df: pl.DataFrame) -> pl.DataFrame:
    for c in ("ts_open", "ts_close", "available_at"):
        if c in df.columns and df.schema[c] != pl.Int64:
            df = df.with_columns(pl.col(c).dt.cast_time_unit("ns").dt.replace_time_zone("UTC").cast(pl.Int64))
    return df


def to_dt(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        [
            pl.col(c).cast(pl.Datetime("ns", "UTC"))
            for c in ("ts_open", "ts_close", "available_at")
            if df.schema[c] == pl.Int64
        ]
    )


@lru_cache(maxsize=8)
def _synthetic_bars(name: str) -> pl.DataFrame:
    return synthetic.generate(synthetic.preset(name))


def load_bars(cfg: tp.TrainingConfig, lake_root: str | None = None) -> tuple[pl.DataFrame, list[str], str]:
    """(bars, universe, benchmark) for the config's data source; bars include the benchmark."""
    src = cfg.data_source
    if src.startswith("synthetic:"):
        spec = synthetic.preset(src.split(":", 1)[1])
        if spec.timeframe != cfg.timeframe:
            raise ValueError(f"synthetic preset {src} is {spec.timeframe}, config wants {cfg.timeframe}")
        bars = _synthetic_bars(src.split(":", 1)[1])
        uni = list(cfg.universe.instrument_ids) or synthetic.universe(spec)
        bench = cfg.universe.benchmark or synthetic.MARKET_ID
        return bars.filter(pl.col("instrument_id").is_in([*uni, bench])), uni, bench
    if src == "lake":
        if not lake_root:
            raise ValueError("data_source=lake needs a lake root (TB_LAKE_DIR)")
        uni = list(cfg.universe.instrument_ids)
        bench = cfg.universe.benchmark
        if not uni or not bench:
            raise ValueError("lake datasets need universe.instrument_ids and universe.benchmark")
        warm = 400 * bar_ns(cfg.timeframe) if cfg.timeframe != DAILY else 400 * 86_400 * NS
        lake = Lake(lake_root)
        bars = lake.bars([*uni, bench], cfg.timeframe, cfg.period.start - warm, cfg.period.end, as_of=cfg.period.end)
        return to_ns(bars), uni, bench
    raise ValueError(f"unknown data source {src!r}")


def _era(ts_open: np.ndarray) -> np.ndarray:
    local = pl.Series(ts_open).cast(pl.Datetime("ns", "UTC")).dt.convert_time_zone("America/New_York").dt.date()
    return np.array([(d - EPOCH).days for d in local.to_list()], dtype=np.int64)


def regimes(bars: pl.DataFrame, benchmark: str) -> pl.DataFrame:
    """Market-volatility regime per bar from past data only: trailing 20-bar volatility of the benchmark
    vs its trailing 100-bar median (low < 0.8x, high > 1.25x)."""
    m = bars.filter(pl.col("instrument_id") == benchmark).sort("ts_open")
    r = (pl.col("close") / pl.col("close").shift(1)).log()
    m = m.with_columns(r.rolling_std(20).alias("_v"))
    m = m.with_columns(pl.col("_v").rolling_median(100, min_samples=20).alias("_med"))
    return m.select(
        "ts_open",
        pl.when(pl.col("_v") > 1.25 * pl.col("_med"))
        .then(pl.lit("high_vol"))
        .when(pl.col("_v") < 0.8 * pl.col("_med"))
        .then(pl.lit("low_vol"))
        .otherwise(pl.lit("normal_vol"))
        .alias("regime"),
    )


def _bar_index(bars: pl.DataFrame) -> pl.DataFrame:
    return bars.sort(["instrument_id", "ts_open"]).with_columns(
        pl.int_range(pl.len()).over("instrument_id").alias("bar_idx")
    )


def _labels(
    cfg: tp.TrainingConfig, bars: pl.DataFrame, universe: list[str], benchmark: str, vd: VectorData | None
) -> pl.DataFrame:
    lab = cfg.label
    h = lab.horizon
    daily = cfg.timeframe == DAILY
    if lab.kind in ("residual_return", "residual_rank"):
        lr = residual_returns(bars, [h], benchmark or None, vol_window=lab.vol_window, cap_at_close=not daily)
        ycol = f"label_h{h}" if lab.kind == "residual_return" else f"rank_h{h}"
        return lr.select(
            "instrument_id",
            "ts_open",
            pl.col(ycol).alias("y"),
            pl.col(f"ret_h{h}").alias("ret"),
            pl.col(f"t1_h{h}").alias("t1"),
            pl.col(f"label_available_at_h{h}").alias("label_available_at"),
            "vol",
        ).filter(pl.col("t1") > 0)
    if lab.kind == "meta_label":
        rule = cfg.primary.rule
        params = to_dict(cfg.primary.params) if cfg.primary.HasField("params") else {}
        if rule == "orb":
            if vd is None:
                raise ValueError("orb meta-labels need vector data")
            tr = orb_trades(vd, params, CostModelConfig())
            return tr.select(
                "instrument_id",
                "ts_open",
                pl.col("meta").cast(pl.Float64).alias("y"),
                pl.col("pnl").alias("ret"),
                "t1",
                (pl.col("t1") + FINALITY_NS).alias("label_available_at"),
                pl.lit(float("nan")).alias("vol"),
            )
        if rule == "residual_reversal":
            sig = residual_reversal_signals(
                bars,
                benchmark,
                lookback=int(params.get("lookback", 5)),
                vol_window=int(params.get("vol_window", 20)),
                z_entry=float(params.get("z_entry", 1.0)),
            ).filter(pl.col("signal"))
            tb = triple_barrier(
                bars.filter(pl.col("instrument_id") != benchmark),
                pt=lab.pt,
                sl=lab.sl,
                max_holding=h,
                vol_window=lab.vol_window,
            )
            j = sig.join(tb, on=["instrument_id", "ts_open"], how="inner").filter(pl.col("t1") > 0)
            return j.select(
                "instrument_id",
                "ts_open",
                (pl.col("ret") > 0).cast(pl.Float64).alias("y"),
                "ret",
                "t1",
                (pl.col("t1") + FINALITY_NS).alias("label_available_at"),
                pl.lit(float("nan")).alias("vol"),
            )
        raise ValueError(f"unknown primary rule {rule!r}")
    raise ValueError(f"unknown label kind {lab.kind!r}")


def _content_hash(frame: pl.DataFrame, features: list[str]) -> str:
    h = hashlib.sha256()
    h.update("\x1f".join(frame["instrument_id"].to_list()).encode())
    for c in ("ts_open", "y", *features):
        h.update(np.ascontiguousarray(frame[c].cast(pl.Float64).fill_null(np.nan).to_numpy()).tobytes())
    return h.hexdigest()


def build(
    cfg: tp.TrainingConfig,
    bars: pl.DataFrame,
    universe: list[str],
    benchmark: str,
    boundary: int | None = None,
    task: str = "regression",
) -> Dataset:
    """Build the dataset for a resolved config. ``boundary`` (holdout start) caps the period end."""
    end = cfg.period.end or int(bars["available_at"].cast(pl.Int64).max() or 0) + 1  # type: ignore[arg-type]
    if boundary:
        end = min(end, boundary)
    start = cfg.period.start
    fsd = featuresets.get(cfg.feature_set.name, cfg.feature_set.version)
    if cfg.timeframe not in fsd.timeframes:
        raise ValueError(f"feature set {fsd.name} is for {fsd.timeframes}, not {cfg.timeframe}")
    fs = fsd.resolve(benchmark)
    b = to_ns(bars).filter(pl.col("instrument_id").is_in([*universe, benchmark]))
    b = b.filter(pl.col("ts_open") < end)  # nothing at or after the boundary is ever read (EX-010)
    b = _bar_index(b)
    vd = (
        VectorData.from_bars(b.filter(pl.col("instrument_id") != benchmark)) if cfg.label.kind == "meta_label" else None
    )
    feats = to_ns(compute(fs, to_dt(b.drop("bar_idx"))))
    labels = _labels(cfg, b.drop("bar_idx"), universe, benchmark, vd)
    idx = b.select("instrument_id", "ts_open", "ts_close", "available_at", "bar_idx", "close")
    df = labels.join(idx, on=["instrument_id", "ts_open"], how="inner")
    df = df.join(feats.drop("available_at"), on=["instrument_id", "ts_open"], how="left")
    df = df.join(regimes(b, benchmark), on="ts_open", how="left").with_columns(pl.col("regime").fill_null("unknown"))
    total = df.height
    dropped: dict[str, int] = {}

    def drop(mask: pl.Expr, reason: str) -> None:
        nonlocal df
        before = df.height
        df = df.filter(~mask)
        if before - df.height:
            dropped[reason] = dropped.get(reason, 0) + before - df.height

    drop(pl.col("instrument_id") == benchmark, "benchmark")
    drop(pl.col("bar_idx") < fs.max_lookback, "warmup")
    drop(pl.col("y").is_null() | pl.col("y").is_nan(), "missing_label")
    drop(pl.col("available_at") < start, "before_period")
    drop(pl.col("label_available_at") >= end, "label_past_period_end")
    # End bar of each label within its instrument (sample weights and purging use bar spans).
    close_idx = idx.select("instrument_id", pl.col("ts_close").alias("t1"), pl.col("bar_idx").alias("end_bar"))
    df = df.join(close_idx, on=["instrument_id", "t1"], how="left").with_columns(
        pl.col("end_bar").fill_null(pl.col("bar_idx") + cfg.label.horizon)
    )
    # Diagnostics only: return over the horizon *after* the label ends (future-correlation leakage flag).
    fwd = idx.select("instrument_id", pl.col("bar_idx").alias("end_bar"), pl.col("close").alias("_c_end"))
    fwd2 = idx.select(
        "instrument_id", (pl.col("bar_idx") - cfg.label.horizon).alias("end_bar"), pl.col("close").alias("_c_after")
    )
    df = (
        df.join(fwd, on=["instrument_id", "end_bar"], how="left")
        .join(fwd2, on=["instrument_id", "end_bar"], how="left")
        .with_columns((pl.col("_c_after") / pl.col("_c_end") - 1.0).alias("ret_beyond"))
    )
    minute = (
        pl.col("ts_open").cast(pl.Datetime("ns", "UTC")).dt.convert_time_zone("America/New_York").dt.strftime("%H:00")
    )
    df = df.with_columns(
        pl.col("available_at").alias("decision_time"),
        minute.alias("tod"),
    ).sort(["decision_time", "instrument_id"])
    df = df.with_columns(pl.Series("era", _era(df["ts_open"].to_numpy())) if df.height else pl.lit(0).alias("era"))
    feature_names = list(fs.names)
    frame = df.select(
        [
            "instrument_id",
            "ts_open",
            "decision_time",
            "era",
            "bar_idx",
            "end_bar",
            pl.col("y").cast(pl.Float64),
            pl.col("ret").cast(pl.Float64),
            "t1",
            "label_available_at",
            pl.col("vol").cast(pl.Float64),
            "regime",
            "tod",
            pl.col("ret_beyond").cast(pl.Float64),
            *[pl.col(f).cast(pl.Float64) for f in feature_names],
        ]
    )
    ident = {
        "feature_set": fs.hash,
        "label": to_dict(cfg.label),
        "primary": to_dict(cfg.primary),
        "timeframe": cfg.timeframe,
        "period": [start, end],
        "data_source": cfg.data_source,
        "universe": sorted(universe),
        "benchmark": benchmark,
        "content": _content_hash(frame, feature_names) if frame.height else "",
    }
    snapshot = "ds_" + hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:32]
    y = frame["y"].to_numpy() if frame.height else np.array([])
    summary = (
        {"positive_rate": float(np.mean(y > 0.5)), "n": float(y.size)}
        if task == "classification" and y.size
        else ({"mean": float(np.mean(y)), "std": float(np.std(y)), "n": float(y.size)} if y.size else {"n": 0.0})
    )
    m = tp.DatasetManifest(
        snapshot_id=snapshot,
        feature_set_id=fs.id,
        feature_set_hash=fs.hash,
        label_spec_hash=hashlib.sha256(json.dumps(to_dict(cfg.label), sort_keys=True).encode()).hexdigest()[:16],
        timeframe=cfg.timeframe,
        period=tp.Period(start=start, end=end),
        rows=frame.height,
        instruments=frame["instrument_id"].n_unique() if frame.height else 0,
        eras=frame["era"].n_unique() if frame.height else 0,
        rows_dropped=dropped,
        label_summary=summary,
        features=feature_names,
        data_source=cfg.data_source,
        created_at=time.time_ns(),
    )
    m.rows_dropped["total_candidates"] = total
    return Dataset(frame, feature_names, task, m, b.drop("bar_idx"), benchmark, fs)


def save(ds: Dataset, root: str | Path) -> Path:
    """Write the snapshot once (immutable): ``<root>/<snapshot_id>/{data.parquet, manifest.json}``."""
    d = Path(root) / ds.snapshot_id
    if (d / "manifest.json").exists():
        return d
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "data.parquet.tmp"
    ds.frame.write_parquet(tmp)
    tmp.rename(d / "data.parquet")
    (d / "manifest.json").write_text(json.dumps(to_dict(ds.manifest), sort_keys=True, indent=1))
    return d
