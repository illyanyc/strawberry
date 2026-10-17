"""MVP reference features (section 03; section 10 extends). All are finite-lookback and
price-scale invariant (DF-013). Expressions are evaluated per instrument (``.over``) by ``compute``.

Lookbacks are in bars of the decision timeframe. Session-based features (``vwap_dist``,
``gap_prev_close``) declare a lookback covering two extended sessions of 15-minute bars (128).
"""

from __future__ import annotations

import math

import polars as pl

from tbcore.features.core import FeatureSet, feature

SESSION_BARS_15M_EXT = 64  # 04:00-20:00 ET in 15-minute bars


def _r1() -> pl.Expr:
    return (pl.col("close") / pl.col("close").shift(1)).log()


@feature("ret_1", 1, ["close"], lookback=2)
def ret_1() -> pl.Expr:
    """1-bar log return."""
    return _r1()


@feature("ret_4", 1, ["close"], lookback=5)
def ret_4() -> pl.Expr:
    """4-bar log return."""
    return (pl.col("close") / pl.col("close").shift(4)).log()


@feature("ret_16", 1, ["close"], lookback=17)
def ret_16() -> pl.Expr:
    """16-bar log return."""
    return (pl.col("close") / pl.col("close").shift(16)).log()


@feature("vol_16", 1, ["close"], lookback=17)
def vol_16() -> pl.Expr:
    """Rolling standard deviation of 1-bar log returns over 16 bars."""
    return _r1().rolling_std(16)


@feature("rsi_14", 1, ["close"], lookback=15)
def rsi_14() -> pl.Expr:
    """Cutler's RSI (simple moving averages, finite memory; Wilder's EWM form would violate DF-013)."""
    d = pl.col("close") - pl.col("close").shift(1)
    gain = pl.when(d > 0).then(d).otherwise(0.0).rolling_mean(14)
    loss = pl.when(d < 0).then(-d).otherwise(0.0).rolling_mean(14)
    return pl.when(loss == 0).then(100.0).otherwise(100.0 - 100.0 / (1.0 + gain / loss))


@feature("atr_14_pct", 1, ["high", "low", "close"], lookback=15)
def atr_14_pct() -> pl.Expr:
    """Average true range over 14 bars divided by the close."""
    pc = pl.col("close").shift(1)
    tr = pl.max_horizontal(pl.col("high") - pl.col("low"), (pl.col("high") - pc).abs(), (pl.col("low") - pc).abs())
    return tr.rolling_mean(14) / pl.col("close")


@feature("volume_z_20", 1, ["volume"], lookback=20)
def volume_z_20() -> pl.Expr:
    """z-score of log(volume) against its trailing 20-bar mean and standard deviation. log (not
    log1p) keeps it invariant to split-adjusted volume scaling (DF-013); null while a zero-volume
    bar is in the window."""
    x = pl.when(pl.col("volume") > 0).then(pl.col("volume").log())
    return (x - x.rolling_mean(20)) / x.rolling_std(20)


@feature("vwap_dist", 1, ["close", "vwap", "volume"], lookback=SESSION_BARS_15M_EXT, frame=True)
def vwap_dist(df: pl.DataFrame) -> pl.Series:
    """Relative distance of the close from the session's cumulative VWAP (resets each session date)."""
    keys = ["instrument_id", "session_date"]
    out = df.select(
        pv=(pl.col("vwap") * pl.col("volume")).cum_sum().over(keys),
        vv=pl.col("volume").cum_sum().over(keys),
        close=pl.col("close"),
    ).select(
        pl.when(pl.col("vv") > 0).then((pl.col("close") - pl.col("pv") / pl.col("vv")) / (pl.col("pv") / pl.col("vv")))
    )
    return out.to_series()


