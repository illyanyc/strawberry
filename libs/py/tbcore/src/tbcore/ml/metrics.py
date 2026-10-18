"""ML metrics on NumPy arrays: IC, per-date rank IC, MSE, AUC, log loss, Brier, NDCG, calibration
error, PSI, KS. All return NaN when undefined (too few rows, constant inputs)."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

F64 = NDArray[np.float64]


def _f(x: object) -> F64:
    return np.asarray(x, dtype=np.float64)


def ic(pred: object, y: object) -> float:
    p, t = _f(pred), _f(y)
    ok = np.isfinite(p) & np.isfinite(t)
    if ok.sum() < 3 or np.std(p[ok]) == 0 or np.std(t[ok]) == 0:
        return math.nan
    return float(np.corrcoef(p[ok], t[ok])[0, 1])


def _rank(x: F64) -> F64:
    order = np.argsort(x, kind="stable")
    r = np.empty(x.shape[0])
    r[order] = np.arange(x.shape[0], dtype=np.float64)
    # Average ties.
    _, inv, counts = np.unique(x, return_inverse=True, return_counts=True)
    if (counts > 1).any():
        sums = np.zeros(counts.shape[0])
        np.add.at(sums, inv, r)
        r = (sums / counts)[inv]
    return r


def rank_ic(pred: object, y: object, groups: object | None = None) -> float:
    """Spearman correlation; with ``groups`` the mean of per-group (per-date) Spearman correlations."""
    p, t = _f(pred), _f(y)
    if groups is None:
        ok = np.isfinite(p) & np.isfinite(t)
        return ic(_rank(p[ok]), _rank(t[ok])) if ok.sum() >= 3 else math.nan
    g = np.asarray(groups)
    vals = []
    for k in np.unique(g):
        m = (g == k) & np.isfinite(p) & np.isfinite(t)
        if m.sum() >= 3:
            v = ic(_rank(p[m]), _rank(t[m]))
            if math.isfinite(v):
                vals.append(v)
    return float(np.mean(vals)) if vals else math.nan


def mse(pred: object, y: object) -> float:
    p, t = _f(pred), _f(y)
    ok = np.isfinite(p) & np.isfinite(t)
    return float(np.mean((p[ok] - t[ok]) ** 2)) if ok.any() else math.nan


def auc(prob: object, y: object) -> float:
    p, t = _f(prob), _f(y)
    ok = np.isfinite(p) & np.isfinite(t)
    p, pos = p[ok], t[ok] > 0.5
    n1, n0 = int(pos.sum()), int((~pos).sum())
    if n1 == 0 or n0 == 0:
        return math.nan
    r = _rank(p) + 1.0
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def logloss(prob: object, y: object) -> float:
    p, t = np.clip(_f(prob), 1e-12, 1 - 1e-12), _f(y)
    ok = np.isfinite(p) & np.isfinite(t)
    if not ok.any():
        return math.nan
    return float(-np.mean(t[ok] * np.log(p[ok]) + (1 - t[ok]) * np.log(1 - p[ok])))


def brier(prob: object, y: object) -> float:
    p, t = _f(prob), _f(y)
    ok = np.isfinite(p) & np.isfinite(t)
    return float(np.mean((p[ok] - t[ok]) ** 2)) if ok.any() else math.nan


def calibration_error(prob: object, y: object, bins: int = 10) -> float:
    """Expected calibration error over equal-width probability bins."""
    p, t = _f(prob), _f(y)
    ok = np.isfinite(p) & np.isfinite(t)
    p, t = p[ok], t[ok]
    if p.size == 0:
        return math.nan
    idx = np.minimum((p * bins).astype(int), bins - 1)
    err = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            err += m.mean() * abs(float(p[m].mean()) - float(t[m].mean()))
    return err


def ndcg(pred: object, relevance: object, groups: object, k: int = 5) -> float:
    p, r, g = _f(pred), _f(relevance), np.asarray(groups)
    vals = []
    for q in np.unique(g):
        m = g == q
        if m.sum() < 2:
            continue
        rel = r[m]
        order = np.argsort(-p[m], kind="stable")[:k]
        ideal = np.sort(rel)[::-1][:k]
        disc = 1.0 / np.log2(np.arange(2, order.size + 2))
        dcg = float(np.sum((2 ** rel[order] - 1) * disc))
        idcg = float(np.sum((2 ** ideal[: order.size] - 1) * disc))
        if idcg > 0:
            vals.append(dcg / idcg)
    return float(np.mean(vals)) if vals else math.nan


def psi(expected: object, actual: object, bins: int = 10) -> float:
    """Population stability index of ``actual`` vs ``expected`` on the expected sample's deciles."""
    e, a = _f(expected), _f(actual)
    e, a = e[np.isfinite(e)], a[np.isfinite(a)]
    if e.size < bins or a.size < bins:
        return math.nan
    cats = np.unique(e)
    if cats.size <= 2 * bins:  # discrete feature: compare category shares
        pe = np.array([np.mean(e == c) for c in cats])
        pa = np.array([np.mean(a == c) for c in cats])
        pe, pa = np.maximum(pe, 1e-6), np.maximum(pa, 1e-6)
        return float(np.sum((pa - pe) * np.log(pa / pe)))
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if edges.size < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges)[0] / e.size
    pa = np.histogram(a, edges)[0] / a.size
    pe, pa = np.maximum(pe, 1e-6), np.maximum(pa, 1e-6)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def ks(expected: object, actual: object) -> float:
    e, a = np.sort(_f(expected)), np.sort(_f(actual))
    e, a = e[np.isfinite(e)], a[np.isfinite(a)]
    if e.size == 0 or a.size == 0:
        return math.nan
    grid = np.concatenate([e, a])
    ce = np.searchsorted(e, grid, side="right") / e.size
    ca = np.searchsorted(a, grid, side="right") / a.size
    return float(np.max(np.abs(ce - ca)))


def adf_stat(x: object) -> float:
    """Augmented Dickey-Fuller t-statistic with a constant and one lagged difference."""
    s = _f(x)
    s = s[np.isfinite(s)]
    if s.size < 20:
        return math.nan
    dy = np.diff(s)
    y_lag = s[1:-1]
    dy_t = dy[1:]
    dy_lag = dy[:-1]
    xmat = np.column_stack([np.ones(y_lag.size), y_lag, dy_lag])
    beta, *_ = np.linalg.lstsq(xmat, dy_t, rcond=None)
    resid = dy_t - xmat @ beta
    dof = max(dy_t.size - xmat.shape[1], 1)
    s2 = float(resid @ resid) / dof
    try:
        cov = s2 * np.linalg.inv(xmat.T @ xmat)
    except np.linalg.LinAlgError:
        return math.nan
    se = math.sqrt(max(float(cov[1, 1]), 1e-300))
    return float(beta[1] / se)
