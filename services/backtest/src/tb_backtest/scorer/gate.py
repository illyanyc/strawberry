"""Promotion gate for a challenger (section 08/11, BT-080, D31 rule 7, ADR-0034).

A candidate passes only if all hold:

* DSR >= ``dsr_min`` (0.95), deflated by the *effective* number of trials (ONC clusters of the
  lineage's full trial ledger) and the cross-trial Sharpe variance (BT-041, BT-044);
* PBO <= ``pbo_max`` (0.2) by CSCV on the study's trial returns (BT-045);
* SPA p-value <= ``spa_p_max`` (0.05) against the champion when there is one (BT-046);
* net P&L > 0 at 2x costs (BT-015);
* at least ``min_trades`` trades and max drawdown within ``max_drawdown``.

The analyst only ever receives ``AnalystView``: pass/fail and the DSR (D31 rules 5 and 9). The policy
values live here, not in analyst-visible configuration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from tbcore.validation.stats import deflated_sharpe, effective_trials, moments, pbo_cscv, sharpe, spa_test

F64 = NDArray[np.float64]


@dataclass(frozen=True)
class GatePolicy:
    dsr_min: float = 0.95
    pbo_max: float = 0.2
    spa_p_max: float = 0.05
    min_trades: int = 30
    max_drawdown: float = 0.25
    pbo_partitions: int = 16


@dataclass(frozen=True)
class CandidateEvidence:
    """What the trial ledger and the event-driven reruns provide for one candidate."""

    candidate_id: str
    oos_returns: F64  # session returns of the candidate, out of sample (CPCV paths or walk-forward)
    trial_returns: F64  # (T, N) out-of-sample returns of every trial in the lineage, candidate included
    trades: int
    net_pnl_2x: float
    max_drawdown: float
    champion_returns: F64 | None = None  # aligned with oos_returns


@dataclass(frozen=True)
class Verdict:
    candidate_id: str
    passed: bool
    dsr: float
    reasons: tuple[str, ...]
    details: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalystView:
    """The only scorer output the AI analyst may see (BT-080)."""

    candidate_id: str
    passed: bool
    dsr: float


def score(ev: CandidateEvidence, policy: GatePolicy = GatePolicy()) -> Verdict:  # noqa: B008 - immutable default
    r = np.asarray(ev.oos_returns, dtype=np.float64)
    trials = np.asarray(ev.trial_returns, dtype=np.float64)
    eff = effective_trials(trials)
    sr = sharpe(r)
    skew, kurt = moments(r)
    dsr = deflated_sharpe(sr, eff.n_effective, eff.sr_variance, skew, kurt, r.size) if math.isfinite(sr) else math.nan
    pbo = (
        pbo_cscv(trials, policy.pbo_partitions).pbo
        if trials.shape[1] >= 2 and trials.shape[0] >= policy.pbo_partitions
        else math.nan
    )
    spa_p = math.nan
    if ev.champion_returns is not None:
        spa_p = spa_test(np.asarray(ev.champion_returns, dtype=np.float64), r[:, None]).p_consistent
    reasons = []
    if not (dsr >= policy.dsr_min):
        reasons.append("dsr")
    if not (pbo <= policy.pbo_max):
        reasons.append("pbo")
    if ev.champion_returns is not None and not (spa_p <= policy.spa_p_max):
        reasons.append("spa")
    if not ev.net_pnl_2x > 0:
        reasons.append("cost_stress")
    if ev.trades < policy.min_trades:
        reasons.append("trades")
    if ev.max_drawdown > policy.max_drawdown:
        reasons.append("drawdown")
    details = {
        "sharpe": sr,
        "n_effective": float(eff.n_effective),
        "sr_variance": eff.sr_variance,
        "pbo": pbo,
        "spa_p": spa_p,
    }
    return Verdict(ev.candidate_id, not reasons, dsr, tuple(reasons), details)


def analyst_view(v: Verdict) -> AnalystView:
    return AnalystView(v.candidate_id, v.passed, round(v.dsr, 4) if math.isfinite(v.dsr) else v.dsr)
