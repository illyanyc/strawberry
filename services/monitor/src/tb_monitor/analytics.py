"""Full-monitoring statistics (section 13 Phase 5, ADR-0045). Pure functions, NumPy only.

* Expected distributions are horizon-matched: a stationary block bootstrap of the backtest's
  per-session returns over the number of sessions the strategy has traded live (MN-030).
* Drawdown is compared with the bootstrap p95/p99 of the max drawdown over that horizon (MN-032).
* Implementation shortfall is side-signed fill vs the intent's reference price plus fees (MN-033).
* Drift is PSI/KS per feature against the training reference, plus prediction PSI (MN-038).
* Health components score 0..100, piecewise linear in the value's distance to the thresholds (MN-031).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from tb.monitor.v1 import monitor_pb2 as mp
from tb_monitor.feedback import ks, psi

F64 = NDArray[np.float64]
PATHS, BLOCK, SEED = 2000, 5, 17


def block_bootstrap(returns: ArrayLike, horizon: int, paths: int = PATHS, block: int = BLOCK, seed: int = SEED) -> F64:
    """Stationary (Politis-Romano) bootstrap: ``paths`` resampled paths of ``horizon`` sessions; each step
    starts a new block with probability 1/block, otherwise continues the previous one (wrapping)."""
    r = np.asarray(returns, dtype=np.float64)
    r = r[np.isfinite(r)]
    h = max(1, int(horizon))
    if r.size == 0:
        return np.zeros((paths, h))
    rng = np.random.default_rng(seed)
    idx = np.empty((paths, h), dtype=np.int64)
    idx[:, 0] = rng.integers(0, r.size, paths)
    new = rng.random((paths, h)) < 1.0 / max(block, 1)
    jump = rng.integers(0, r.size, (paths, h))
    for t in range(1, h):
        idx[:, t] = np.where(new[:, t], jump[:, t], (idx[:, t - 1] + 1) % r.size)
    return r[idx]


def max_drawdown_pct(session_bps: F64) -> F64:
    """Max drawdown (% of the running equity peak) of compounded session returns; rows are paths."""
    x = np.atleast_2d(np.asarray(session_bps, dtype=np.float64))
    if x.shape[1] == 0:
        return np.zeros(x.shape[0])
    eq = np.cumprod(1.0 + x / 1e4, axis=1)
    eq = np.concatenate([np.ones((x.shape[0], 1)), eq], axis=1)
    peak = np.maximum.accumulate(eq, axis=1)
    return np.asarray(np.max(1.0 - eq / peak, axis=1) * 100.0)


def percentile_of(value: float, dist: F64) -> float:
    d = np.asarray(dist, dtype=np.float64)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return 50.0
    return float(100.0 * (np.sum(d < value) + 0.5 * np.sum(d == value)) / d.size)


def band(metric: str, live: float, dist: F64, horizon: int) -> mp.PerformanceBand:
    d = np.asarray(dist, dtype=np.float64)
    d = d[np.isfinite(d)]
    q = np.percentile(d, [5, 10, 50, 90, 95, 99]) if d.size else np.zeros(6)
    return mp.PerformanceBand(
        metric=metric,
        live=float(live),
        p05=float(q[0]),
        p10=float(q[1]),
        p50=float(q[2]),
        p90=float(q[3]),
        p95=float(q[4]),
        p99=float(q[5]),
        percentile=percentile_of(live, d),
        horizon_sessions=horizon,
    )


def _sharpe(x: F64) -> F64:
    sd = np.std(x, axis=-1, ddof=1) if x.shape[-1] > 1 else np.zeros(x.shape[:-1])
    m = np.mean(x, axis=-1)
    return np.asarray(np.where(sd > 0, m / np.where(sd > 0, sd, 1.0), 0.0))


def _payoff(t: F64) -> float:
    wins, losses = t[t > 0], t[t < 0]
    if wins.size == 0 or losses.size == 0:
        return math.nan
    return float(wins.mean() / abs(losses.mean()))


@dataclass(frozen=True)
class DrawdownBands:
    horizon: int
    p95: float
    p99: float


def drawdown_bands(backtest_session_bps: ArrayLike, horizon: int) -> DrawdownBands:
    dd = max_drawdown_pct(block_bootstrap(backtest_session_bps, horizon))
    return DrawdownBands(horizon, float(np.percentile(dd, 95)), float(np.percentile(dd, 99)))


def performance_bands(
    backtest_session_bps: ArrayLike,
    backtest_trade_bps: ArrayLike,
    live_session_bps: ArrayLike,
    live_trade_bps: ArrayLike,
    backtest_trades_per_session: float = 0.0,
) -> list[mp.PerformanceBand]:
    """Live metrics against horizon-matched bootstrap distributions of the backtest (MN-030)."""
    live = np.asarray(live_session_bps, dtype=np.float64)
    h = max(1, live.size)
    paths = block_bootstrap(backtest_session_bps, h)
    out = [
        band(
            "return_pct",
            float((np.prod(1 + live / 1e4) - 1) * 100) if live.size else 0.0,
            (np.prod(1 + paths / 1e4, axis=1) - 1) * 100,
            h,
        ),
        band("sharpe", float(_sharpe(live[None, :])[0]) if live.size > 1 else 0.0, _sharpe(paths), h),
        band("max_drawdown_pct", float(max_drawdown_pct(live)[0]) if live.size else 0.0, max_drawdown_pct(paths), h),
    ]
    lt = np.asarray(live_trade_bps, dtype=np.float64)
    bt = np.asarray(backtest_trade_bps, dtype=np.float64)
    bt = bt[np.isfinite(bt)]
    if bt.size and lt.size:
        rng = np.random.default_rng(SEED)
        draws = bt[rng.integers(0, bt.size, (PATHS, lt.size))]
        out.append(band("hit_rate", float(np.mean(lt > 0)), np.mean(draws > 0, axis=1), h))
        payoffs = np.array([_payoff(np.asarray(row)) for row in draws[:500]], dtype=np.float64)
        out.append(band("payoff", _payoff(lt), payoffs, h))
    if backtest_trades_per_session > 0:
        rng = np.random.default_rng(SEED + 1)
        turnover = np.asarray(rng.poisson(backtest_trades_per_session * h, PATHS), dtype=np.float64) / h
        out.append(band("turnover", lt.size / h, turnover.astype(np.float64), h))
    return out


# ---- execution quality (MN-033)


@dataclass(frozen=True)
class FillObs:
    time: int
    side: int  # +1 buy, -1 sell
    qty: float
    price: float
    fees: float
    decision_price: float
    decision_time: int
    submitted_at: int


def shortfall_bps(f: FillObs) -> float:
    """Side-signed fill vs the decision (reference) price, plus fees, in bps; positive = cost."""
    if f.decision_price <= 0 or f.qty <= 0 or f.price <= 0:
        return math.nan
    return f.side * (f.price - f.decision_price) / f.decision_price * 1e4 + f.fees / (f.qty * f.price) * 1e4


def latency(values_ms: Sequence[float]) -> mp.LatencyStats:
    v = np.asarray([x for x in values_ms if math.isfinite(x) and x >= 0], dtype=np.float64)
    if v.size == 0:
        return mp.LatencyStats()
    p = np.percentile(v, [50, 90, 99])
    return mp.LatencyStats(p50_ms=float(p[0]), p90_ms=float(p[1]), p99_ms=float(p[2]), count=int(v.size))


def execution_quality(
    strategy_id: str, fills: Sequence[FillObs], modeled_bps: float, limit_orders: int, limit_filled: int
) -> mp.ExecutionQualityReport:
    sf = np.array([shortfall_bps(f) for f in fills], dtype=np.float64)
    sf = sf[np.isfinite(sf)]
    notional = np.array([f.qty * f.price for f in fills if shortfall_bps(f) == shortfall_bps(f)], dtype=np.float64)
    mean = float(np.average(sf, weights=notional)) if sf.size and notional.sum() > 0 else 0.0
    rep = mp.ExecutionQualityReport(
        strategy_id=strategy_id,
        window_start=min((f.time for f in fills), default=0),
        window_end=max((f.time for f in fills), default=0),
        shortfall_bps=mean,
        shortfall_p90_bps=float(np.percentile(sf, 90)) if sf.size else 0.0,
        modeled_shortfall_bps=modeled_bps,
        shortfall_ratio=mean / modeled_bps if modeled_bps > 0 else 0.0,
        limit_fill_rate=limit_filled / limit_orders if limit_orders else 0.0,
        limit_orders=limit_orders,
        fills=int(sf.size),
    )
    ns = 1e6
    rep.decision_to_submit.CopyFrom(
        latency([(f.submitted_at - f.decision_time) / ns for f in fills if f.submitted_at and f.decision_time])
    )
    rep.submit_to_fill.CopyFrom(latency([(f.time - f.submitted_at) / ns for f in fills if f.submitted_at]))
    rep.decision_to_fill.CopyFrom(latency([(f.time - f.decision_time) / ns for f in fills if f.decision_time]))
    return rep


# ---- drift (MN-038)


def spearman(a: ArrayLike, b: ArrayLike) -> float:
    x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    m = np.isfinite(x) & np.isfinite(y)
    if m.sum() < 3:
        return 0.0
    rx, ry = np.argsort(np.argsort(x[m])), np.argsort(np.argsort(y[m]))
    c = np.corrcoef(rx, ry)[0, 1]
    return float(c) if math.isfinite(c) else 0.0


def drift_features(
    live: Mapping[str, Sequence[float]], reference: Mapping[str, Sequence[float]], importance: Mapping[str, int]
) -> list[tuple[str, float, float, int]]:
    """(feature, PSI, KS, importance rank) for every referenced feature with live values, by PSI desc."""
    out: list[tuple[str, float, float, int]] = []
    for name, ref in reference.items():
        cur = [float(v) for v in live.get(name, ()) if v == v]
        if not cur:
            continue
        out.append((name, psi(ref, cur), ks(ref, cur), importance.get(name, 0)))
    out.sort(key=lambda t: -t[1])
    return out


# ---- health (MN-031)


def component_score(value: float, warning: float, breach: float, higher_is_worse: bool = True) -> float:
    """Piecewise linear on the gap g = |breach - warning|: 100 at one gap better than the warning
    threshold, 60 at the warning threshold, 20 at the breach threshold, 0 one gap past the breach."""
    if not (math.isfinite(value) and math.isfinite(warning) and math.isfinite(breach)) or warning == breach:
        return 100.0
    if not higher_is_worse:
        value, warning, breach = -value, -warning, -breach
    g = abs(breach - warning)
    pts = [(warning - g, 100.0), (warning, 60.0), (breach, 20.0), (breach + g, 0.0)]
    if value <= pts[0][0]:
        return 100.0
    for (x0, y0), (x1, y1) in itertools.pairwise(pts):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return 0.0


def status_of(value: float, warning: float, breach: float, higher_is_worse: bool = True) -> int:
    if not math.isfinite(value):
        return mp.HEALTH_STATUS_INSUFFICIENT_DATA
    sign = 1.0 if higher_is_worse else -1.0
    if sign * value >= sign * breach:
        return mp.HEALTH_STATUS_BREACH
    if sign * value >= sign * warning:
        return mp.HEALTH_STATUS_WARNING
    return mp.HEALTH_STATUS_OK


SEVERITY = {
    mp.HEALTH_STATUS_UNSPECIFIED: 0,
    mp.HEALTH_STATUS_INSUFFICIENT_DATA: 1,
    mp.HEALTH_STATUS_OK: 2,
    mp.HEALTH_STATUS_WARNING: 3,
    mp.HEALTH_STATUS_BREACH: 4,
    mp.HEALTH_STATUS_SEVERE: 5,
}


def component(
    name: str,
    value: float,
    warning: float,
    breach: float,
    samples: int,
    min_samples: int,
    detail: str = "",
    evidence: str = "",
    higher_is_worse: bool = True,
) -> mp.HealthComponent:
    c = mp.HealthComponent(
        name=name,
        value=value if math.isfinite(value) else 0.0,
        warning_threshold=warning,
        breach_threshold=breach,
        samples=samples,
        detail=detail,
        evidence_id=evidence,
    )
    if samples < min_samples or not math.isfinite(value):
        c.status, c.score = mp.HEALTH_STATUS_INSUFFICIENT_DATA, 100.0
        if not c.detail:
            c.detail = f"{samples} samples < {min_samples}"
        return c
    c.status = status_of(value, warning, breach, higher_is_worse)  # type: ignore[assignment]
    c.score = component_score(value, warning, breach, higher_is_worse)
    return c


def overall(components: Sequence[mp.HealthComponent]) -> tuple[float, int]:
    """Score = minimum component score; status = worst acting component (insufficient data never acts)."""
    acting = [c for c in components if c.status != mp.HEALTH_STATUS_INSUFFICIENT_DATA]
    if not acting:
        return 100.0, mp.HEALTH_STATUS_INSUFFICIENT_DATA
    worst = max(acting, key=lambda c: SEVERITY[c.status])
    return float(min(c.score for c in acting)), int(worst.status)