@feature("gap_prev_close", 1, ["open", "close"], lookback=2 * SESSION_BARS_15M_EXT, frame=True)
def gap_prev_close(df: pl.DataFrame) -> pl.Series:
    """log(first regular open of the session / last regular close of the previous session), for
    regular-hours bars; null otherwise or when the previous session is not in the frame."""
    keys = ["instrument_id", "session_date"]
    d = df.select(
        "instrument_id",
        "session_date",
        "is_regular",
        reg_close=pl.when(pl.col("is_regular")).then(pl.col("close")),
        reg_open=pl.when(pl.col("is_regular")).then(pl.col("open")),
    )
    sess = (
        d.group_by(keys, maintain_order=True)
        .agg(
            pl.col("reg_close").drop_nulls().last().alias("last_close"),
            pl.col("reg_open").drop_nulls().first().alias("first_open"),
        )
        .with_columns(pl.col("last_close").shift(1).over("instrument_id").alias("prev_close"))
    )
    out = d.join(sess, on=keys, how="left", maintain_order="left").select(
        pl.when(pl.col("is_regular")).then((pl.col("first_open") / pl.col("prev_close")).log())
    )
    return out.to_series()


@feature("tod_sin", 1, ["ts_open"], lookback=1, kind="calendar")
def tod_sin() -> pl.Expr:
    """Time of day (minutes since 09:30 ET over a 390-minute session), sine encoding."""
    return (pl.col("minute_of_session").cast(pl.Float64) * (2 * math.pi / 390.0)).sin()


@feature("tod_cos", 1, ["ts_open"], lookback=1, kind="calendar")
def tod_cos() -> pl.Expr:
    return (pl.col("minute_of_session").cast(pl.Float64) * (2 * math.pi / 390.0)).cos()


@feature("dow_sin", 1, ["ts_open"], lookback=1, kind="calendar")
def dow_sin() -> pl.Expr:
    """Day of week (Mon=1..Fri=5), sine encoding over a 5-day week."""
    return ((pl.col("weekday").cast(pl.Float64) - 1.0) * (2 * math.pi / 5.0)).sin()


@feature("dow_cos", 1, ["ts_open"], lookback=1, kind="calendar")
def dow_cos() -> pl.Expr:
    return ((pl.col("weekday").cast(pl.Float64) - 1.0) * (2 * math.pi / 5.0)).cos()


@feature("mkt_ret_4", 1, ["close"], lookback=5, kind="context", role="market")
def mkt_ret_4() -> pl.Expr:
    """4-bar log return of the market proxy (SPY) at the same bar."""
    return (pl.col("close") / pl.col("close").shift(4)).log()


@feature("sector_ret_4", 1, ["close"], lookback=5, kind="context", role="sector")
def sector_ret_4() -> pl.Expr:
    """4-bar log return of the instrument's sector ETF at the same bar (null when unmapped)."""
    return (pl.col("close") / pl.col("close").shift(4)).log()


@feature("xs_rank_ret_4", 1, ["ret_4"], lookback=5, kind="cross_sectional")
def xs_rank_ret_4() -> pl.Expr:
    """Cross-sectional percentile rank of ret_4 among universe members at the same bar."""
    return pl.col("ret_4").rank("average") / pl.col("ret_4").count()


MVP_FEATURES = [
    "ret_1@1",
    "ret_4@1",
    "ret_16@1",
    "vol_16@1",
    "rsi_14@1",
    "atr_14_pct@1",
    "volume_z_20@1",
    "vwap_dist@1",
    "gap_prev_close@1",
    "tod_sin@1",
    "tod_cos@1",
    "dow_sin@1",
    "dow_cos@1",
    "mkt_ret_4@1",
    "sector_ret_4@1",
    "xs_rank_ret_4@1",
]


def mvp_feature_set(market_instrument_id: str) -> FeatureSet:
    """The MVP intraday feature set (15-minute decision bars)."""
    return FeatureSet.of("mvp_intraday", 1, MVP_FEATURES, context={"market": market_instrument_id})
