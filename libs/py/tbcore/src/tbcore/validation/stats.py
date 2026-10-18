"""Overfitting-aware statistics (section 08, ADR-0030, BT-040..BT-047, SM-008, D31 rules 2 and 7).

Sharpe ratios inside the deflation formulas are *per observation* (not annualized); skewness is
the third standardized moment and kurtosis the fourth (normal = 3), as in the source papers:

* PSR and DSR: Bailey and López de Prado, "The Deflated Sharpe Ratio" (JPM 2014).
* MinTRL: Bailey and López de Prado, "The Sharpe Ratio Efficient Frontier" (JoR 2012).
* MinBTL: Bailey, Borwein, López de Prado, Zhu, Notices of the AMS (2014).
* Effective N via ONC: López de Prado and Lewis, "Detection of false investment strategies using
  unsupervised learning methods" (QF 2019).
* PBO via CSCV: Bailey, Borwein, López de Prado, Zhu, "The probability of backtest overfitting" (JCF 2017).
* SPA: Hansen, "A test for superior predictive ability" (JBES 2005), stationary bootstrap of
  Politis and Romano (1994); Romano and Wolf stepdown (Econometrica 2005).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist

import numpy as np
from numpy.typing import ArrayLike, NDArray

F64 = NDArray[np.float64]
EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def _f(x: ArrayLike) -> F64:
    return np.asarray(x, dtype=np.float64)


def sharpe(returns: ArrayLike, periods_per_year: float | None = None) -> float:
    """Sharpe ratio of per-period returns (risk-free 0); annualized by sqrt(periods) when given."""
    r = _f(returns)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return math.nan
    sd = float(np.std(r, ddof=1))
    if sd == 0:
        return math.nan
    sr = float(np.mean(r)) / sd
    return sr * math.sqrt(periods_per_year) if periods_per_year else sr


def moments(returns: ArrayLike) -> tuple[float, float]:
    """(skewness, kurtosis) with kurtosis normal = 3."""
    r = _f(returns)
    r = r[np.isfinite(r)]
    m, s = float(np.mean(r)), float(np.std(r))
    if s == 0:
        return 0.0, 3.0
    z = (r - m) / s
    return float(np.mean(z**3)), float(np.mean(z**4))


def probabilistic_sharpe(sr: float, sr_benchmark: float, n_obs: int, skew: float = 0.0, kurtosis: float = 3.0) -> float:
    """PSR: probability that the true per-period Sharpe exceeds ``sr_benchmark`` (BT-040)."""
    denom = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr
    if n_obs < 2 or denom <= 0:
        return math.nan
    return _N.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom))


def expected_max_sharpe(n_trials: float, sr_variance: float) -> float:
    """Expected maximum per-period Sharpe of ``n_trials`` independent zero-skill trials (BT-041)."""
    if n_trials <= 1:
        return 0.0
    z1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(max(sr_variance, 0.0)) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def deflated_sharpe(sr: float, n_trials: float, sr_variance: float, skew: float, kurtosis: float, n_obs: int) -> float:
    """DSR: PSR against the expected maximum Sharpe of the trials (BT-041). Use the *effective*
    trial count and the cross-trial Sharpe variance from the full trial ledger (D31 rule 2)."""
    return probabilistic_sharpe(sr, expected_max_sharpe(n_trials, sr_variance), n_obs, skew, kurtosis)


def min_track_record_length(
    sr: float, target_sr: float, skew: float = 0.0, kurtosis: float = 3.0, confidence: float = 0.95
) -> float:
    """MinTRL: observations needed for PSR(target) >= confidence at the observed per-period SR (BT-042)."""
    if sr <= target_sr:
        return math.inf
    z = _N.inv_cdf(confidence)
    return 1.0 + (1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr) * (z / (sr - target_sr)) ** 2


def min_backtest_length(n_trials: float, target_sr_annual: float = 1.0) -> float:
    """MinBTL in years: the history below which ``n_trials`` independent noise trials are expected to
    produce an annualized in-sample Sharpe of ``target_sr_annual`` (BT-043, D31 rule 3)."""
    if n_trials <= 1:
        return 0.0
    e = expected_max_sharpe(n_trials, 1.0)
    return (e / target_sr_annual) ** 2


# ---- effective number of trials (ONC) ----


def _kmeans(x: F64, k: int, rng: np.random.Generator, n_iter: int = 100) -> NDArray[np.int64]:
    n = x.shape[0]
    centers = [x[rng.integers(n)]]
    for _ in range(1, k):
        d2 = np.min(((x[:, None, :] - np.asarray(centers)[None, :, :]) ** 2).sum(-1), axis=1)
        p = d2 / d2.sum() if d2.sum() > 0 else np.full(n, 1.0 / n)
        centers.append(x[rng.choice(n, p=p)])
    c = np.asarray(centers)
    labels = np.zeros(n, dtype=np.int64)
    for _ in range(n_iter):
        new = np.argmin(((x[:, None, :] - c[None, :, :]) ** 2).sum(-1), axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            if np.any(labels == j):
                c[j] = x[labels == j].mean(axis=0)
    return labels


def _silhouette(x: F64, labels: NDArray[np.int64]) -> F64:
    d = np.sqrt(((x[:, None, :] - x[None, :, :]) ** 2).sum(-1))
    n = x.shape[0]
    out = np.zeros(n)
    ks = np.unique(labels)
    if ks.size < 2:
        return out
    for i in range(n):
        same = labels == labels[i]
        a = d[i, same].sum() / max(same.sum() - 1, 1)
        b = min(d[i, labels == k].mean() for k in ks if k != labels[i])
        out[i] = 0.0 if same.sum() == 1 else (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return out


def _onc_base(corr: F64, max_k: int, n_init: int, rng: np.random.Generator) -> NDArray[np.int64]:
    x = np.sqrt(np.clip((1.0 - corr) / 2.0, 0.0, None))
    best, best_q = np.zeros(corr.shape[0], dtype=np.int64), -math.inf
    for _ in range(n_init):
        for k in range(2, min(max_k, corr.shape[0] - 1) + 1):
            lab = _kmeans(x, k, rng)
            sil = _silhouette(x, lab)
            q = float(np.mean(sil) / np.std(sil)) if np.std(sil) > 0 else -math.inf
            if q > best_q:
                best, best_q = lab, q
    return best


def onc_clusters(corr: ArrayLike, max_k: int | None = None, n_init: int = 5, seed: int = 0) -> NDArray[np.int64]:
    """Optimal Number of Clusters on a correlation matrix (base ONC with one refinement pass of
    clusters whose silhouette quality is below average). Returns a cluster label per trial."""
    c = _f(corr)
    n = c.shape[0]
    if n < 3:
        return np.arange(n, dtype=np.int64)
    rng = np.random.default_rng(seed)
    labels = _onc_base(c, max_k or n - 1, n_init, rng)
    x = np.sqrt(np.clip((1.0 - c) / 2.0, 0.0, None))
    sil = _silhouette(x, labels)
    quality = {
        k: (float(np.mean(sil[labels == k]) / np.std(sil[labels == k])) if np.std(sil[labels == k]) > 0 else math.inf)
        for k in np.unique(labels)
    }
    finite = [q for q in quality.values() if math.isfinite(q)]
    mean_q = float(np.mean(finite)) if finite else math.inf
    out = labels.copy()
    nxt = int(labels.max()) + 1
    for k, q in quality.items():
        members = np.flatnonzero(labels == k)
        if q < mean_q and members.size >= 3:
            sub = _onc_base(c[np.ix_(members, members)], members.size - 1, n_init, rng)
            for s in np.unique(sub)[1:]:
                out[members[sub == s]] = nxt
                nxt += 1
    # Keep the refinement only if it improves the overall silhouette t-statistic (ONC "top" step).
    if not np.array_equal(out, labels) and _quality(x, out) <= _quality(x, labels):
        out = labels
    _, out = np.unique(out, return_inverse=True)
    return out.astype(np.int64)


def _quality(x: F64, labels: NDArray[np.int64]) -> float:
    sil = _silhouette(x, labels)
    return float(np.mean(sil) / np.std(sil)) if np.std(sil) > 0 else -math.inf


@dataclass(frozen=True)
class EffectiveTrials:
    n_effective: int
    labels: NDArray[np.int64]
    sr_variance: float  # variance of per-period Sharpe across cluster representatives
    cluster_sharpes: F64


def effective_trials(trial_returns: ArrayLike, seed: int = 0) -> EffectiveTrials:
    """Effective N and cross-trial Sharpe variance from a ``(T, N)`` matrix of trial returns (BT-044).

    Trials are clustered by ONC on their return correlations; each cluster becomes one
    inverse-variance-weighted return series whose Sharpe enters the variance.
    """
    m = _f(trial_returns)
    if m.ndim != 2 or m.shape[1] == 0:
        raise ValueError("trial_returns must be (T, N)")
    if m.shape[1] < 3:
        srs = np.array([sharpe(m[:, j]) for j in range(m.shape[1])])
        return EffectiveTrials(
            m.shape[1], np.arange(m.shape[1], dtype=np.int64), float(np.var(srs, ddof=1)) if srs.size > 1 else 0.0, srs
        )
    corr: F64 = np.nan_to_num(np.asarray(np.corrcoef(m, rowvar=False), dtype=np.float64), nan=0.0)
    np.fill_diagonal(corr, 1.0)
    labels = onc_clusters(corr, seed=seed)
    cluster_srs: list[float] = []
    for k in np.unique(labels):
        sub = m[:, labels == k]
        var = np.var(sub, axis=0, ddof=1)
        w = np.where(var > 0, 1.0 / np.where(var > 0, var, 1.0), 0.0)
        w = w / w.sum() if w.sum() > 0 else np.full(sub.shape[1], 1.0 / sub.shape[1])
        cluster_srs.append(sharpe(sub @ w))
    arr = np.asarray(cluster_srs)
    v = (
        float(np.nanvar(arr, ddof=1))
        if arr.size > 1
        else float(np.nanvar([sharpe(m[:, j]) for j in range(m.shape[1])], ddof=1))
    )
    return EffectiveTrials(int(arr.size), labels, v, arr)


# ---- probability of backtest overfitting (CSCV) ----


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    logits: F64
    n_combinations: int
    # Fraction of combinations where the IS-best trial lost money out of sample.
    prob_oos_loss: float


def pbo_cscv(
    trial_returns: ArrayLike, n_partitions: int = 16, metric: Callable[[F64], float] | None = None
) -> PBOResult:
    """PBO by combinatorially symmetric cross-validation (BT-045; default S = 16, D31 rule 2).

    Rows (time) are cut into ``n_partitions`` blocks; for each half/half combination the best trial
    in sample is ranked out of sample. PBO is the share of combinations where its OOS rank is at or
    below the median (logit <= 0). The default metric is the per-period Sharpe ratio.
    """
    m = _f(trial_returns)
    t, n = m.shape
    if n_partitions % 2 or n_partitions < 2:
        raise ValueError("n_partitions must be even")
    if t < n_partitions or n < 2:
        raise ValueError("need at least n_partitions rows and two trials")
    blocks = np.array_split(np.arange(t), n_partitions)
    logits, oos_loss = [], 0
    if metric is None:
        s = np.stack([m[b].sum(0) for b in blocks])
        s2 = np.stack([(m[b] ** 2).sum(0) for b in blocks])
        cnt = np.array([len(b) for b in blocks], dtype=np.float64)

        def stat(idx: tuple[int, ...]) -> F64:
            ii = list(idx)
            c = cnt[ii].sum()
            mu = s[ii].sum(0) / c
            var = (s2[ii].sum(0) - c * mu * mu) / (c - 1)
            sd = np.sqrt(np.where(var > 0, var, np.nan))
            return np.nan_to_num(mu / sd, nan=-np.inf)

        def total(idx: tuple[int, ...]) -> F64:
            return s[list(idx)].sum(0)
    else:

        def stat(idx: tuple[int, ...]) -> F64:
            rows = np.concatenate([blocks[i] for i in idx])
            return np.array([metric(m[rows, j]) for j in range(n)])

        def total(idx: tuple[int, ...]) -> F64:
            rows = np.concatenate([blocks[i] for i in idx])
            return np.asarray(m[rows].sum(0), dtype=np.float64)

    allp = set(range(n_partitions))
    combos = list(combinations(range(n_partitions), n_partitions // 2))
    for is_idx in combos:
        oos_idx = tuple(sorted(allp - set(is_idx)))
        best = int(np.argmax(stat(is_idx)))
        oos = stat(oos_idx)
        rank = float(np.sum(oos < oos[best]) + 0.5 * (np.sum(oos == oos[best]) - 1) + 1)
        w = rank / (n + 1)
        logits.append(math.log(w / (1 - w)))
        oos_loss += int(total(oos_idx)[best] < 0)
    lg = np.asarray(logits)
    return PBOResult(float(np.mean(lg <= 0)), lg, len(combos), oos_loss / len(combos))


# ---- bootstrap tests ----


def stationary_bootstrap_indices(n: int, mean_block: float, n_boot: int, rng: np.random.Generator) -> NDArray[np.int64]:
    """Politis-Romano stationary bootstrap index matrix ``(n_boot, n)``."""
    p = 1.0 / max(mean_block, 1.0)
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, n_boot)
    restart = rng.random((n_boot, n)) < p
    fresh = rng.integers(0, n, (n_boot, n))
    for t in range(1, n):
        idx[:, t] = np.where(restart[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return idx


@dataclass(frozen=True)
class SPAResult:
    statistic: float
    p_consistent: float
    p_lower: float
    p_upper: float  # White's reality check


def spa_test(
    benchmark: ArrayLike, models: ArrayLike, n_boot: int = 2000, mean_block: float = 10.0, seed: int = 0
) -> SPAResult:
    """Hansen's SPA test of H0 "no model beats the benchmark" on per-period returns (BT-046).

    ``models`` is ``(T, K)``. Small p-values reject H0 (a model has superior expected return).
    """
    b = _f(benchmark)
    m = _f(models)
    if m.ndim == 1:
        m = m[:, None]
    d = m - b[:, None]
    t = d.shape[0]
    dbar = d.mean(0)
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(t, mean_block, n_boot, rng)
    boot = np.stack([d[i].mean(0) for i in idx])  # (B, K)
    omega = np.sqrt(t * np.var(boot - dbar, axis=0))
    omega = np.where(omega > 0, omega, np.inf)
    stat = max(float(np.max(math.sqrt(t) * dbar / omega)), 0.0)
    thresh = -np.sqrt(omega**2 / t * 2.0 * math.log(math.log(max(t, 3))))
    centers = {
        "lower": np.maximum(dbar, 0.0),
        "consistent": np.where(dbar >= thresh, dbar, 0.0),
        "upper": dbar,
    }
    out = {}
    for name, g in centers.items():
        z = math.sqrt(t) * (boot - dbar + (dbar - g)) / omega
        tb = np.maximum(np.max(z, axis=1), 0.0)
        out[name] = float(np.mean(tb >= stat))
    return SPAResult(stat, out["consistent"], out["lower"], out["upper"])


def romano_wolf(
    benchmark: ArrayLike, models: ArrayLike, n_boot: int = 2000, mean_block: float = 10.0, seed: int = 0
) -> F64:
    """Romano-Wolf stepdown adjusted p-values for "model k beats the benchmark", one per model (BT-047)."""
    b = _f(benchmark)
    m = _f(models)
    if m.ndim == 1:
        m = m[:, None]
    d = m - b[:, None]
    t, k = d.shape
    dbar = d.mean(0)
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(t, mean_block, n_boot, rng)
    boot = np.stack([d[i].mean(0) for i in idx])
    se = np.std(boot, axis=0)
    se = np.where(se > 0, se, np.inf)
    tstat = dbar / se
    tboot = (boot - dbar) / se
    order = np.argsort(-tstat)
    adj = np.zeros(k)
    prev = 0.0
    for step, j in enumerate(order):
        active = order[step:]
        mx = np.max(tboot[:, active], axis=1)
        p = float(np.mean(mx >= tstat[j]))
        prev = max(prev, p)
        adj[j] = prev
    return adj


def bootstrap_ci(
    returns: ArrayLike,
    stat: Callable[[F64], float] = sharpe,
    level: float = 0.95,
    n_boot: int = 2000,
    mean_block: float = 5.0,
    seed: int = 0,
) -> tuple[float, float]:
    """Stationary-bootstrap percentile confidence interval of ``stat`` (BT-047)."""
    r = _f(returns)
    r = r[np.isfinite(r)]
    if r.size < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(seed)
    idx = stationary_bootstrap_indices(r.size, mean_block, n_boot, rng)
    vals = np.array([stat(r[i]) for i in idx])
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:  # e.g. zero-variance returns: every resampled Sharpe is undefined
        return math.nan, math.nan
    a = (1 - level) / 2
    return float(np.quantile(vals, a)), float(np.quantile(vals, 1 - a))
