"""Labels with end times (Gate 3, D24; DF-012, DF-014..DF-016)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import polars as pl
import pytest

from tbcore.backtest.synthetic import random_walk_bars
from tbcore.labels import meta_labels, residual_returns, triple_barrier
from tbcore.validation.splitters import CombinatorialPurgedCV

NS = 1_000_000_000


def two_instruments(n_sessions: int = 30) -> tuple[pl.DataFrame, str, str]:
    bars = random_walk_bars(2, n_sessions, 15, seed=5)
    a, b = sorted(bars["instrument_id"].unique().to_list())
    return bars, a, b


def test_df014_residual_label_hand_calculation_and_cap_at_close() -> None:
    bars, a, spy = two_instruments()
    lab = residual_returns(bars, [1, 4], benchmark=spy, vol_window=10)
    assert set(lab["instrument_id"].unique().to_list()) == {a}
    ga = bars.filter(pl.col("instrument_id") == a).sort("ts_open")
    gb = bars.filter(pl.col("instrument_id") == spy).sort("ts_open")
    k = 40  # a mid-session bar
    ca, cb = ga["close"].to_numpy(), gb["close"].to_numpy()
    r = np.log(ca[1:] / ca[:-1])
    vol = np.std(r[k - 10 : k], ddof=1)  # returns ending at bar k
    expect = ((ca[k + 1] / ca[k] - 1) - (cb[k + 1] / cb[k] - 1)) / vol
    row = lab.filter(pl.col("ts_open") == ga["ts_open"][k])
    assert row["label_h1"][0] == pytest.approx(expect)
    # Capped at the session close: the last bar of a session has no forward bar in its session.
    last_of_day = ga.with_columns(
        pl.col("ts_open").cast(pl.Datetime("ns", "UTC")).dt.convert_time_zone("America/New_York").dt.date().alias("d")
    )
    last_ts = last_of_day.group_by("d").agg(pl.col("ts_open").max())["ts_open"].to_list()
    capped = lab.filter(pl.col("ts_open").is_in(last_ts))
    assert capped["label_h4"].is_null().all() or capped["label_h4"].is_nan().all()
    near = lab.filter(pl.col("ts_open") == ga["ts_open"][24])  # 2 bars before the close of session 1
    assert near["t1_h4"][0] == ga["ts_close"][25]


def test_df015_labels_use_no_data_after_t1_and_scaler_no_data_after_t() -> None:
    bars, a, spy = two_instruments()
    full = residual_returns(bars, [4], benchmark=spy).filter(pl.col("label_h4").is_not_nan())
    row = full.row(200, named=True)
    t0, t1 = row["ts_open"], row["t1_h4"]
    after = pl.col("ts_close") > t1
    perturbed = bars.with_columns(
        pl.when(after).then(pl.col(c) * 1.5).otherwise(pl.col(c)).alias(c) for c in ("open", "high", "low", "close")
    )
    again = residual_returns(perturbed, [4], benchmark=spy)
    assert again.filter(pl.col("ts_open") == t0)["label_h4"][0] == pytest.approx(row["label_h4"])
    assert row["t1_h4"] > row["ts_open"] and row["label_available_at_h4"] > row["t1_h4"]
    shifted = bars.with_columns(
        pl.when(pl.col("ts_open") > t0).then(pl.col("close") * 2).otherwise(pl.col("close")).alias("close")
    )
    assert residual_returns(shifted, [1], benchmark=None).filter(
        (pl.col("ts_open") == t0) & (pl.col("instrument_id") == a)
    )["vol"][0] == pytest.approx(row["vol"])


def test_df014_swing_rank_and_legs() -> None:
    bars = random_walk_bars(6, 40, 390, seed=2)  # one bar per session = daily bars
    lab = residual_returns(bars, [1, 5], benchmark=None, vol_window=10, cap_at_close=False, legs=True)
    ranks = lab["rank_h1"].drop_nans().drop_nulls()
    assert ranks.min() == 0.0 and ranks.max() == 1.0
    ok = lab.filter(pl.col("ret_overnight").is_not_nan())
    gross = (1 + ok["ret_overnight"]) * (1 + ok["ret_intraday"]) - 1
    assert np.allclose(gross.to_numpy(), ok["ret_h1"].to_numpy())


def test_df016_triple_barrier_and_meta_labels() -> None:
    t = int(datetime(2026, 10, 13, 13, 30, tzinfo=UTC).timestamp()) * NS
    # Rows 0..10 set a ~1 % volatility; entries at rows 11..14 face the next bar's range.
    close = [float(c) for c in (100, 101, 99, 100, 101, 100, 99, 100, 101, 100, 100, 100, 100, 100, 100, 100)]
    high = [c + 0.1 for c in close[:12]] + [104, 100.5, 104, 100.3]
    low = [c - 0.1 for c in close[:12]] + [99.9, 95, 95, 99.7]
    n = len(close)
    df = pl.DataFrame(
        {
            "instrument_id": ["X"] * n,
            "ts_open": [t + i * 900 * NS for i in range(n)],
            "open": close,
            "high": high,
            "low": low,
            "close": close,
        }
    ).with_columns(
        (pl.col("ts_open") + 900 * NS).alias("ts_close"), (pl.col("ts_open") + 960 * NS).alias("available_at")
    )
    tb = triple_barrier(df, pt=1.0, sl=1.0, max_holding=1, vol_window=10)
    lab = tb["label"].to_list()
    assert lab[11] == 1.0  # bar 12's high crosses the profit-take
    assert lab[12] == -1.0  # bar 13's low crosses the stop
    assert lab[13] == -1.0  # bar 14 crosses both: stop first (conservative)
    assert lab[14] == 0.0  # vertical barrier
    assert math.isnan(lab[15])  # no forward bar
    assert tb["t1"][11] == df["ts_close"][12]
    primary = pl.DataFrame(
        {"instrument_id": ["X", "X"], "ts_open": [df["ts_open"][11], df["ts_open"][12]], "side": [1, -1]}
    )
    assert meta_labels(primary, tb)["meta"].to_list() == [1, 1]


def test_df014_label_end_times_drive_purging() -> None:
    bars, _, spy = two_instruments()
    lab = residual_returns(bars, [4], benchmark=spy).filter(pl.col("label_h4").is_not_nan()).sort("decision_time")
    t0, t1 = lab["decision_time"].to_numpy(), lab["t1_h4"].to_numpy()
    t0 = np.minimum(t0, t1)
    for s in CombinatorialPurgedCV(6, 2, label_end_times=t1).split(t0):
        for g in np.split(s.test, np.flatnonzero(np.diff(s.test) != 1) + 1):
            assert not ((t0[s.train] <= t1[g].max()) & (t1[s.train] >= t0[g].min())).any()
