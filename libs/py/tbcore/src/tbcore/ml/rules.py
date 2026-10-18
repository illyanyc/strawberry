"""Primary rules for the rule + ML filter families (D23, ML-016).

* ``orb``: the shared opening-range breakout (``tbcore.strategy.orb`` / ``orb_vector``); each trade's
  outcome under the rule's own exits (stop, target, session exit) is its meta-label.
* ``residual_reversal`` (swing, daily bars): long when the instrument's trailing ``lookback``-session
  residual return vs the benchmark is at least ``z_entry`` trailing residual standard deviations below
  zero. ``residual_z`` is the single computation used by the vectorized path here and by the
  event-driven ``tbcore.strategy.residual_reversal.ResidualReversal`` strategy.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
import polars as pl

from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.vectorized import EntrySignals, VectorData, VectorizedResult, VectorRules, run_vectorized
from tbcore.strategy.orb_vector import orb_signals


def residual_z(inst_close: np.ndarray, mkt_close: np.ndarray, lookback: int, vol_window: int) -> float:
    """z-score of the residual log return over the last ``lookback`` bars, scaled by the residual
    one-bar volatility over the last ``vol_window`` bars; NaN without enough history. Uses only the
    arrays given (the caller passes history up to and including the decision bar)."""
    need = max(lookback, vol_window) + 1
    if inst_close.shape[0] < need or mkt_close.shape[0] < need:
        return math.nan
    a = np.log(inst_close[-need:])
    m = np.log(mkt_close[-need:])
    rr = np.diff(a) - np.diff(m)
    sd = float(np.std(rr[-vol_window:], ddof=1))
    if not math.isfinite(sd) or sd <= 0:
        return math.nan
    return float(np.sum(rr[-lookback:]) / (sd * math.sqrt(lookback)))


def residual_reversal_signals(
    bars: pl.DataFrame, benchmark: str, lookback: int = 5, vol_window: int = 20, z_entry: float = 1.0
) -> pl.DataFrame:
    """Per (instrument, decision bar): ``z`` and ``signal`` (z <= -z_entry). Bars are int-ns lake rows."""
    b = bars.sort(["instrument_id", "ts_open"])
    mkt = b.filter(pl.col("instrument_id") == benchmark).select("ts_open", pl.col("close").alias("_m"))
    out = []
    for (inst,), g in b.filter(pl.col("instrument_id") != benchmark).group_by(["instrument_id"], maintain_order=True):
        j = g.join(mkt, on="ts_open", how="inner")
        c = j["close"].to_numpy().astype(np.float64)
        m = j["_m"].to_numpy().astype(np.float64)
        z = np.array([residual_z(c[: i + 1], m[: i + 1], lookback, vol_window) for i in range(c.shape[0])])
        out.append(
            pl.DataFrame(
                {
                    "instrument_id": [str(inst)] * j.height,
                    "ts_open": j["ts_open"],
                    "z": z,
                    "signal": np.nan_to_num(z, nan=0.0) <= -z_entry,
                }
            )
        )
    return pl.concat(out) if out else pl.DataFrame(schema={"instrument_id": pl.Utf8, "ts_open": pl.Int64})


def orb_trades(
    d: VectorData, params: Mapping[str, Any], cost: CostModelConfig, allocation: float = 100_000.0
) -> pl.DataFrame:
    """Every ORB trade with its signal bar (``ts_open``), label end (``t1`` = close of the exit bar),
    and outcome ``meta`` (1 when net P&L > 0)."""
    res = run_vectorized(orb_signals, d, cost, VectorRules(allocation=allocation), dict(params))
    assert isinstance(res, VectorizedResult)
    tr = res.trades
    if tr.is_empty():
        return pl.DataFrame(schema={"instrument_id": pl.Utf8, "ts_open": pl.Int64, "t1": pl.Int64, "meta": pl.Int8})
    ti = {int(t): k for k, t in enumerate(d.ts_open)}
    entry_idx = np.array([ti[int(t)] for t in tr["entry_time"].to_numpy()])
    exit_idx = np.array([ti[int(t)] for t in tr["exit_time"].to_numpy()])
    return pl.DataFrame(
        {
            "instrument_id": tr["instrument_id"],
            "ts_open": d.ts_open[entry_idx - 1],
            "t1": d.ts_close[exit_idx],
            "pnl": tr["pnl"],
            "meta": (tr["pnl"].to_numpy() > 0).astype(np.int8),
        }
    )


def filtered_orb_result(
    d: VectorData,
    params: Mapping[str, Any],
    take: set[tuple[str, int]],
    cost: CostModelConfig,
    allocation: float = 100_000.0,
) -> VectorizedResult:
    """ORB with the ML filter: an entry at signal bar t survives only when (instrument, ts_open[t]) is
    in ``take``. Same engine and costs as ``orb_trades``."""
    inst_idx = {s: k for k, s in enumerate(d.instruments)}
    mask = np.zeros(d.close.shape, dtype=bool)
    ti = {int(t): k for k, t in enumerate(d.ts_open)}
    for inst, ts in take:
        if inst in inst_idx and int(ts) in ti:
            mask[ti[int(ts)], inst_idx[inst]] = True

    def fn(dd: VectorData, p: Mapping[str, Any]) -> EntrySignals:
        s = orb_signals(dd, p)
        return EntrySignals(entry=s.entry & mask, stop=s.stop, target=s.target, exit=s.exit, side=s.side)

    res = run_vectorized(fn, d, cost, VectorRules(allocation=allocation), dict(params))
    assert isinstance(res, VectorizedResult)
    return res
