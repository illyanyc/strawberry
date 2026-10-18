"""Lineage-level overfitting statistics over the whole trial ledger (EX-021, EX-023, EX-025; D31 rules 2, 3).

* The ledger's out-of-fold session-return series are aligned on the union of session days (a missing
  session counts as a 0 return: the trial had no position).
* Effective N: ONC clusters of the usable series (>= ``MIN_OBS`` sessions); trials without a usable
  series (pruned early, failed, abandoned) count at the same redundancy ratio; each holdout
  evaluation adds ``inflation`` full trials (EX-013).
* DSR of the best trial and of any candidate use that N and the cross-trial Sharpe variance; PBO is
  CSCV (S = 16) on the aligned matrix.
* MinBTL cycle budget: the largest effective N whose MinBTL is within the years of data (EX-025).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.validation.stats import (
    deflated_sharpe,
    effective_trials,
    min_backtest_length,
    min_track_record_length,
    moments,
    pbo_cscv,
    sharpe,
)

F64 = NDArray[np.float64]
MIN_OBS = 10
DSR_GATE = 0.95


@dataclass(frozen=True)
class Series:
    days: NDArray[np.int64]
    rets: F64


@dataclass
class Matrix:
    trial_ids: list[str]
    days: NDArray[np.int64]
    values: F64  # (T, N)


def align(series: dict[str, Series]) -> Matrix:
    usable = {k: s for k, s in series.items() if s.rets.size >= MIN_OBS}
    if not usable:
        return Matrix([], np.zeros(0, dtype=np.int64), np.zeros((0, 0)))
    days = np.unique(np.concatenate([s.days for s in usable.values()]))
    pos = {int(d): i for i, d in enumerate(days)}
    ids = sorted(usable)
    m = np.zeros((days.size, len(ids)))
    for j, k in enumerate(ids):
        s = usable[k]
        idx = np.array([pos[int(d)] for d in s.days])
        np.add.at(m[:, j], idx, s.rets)
    return Matrix(ids, days, m)


@dataclass(frozen=True)
class EffectiveN:
    n_trials: int
    n_effective: float
    sr_variance: float
    usable: int


def effective_n(trials: list[op.Trial], mat: Matrix, holdout_touches: int = 0, inflation: int = 1) -> EffectiveN:
    n_all = len(trials)
    usable = len(mat.trial_ids)
    if usable == 0:
        return EffectiveN(n_all + holdout_touches * inflation, float(max(n_all, 1)), 0.0, 0)
    et = effective_trials(mat.values)
    ratio = et.n_effective / usable
    unusable = max(n_all - usable, 0)
    n_eff = et.n_effective + unusable * ratio + holdout_touches * inflation
    return EffectiveN(n_all + holdout_touches * inflation, float(max(n_eff, 1.0)), float(et.sr_variance), usable)


def dsr_of(rets: F64, eff: EffectiveN) -> float:
    r = np.asarray(rets, dtype=np.float64)
    if r.size < 3:
        return math.nan
    sr = sharpe(r)
    if not math.isfinite(sr):
        return math.nan
    skew, kurt = moments(r)
    return float(deflated_sharpe(sr, eff.n_effective, eff.sr_variance, skew, kurt, r.size))


def cycle_budget(available_years: float, target_sharpe: float = 1.0) -> float:
    """Largest effective N with MinBTL(N, target) <= available years (EX-025); at least 1."""
    if available_years <= 0:
        return 1.0
    lo, hi = 1.0, 1e6
    if min_backtest_length(hi, target_sharpe) <= available_years:
        return hi
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if min_backtest_length(mid, target_sharpe) <= available_years:
            lo = mid
        else:
            hi = mid
    return lo


def sharpe_needed(n_eff: float, sr_variance: float, n_obs: int, threshold: float = DSR_GATE) -> float:
    """Smallest per-session Sharpe whose DSR (normal returns) reaches ``threshold`` at ``n_eff``."""
    if n_obs < 3:
        return math.inf
    lo, hi = -1.0, 5.0
    if deflated_sharpe(hi, n_eff, sr_variance, 0.0, 3.0, n_obs) < threshold:
        return math.inf
    for _ in range(60):
        mid = (lo + hi) / 2
        if deflated_sharpe(mid, n_eff, sr_variance, 0.0, 3.0, n_obs) >= threshold:
            hi = mid
        else:
            lo = mid
    return hi


def cumulative(
    lineage_id: str,
    trials: list[op.Trial],
    series: dict[str, Series],
    holdout_touches: int = 0,
    inflation: int = 1,
    cycle: op.ResearchCycle | None = None,
    target_sharpe: float = 1.0,
) -> op.CumulativeOverfittingStats:
    mat = align(series)
    eff = effective_n(trials, mat, holdout_touches, inflation)
    out = op.CumulativeOverfittingStats(
        lineage_id=lineage_id,
        n_trials=eff.n_trials,
        n_effective=eff.n_effective,
        sr_variance=eff.sr_variance,
        dsr_threshold=DSR_GATE,
    )
    if not mat.trial_ids:
        out.significance_reachable = True
        return out
    srs = np.array([sharpe(series[t].rets) for t in mat.trial_ids])
    srs = np.nan_to_num(srs, nan=-np.inf)
    j = int(np.argmax(srs))
    best_id = mat.trial_ids[j]
    s = series[best_id]
    out.best_trial_id = best_id
    out.best_sharpe = float(srs[j]) if math.isfinite(srs[j]) else 0.0
    d = dsr_of(s.rets, eff)
    out.deflated_sharpe_prob = d if math.isfinite(d) else 0.0
    if mat.values.shape[0] >= 16 and mat.values.shape[1] >= 2:
        out.pbo = float(pbo_cscv(mat.values, 16).pbo)
    skew, kurt = moments(s.rets)
    mtrl = min_track_record_length(out.best_sharpe, 0.0, skew, kurt) if out.best_sharpe > 0 else math.inf
    out.min_track_record_length = mtrl if math.isfinite(mtrl) else 0.0
    out.min_backtest_length_years = float(min_backtest_length(eff.n_effective, target_sharpe))
    out.available_years = float((mat.days.max() - mat.days.min()) / 365.25) if mat.days.size else 0.0
    n_max = cycle.effective_trial_budget if cycle is not None and cycle.effective_trial_budget else eff.n_effective * 2
    need = sharpe_needed(
        max(n_max, eff.n_effective), eff.sr_variance, int(np.median([x.rets.size for x in series.values()]))
    )
    ceiling = out.best_sharpe + 2.0 * math.sqrt(max(eff.sr_variance, 0.0))
    out.significance_reachable = bool(math.isfinite(need) and need <= ceiling)  # EX-023
    return out
