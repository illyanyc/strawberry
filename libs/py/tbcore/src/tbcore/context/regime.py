"""Point-in-time regime labels (CX-017), version ``regime_v1``.

Inputs: SPY daily bars (``ts_close``, ``close``, ``available_at``) and the VIX / VIX3M series
(``available_at``, ``value``; FRED ``VIXCLS`` / ``VXVCLS`` from ALFRED). Each output row for a session
uses only data with ``available_at`` at or before that row's ``available_at`` (the SPY bar's), via
backward as-of joins; rolling windows look back only. Labels:

* ``vol_regime``: SPY 20-day realized volatility percentile over the last 252 sessions — ``high``
  (>= 0.8), ``low`` (<= 0.2), else ``normal``;
* ``trend_regime``: 20-day efficiency ratio >= 0.3 with the 20-day return and the price vs its
  200-day mean agreeing — ``uptrend`` / ``downtrend``, else ``chop``;
* ``stress``: VIX term structure inverted (VIX / VIX3M > 1) or VIX percentile >= 0.9.

Rows without enough history are labelled ``unknown``. Changing any rule bumps ``REGIME_VERSION``.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from tbcore.data.lake import asof_join

REGIME_VERSION = "regime_v1"
RV_WINDOW = 20
PCT_WINDOW = 252
SMA_WINDOW = 200
ER_THRESHOLD = 0.3


def _pct_rank(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    arr = np.array([np.nan if v is None else v for v in values], dtype=float)
    for i in range(len(arr)):
        lo = i - window + 1
        if lo < 0 or math.isnan(arr[i]):
            out.append(None)
            continue
        w = arr[lo : i + 1]
        w = w[~np.isnan(w)]
        out.append(float((w <= arr[i]).mean()) if len(w) >= window // 2 else None)
    return out


def compute_regimes(
    spy: pl.DataFrame, vix: pl.DataFrame | None = None, vix3m: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Regime rows, one per SPY session (see module docstring)."""
    d = spy.select("ts_close", "close", "available_at").sort("ts_close")
    lr = pl.col("close").log().diff()
    d = d.with_columns(
        (lr.rolling_std(RV_WINDOW) * math.sqrt(252)).alias("spy_rv20"),
        (pl.col("close") / pl.col("close").rolling_mean(SMA_WINDOW) - 1).alias("spy_vs_sma200"),
        (pl.col("close").log() - pl.col("close").log().shift(RV_WINDOW)).alias("_ret20"),
        lr.abs().rolling_sum(RV_WINDOW).alias("_path20"),
    ).with_columns((pl.col("_ret20").abs() / pl.col("_path20")).alias("trend_er20"))
    d = d.with_columns(pl.Series("rv_pct_252", _pct_rank(d["spy_rv20"].to_list(), PCT_WINDOW), dtype=pl.Float64))
    d = d.with_columns(pl.lit("SPY").alias("_k"))
    for name, series in (("vix", vix), ("vix3m", vix3m)):
        if series is None or series.is_empty():
            d = d.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
            continue
        s = series.select(pl.col("available_at").alias(f"_{name}_at"), pl.col("value").cast(pl.Float64).alias(name))
        s = s.drop_nulls(name).with_columns(pl.lit("SPY").alias("_k"))
        d = asof_join(d, s, on="_k", left_time="available_at", right_time=f"_{name}_at").drop(f"_{name}_at")
    d = d.with_columns(pl.Series("vix_pct_252", _pct_rank(d["vix"].to_list(), PCT_WINDOW), dtype=pl.Float64))
    d = d.with_columns((pl.col("vix") / pl.col("vix3m")).alias("vix_term_ratio"))
    vol = (
        pl.when(pl.col("rv_pct_252").is_null())
        .then(pl.lit("unknown"))
        .when(pl.col("rv_pct_252") >= 0.8)
        .then(pl.lit("high"))
        .when(pl.col("rv_pct_252") <= 0.2)
        .then(pl.lit("low"))
        .otherwise(pl.lit("normal"))
    )
    strong = pl.col("trend_er20") >= ER_THRESHOLD
    trend = (
        pl.when(pl.col("spy_vs_sma200").is_null() | pl.col("trend_er20").is_null())
        .then(pl.lit("unknown"))
        .when(strong & (pl.col("_ret20") > 0) & (pl.col("spy_vs_sma200") > 0))
        .then(pl.lit("uptrend"))
        .when(strong & (pl.col("_ret20") < 0) & (pl.col("spy_vs_sma200") < 0))
        .then(pl.lit("downtrend"))
        .otherwise(pl.lit("chop"))
    )
    stress = (pl.col("vix_term_ratio") > 1).fill_null(False) | (pl.col("vix_pct_252") >= 0.9).fill_null(False)
    return (
        d.with_columns(vol.alias("vol_regime"), trend.alias("trend_regime"), stress.alias("stress"))
        .with_columns(
            (pl.col("vol_regime") + pl.lit("|") + pl.col("trend_regime")).alias("label"),
            pl.lit(REGIME_VERSION).alias("version"),
        )
        .select(
            pl.col("ts_close").alias("event_time"),
            "available_at",
            "version",
            "vol_regime",
            "trend_regime",
            "stress",
            "label",
            "spy_rv20",
            "rv_pct_252",
            "trend_er20",
            "spy_vs_sma200",
            "vix",
            "vix_pct_252",
            "vix_term_ratio",
        )
    )
