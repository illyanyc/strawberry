"""Labels with end times for purging (Gate 3; D24; DF-012, DF-014..DF-016).

* ``residual_returns``: volatility-scaled residual forward returns, the default target (D24).
  Intraday: return over ``h`` decision bars capped at the session's last bar, minus the benchmark's
  return over the same interval, divided by trailing realized volatility x sqrt(bars spanned).
  Swing (``cap_at_close=False`` on daily bars): the same over ``h`` sessions, plus a per-date rank
  and separate overnight and intraday legs for h = 1.
* ``triple_barrier``: volatility-scaled profit-take / stop-loss barriers and a vertical barrier.
* ``meta_labels``: 1 when a primary signal's side agreed with the triple-barrier outcome.
* ``sample_weights`` (uniqueness, return attribution, time decay) and ``rank_relevance`` (LambdaRank
  grades), section 10 (ML-001, ML-003).

Every label carries ``t1`` (close of the last bar it uses; splitters purge on it) and
``label_available_at`` (when it becomes known). The volatility scaler uses bars up to and including
the decision bar only.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import polars as pl

NS = 1_000_000_000


def _prep(bars: pl.DataFrame) -> pl.DataFrame:
    df = bars
    for c in ("ts_open", "ts_close", "available_at"):
        if df.schema[c] != pl.Int64:
            df = df.with_columns(pl.col(c).dt.cast_time_unit("ns").dt.replace_time_zone("UTC").cast(pl.Int64))
    local = pl.col("ts_open").cast(pl.Datetime("ns", "UTC")).dt.convert_time_zone("America/New_York")
    return df.sort(["instrument_id", "ts_open"]).with_columns(local.dt.date().alias("session_date"))


def _trailing_vol(close: np.ndarray, window: int) -> np.ndarray:
    """Std of one-bar log returns over the last ``window`` bars ending at each row (causal)."""
    r = np.full(close.shape, np.nan)
    r[1:] = np.log(close[1:] / close[:-1])
    out = np.full(close.shape, np.nan)
    for i in range(window, close.size):
        w = r[i - window + 1 : i + 1]
        out[i] = np.std(w, ddof=1)
    return out


def residual_returns(
    bars: pl.DataFrame,
    horizons: Sequence[int],
    benchmark: str | None,
    vol_window: int = 20,
    cap_at_close: bool = True,
    legs: bool = False,
) -> pl.DataFrame:
    """Volatility-scaled residual forward returns per (instrument, bar); ``benchmark`` is an
    instrument ID in ``bars`` (SPY or the sector ETF), or None for raw returns."""
    df = _prep(bars)
    bench: dict[int, float] = {}
    if benchmark is not None:
        b = df.filter(pl.col("instrument_id") == benchmark)
        bench = dict(zip(b["ts_open"].to_list(), b["close"].to_list(), strict=True))
    frames = []
    for inst, g in df.partition_by("instrument_id", as_dict=True, maintain_order=True).items():
        n = g.height
        close = g["close"].to_numpy().astype(np.float64)
        opn = g["open"].to_numpy().astype(np.float64)
        ts_open, ts_close, avail = g["ts_open"].to_numpy(), g["ts_close"].to_numpy(), g["available_at"].to_numpy()
        sess = g["session_date"].to_numpy()
        last_in_sess = np.zeros(n, dtype=np.int64)
        last = n - 1
        for k in range(n - 1, -1, -1):
            if k < n - 1 and sess[k] != sess[k + 1]:
                last = k
            last_in_sess[k] = last
        vol = _trailing_vol(close, vol_window)
        cols: dict[str, object] = {
            "instrument_id": [str(inst[0]) if isinstance(inst, tuple) else str(inst)] * n,
            "ts_open": ts_open,
            "decision_time": avail,
        }
        idx = np.arange(n)
        for h in horizons:
            end = np.minimum(idx + h, last_in_sess) if cap_at_close else idx + h
            valid = (end < n) & (end > idx)
            end_c = np.clip(end, 0, n - 1)
            ret = np.where(valid, close[end_c] / close - 1.0, np.nan)
            if benchmark is not None:
                b0 = np.array([bench.get(int(t), np.nan) for t in ts_open])
                b1 = np.array([bench.get(int(t), np.nan) for t in ts_open[end_c]])
                ret = ret - (b1 / b0 - 1.0)
            span = np.maximum(end_c - idx, 1)
            cols[f"label_h{h}"] = np.where(valid, ret / (vol * np.sqrt(span)), np.nan)
            cols[f"ret_h{h}"] = ret
            cols[f"t1_h{h}"] = np.where(valid, ts_close[end_c], 0)
            cols[f"label_available_at_h{h}"] = np.where(valid, avail[end_c], 0)
        if legs:
            nxt = np.clip(idx + 1, 0, n - 1)
            ok = idx + 1 < n
            cols["ret_overnight"] = np.where(ok, opn[nxt] / close - 1.0, np.nan)
            cols["ret_intraday"] = np.where(ok, close[nxt] / opn[nxt] - 1.0, np.nan)
        cols["vol"] = vol
        frames.append(pl.DataFrame(cols))
    out = pl.concat(frames).sort(["ts_open", "instrument_id"])
    if benchmark is not None:
        out = out.filter(pl.col("instrument_id") != benchmark)
    return out.with_columns(
        [
            (
                (pl.col(f"label_h{h}").rank("average").over("ts_open") - 1)
                / (pl.col(f"label_h{h}").count().over("ts_open") - 1)
            ).alias(f"rank_h{h}")
            for h in horizons
        ]
    )


def triple_barrier(
    bars: pl.DataFrame, pt: float = 1.0, sl: float = 1.0, max_holding: int = 4, vol_window: int = 20, side: int = 1
) -> pl.DataFrame:
    """Triple-barrier outcome for an entry at every bar's close: +1 when the profit-take barrier
    (``pt`` x trailing volatility) is hit first, -1 for the stop-loss (also when both are hit in the
    same bar: conservative), 0 at the vertical barrier after ``max_holding`` bars."""
    df = _prep(bars)
    frames = []
    for inst, g in df.partition_by("instrument_id", as_dict=True, maintain_order=True).items():
        close, high, low = (g[c].to_numpy().astype(np.float64) for c in ("close", "high", "low"))
        ts_close, avail = g["ts_close"].to_numpy(), g["available_at"].to_numpy()
        vol = _trailing_vol(close, vol_window)
        n = g.height
        lab = np.full(n, np.nan)
        ret = np.full(n, np.nan)
        t1 = np.zeros(n, dtype=np.int64)
        for i in range(n):
            if not np.isfinite(vol[i]) or i + 1 >= n:
                continue
            up = close[i] * (1 + side * pt * vol[i])  # profit-take
            dn = close[i] * (1 - side * sl * vol[i])  # stop-loss
            j_end = min(i + max_holding, n - 1)
            lab[i], t1[i], ret[i] = 0.0, ts_close[j_end], side * (close[j_end] / close[i] - 1)
            for j in range(i + 1, j_end + 1):
                hit_dn = low[j] <= dn if side > 0 else high[j] >= dn
                hit_up = high[j] >= up if side > 0 else low[j] <= up
                if hit_dn or hit_up:
                    lab[i] = -1.0 if hit_dn else 1.0
                    t1[i] = ts_close[j]
                    ret[i] = side * ((dn if hit_dn else up) / close[i] - 1)
                    break
        name = str(inst[0]) if isinstance(inst, tuple) else str(inst)
        frames.append(
            pl.DataFrame(
                {
                    "instrument_id": [name] * n,
                    "ts_open": g["ts_open"],
                    "decision_time": avail,
                    "label": lab,
                    "ret": ret,
                    "t1": t1,
                }
            )
        )
    return pl.concat(frames).sort(["ts_open", "instrument_id"])


def sample_weights(
    instrument: np.ndarray,
    start_bar: np.ndarray,
    end_bar: np.ndarray,
    method: str = "uniqueness",
    returns: np.ndarray | None = None,
    decay: float = 1.0,
    normalize: bool = True,
) -> np.ndarray:
    """Sample weights for one fold's training rows (ML-003); pass only those rows.

    Each row ``i`` uses bars ``(start_bar[i], end_bar[i]]`` of its instrument (bar indices within the
    instrument). ``uniqueness`` is the mean of 1/concurrency over the span (López de Prado 2018, 4.4);
    ``return`` scales uniqueness by |sum of one-bar ``returns`` over the span / concurrency|;
    ``time_decay`` multiplies uniqueness by a linear ramp from ``decay`` (oldest) to 1 (newest).
    Weights are normalized to mean 1. ``none`` returns ones.
    """
    n = instrument.shape[0]
    if method == "none" or n == 0:
        return np.ones(n)
    if method not in ("uniqueness", "return", "time_decay"):
        raise ValueError(f"unknown sample weighting {method!r}")
    w = np.zeros(n)
    for inst in np.unique(instrument):
        rows = np.flatnonzero(instrument == inst)
        s, e = start_bar[rows].astype(np.int64), end_bar[rows].astype(np.int64)
        lo, hi = int(s.min()), int(e.max())
        conc = np.zeros(hi - lo + 2)
        np.add.at(conc, s - lo + 1, 1.0)
        np.add.at(conc, e - lo + 1, -1.0)
        conc = np.cumsum(conc)  # conc[b - lo] = labels covering bar b (spans are (s, e])
        inv = np.where(conc > 0, 1.0 / np.maximum(conc, 1.0), 0.0)
        cum = np.r_[0.0, np.cumsum(inv)]
        span = np.maximum(e - s, 1)
        uniq = (cum[e - lo + 1] - cum[s - lo + 1]) / span
        if method == "return" and returns is not None:
            r = returns[rows]
            uniq = np.abs(r) * uniq
        w[rows] = uniq
    if method == "time_decay":
        order = np.argsort(np.argsort(start_bar, kind="stable"), kind="stable")
        ramp = decay + (1.0 - decay) * (order / max(n - 1, 1))
        w = w * ramp
    if not normalize:
        return w
    mean = float(w.mean())
    return w / mean if mean > 0 else np.ones(n)


def rank_relevance(rank: np.ndarray, buckets: int = 5) -> np.ndarray:
    """Integer relevance grades 0..buckets-1 from per-date ranks in [0, 1] (LambdaRank target, ML-001)."""
    r = np.nan_to_num(np.asarray(rank, dtype=np.float64), nan=0.0)
    return np.minimum((r * buckets).astype(np.int64), buckets - 1)


def meta_labels(primary: pl.DataFrame, barrier: pl.DataFrame) -> pl.DataFrame:
    """Meta-label (1 = take the primary signal) from ``primary`` (instrument_id, ts_open, side in
    {-1, +1}) and a long-side triple-barrier frame with symmetric barriers (``pt == sl``), for which
    the short trade's outcome is the negated long outcome: the trade's return is side x barrier return."""
    j = primary.join(
        barrier.select("instrument_id", "ts_open", "ret", "t1"), on=["instrument_id", "ts_open"], how="inner"
    )
    return j.with_columns(((pl.col("side") * pl.col("ret")) > 0).cast(pl.Int8).alias("meta"))
