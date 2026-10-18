"""Live feedback to the AI Research Analyst (section 13 analyst feedback slice, ADR-0044).

For each strategy in shadow, paper, or live this builds a ``LiveFeedbackReport``: live metrics
against the backtest's expected distribution, parity, a CUSUM decay test on per-trade returns,
feature and prediction drift (PSI, KS) against the training reference, calibration on resolved
predictions, error slices, regime coverage, and execution quality. Retraining triggers fire only with
enough samples (MN-024) and are published as ``RetrainSuggested`` (MN-023); a critical trigger also
asks the analyst to drop the lineage to level 0 and pause (MN-025). Reports carry no balances,
positions, quantities, or account identifiers (MN-021): returns are in basis points per trade.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

from tb.common.v1 import common_pb2
from tb.monitor.v1 import feedback_pb2
from tb.orders.v1 import orders_pb2


@dataclass(frozen=True)
class FeedbackConfig:
    """Thresholds (config, MN-023/MN-024). Keys under ``TB_MONITOR_FEEDBACK_*``."""

    min_trades: int = 30
    min_resolved: int = 100
    cusum_k: float = 0.5
    cusum_h: float = 5.0
    psi_threshold: float = 0.25
    psi_top_k: int = 5
    ece_threshold: float = 0.1
    below_p10_sessions: int = 5
    match_rate_floor: float = 0.9
    slippage_gap_bps: float = 5.0
    unseen_regime_share: float = 0.05
    top_drift_features: int = 10


@dataclass
class Expectation:
    """What the backtest promised (walk-forward/CPCV paths) for one strategy and model version."""

    trade_mean_bps: float
    trade_sd_bps: float
    # Per-metric distributions across backtest paths, e.g. {"hit_rate": [...], "mean_trade_bps": [...]}.
    distributions: dict[str, list[float]] = field(default_factory=dict)
    backtest_report_id: str = ""
    training_run_id: str = ""
    slippage_bps_modeled: float = 0.0
    shortfall_bps_modeled: float = 0.0
    # Training-period regime shares, e.g. {"low_vol": 0.6, "high_vol": 0.4}.
    regime_shares: dict[str, float] = field(default_factory=dict)
    reference_ece: float = 0.0


@dataclass
class LiveWindow:
    strategy_id: str
    lineage_id: str
    model_version: str
    mode: str
    window_start: int
    window_end: int
    trade_returns_bps: list[float] = field(default_factory=list)
    session_returns_bps: list[float] = field(default_factory=list)
    sessions: int = 0
    # Feature samples: live vs training reference, and the training importance rank per feature.
    live_features: dict[str, Sequence[float]] = field(default_factory=dict)
    reference_features: dict[str, Sequence[float]] = field(default_factory=dict)
    importance_rank: dict[str, int] = field(default_factory=dict)
    live_predictions: Sequence[float] = ()
    reference_predictions: Sequence[float] = ()
    resolved_probs: Sequence[float] = ()
    resolved_outcomes: Sequence[int] = ()
    parity: feedback_pb2.ParitySummary | None = None
    current_regime: str = ""
    slippage_bps_live: float = 0.0
    shortfall_bps: float = 0.0
    limit_fill_rate: float = 0.0
    error_slices: list[feedback_pb2.ErrorSlice] = field(default_factory=list)


# ---- statistics (pure, numpy only)


def psi(reference: Sequence[float], live: Sequence[float], bins: int = 10) -> float:
    """Population stability index with reference-quantile bins (epsilon-smoothed)."""
    ref, cur = np.asarray(reference, dtype=float), np.asarray(live, dtype=float)
    ref, cur = ref[np.isfinite(ref)], cur[np.isfinite(cur)]
    if ref.size < bins or cur.size == 0:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if edges.size < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    p = np.histogram(ref, edges)[0] / ref.size
    q = np.histogram(cur, edges)[0] / cur.size
    eps = 1e-6
    p, q = np.clip(p, eps, None), np.clip(q, eps, None)
    return float(np.sum((q - p) * np.log(q / p)))


def ks(reference: Sequence[float], live: Sequence[float]) -> float:
    """Two-sample Kolmogorov-Smirnov statistic."""
    a, b = np.sort(np.asarray(reference, dtype=float)), np.sort(np.asarray(live, dtype=float))
    if a.size == 0 or b.size == 0:
        return 0.0
    grid = np.concatenate([a, b])
    fa = np.searchsorted(a, grid, side="right") / a.size
    fb = np.searchsorted(b, grid, side="right") / b.size
    return float(np.max(np.abs(fa - fb)))


def cusum_lower(returns: Sequence[float], mean: float, sd: float, k: float, h: float) -> tuple[float, bool]:
    """One-sided lower CUSUM on standardized returns: detects a drop of the mean below expectation
    (statistic = max over time of S_t = max(0, S_{t-1} - z_t - k); breach when S_t > h)."""
    if sd <= 0 or not returns:
        return 0.0, False
    s, peak = 0.0, 0.0
    for r in returns:
        z = (r - mean) / sd
        s = max(0.0, s - z - k)
        peak = max(peak, s)
    return peak, peak > h


def calibration(probs: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> tuple[float, float]:
    """(Brier score, expected calibration error over equal-width bins)."""
    p, y = np.asarray(probs, dtype=float), np.asarray(outcomes, dtype=float)
    if p.size == 0:
        return 0.0, 0.0
    brier = float(np.mean((p - y) ** 2))
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return brier, float(ece)


def percentile_of(value: float, dist: Sequence[float]) -> float:
    d = np.asarray(dist, dtype=float)
    if d.size == 0:
        return 50.0
    return float(100.0 * (np.sum(d < value) + 0.5 * np.sum(d == value)) / d.size)


def _pct(dist: Sequence[float], q: float) -> float:
    d = np.asarray(dist, dtype=float)
    return float(np.percentile(d, q)) if d.size else 0.0


# ---- report


def report_id(w: LiveWindow) -> str:
    raw = f"{w.strategy_id}|{w.model_version}|{w.mode}|{w.window_start}|{w.window_end}|{len(w.trade_returns_bps)}"
    return "fb-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def build_report(
    w: LiveWindow, exp: Expectation, cfg: FeedbackConfig, now: datetime | None = None
) -> feedback_pb2.LiveFeedbackReport:
    now = now or datetime.now(tz=UTC)
    r = feedback_pb2.LiveFeedbackReport(
        report_id=report_id(w),
        strategy_id=w.strategy_id,
        lineage_id=w.lineage_id or w.strategy_id,
        model_version=w.model_version,
        mode=w.mode,
        window_start=w.window_start,
        window_end=w.window_end,
        sessions=w.sessions,
        trades=len(w.trade_returns_bps),
        resolved_predictions=len(w.resolved_outcomes),
        backtest_report_id=exp.backtest_report_id,
        training_run_id=exp.training_run_id,
        generated_at=int(now.timestamp() * 1e9),
    )
    trades = list(w.trade_returns_bps)
    live_metrics: dict[str, float] = {}
    if trades:
        live_metrics["mean_trade_bps"] = float(np.mean(trades))
        live_metrics["hit_rate"] = float(np.mean(np.asarray(trades) > 0))
    if len(w.session_returns_bps) > 1:
        s = np.asarray(w.session_returns_bps, dtype=float)
        live_metrics["session_sharpe"] = float(s.mean() / s.std()) if s.std() > 0 else 0.0
    for name, val in live_metrics.items():
        dist = exp.distributions.get(name, [])
        r.metrics.add(
            name=name,
            live_value=val,
            expected_p10=_pct(dist, 10),
            expected_p50=_pct(dist, 50),
            expected_p90=_pct(dist, 90),
            percentile=percentile_of(val, dist),
        )
    stat, breached = cusum_lower(trades, exp.trade_mean_bps, exp.trade_sd_bps, cfg.cusum_k, cfg.cusum_h)
    r.decay.method, r.decay.statistic, r.decay.threshold, r.decay.breached, r.decay.samples = (
        "cusum_lower",
        stat,
        cfg.cusum_h,
        breached,
        len(trades),
    )
    drifts = []
    for feat, live in w.live_features.items():
        ref = w.reference_features.get(feat)
        if ref is None:
            continue
        drifts.append(
            feedback_pb2.FeatureDrift(
                feature=feat, psi=psi(ref, live), ks=ks(ref, live), importance_rank=w.importance_rank.get(feat, 0)
            )
        )
    drifts.sort(key=lambda d: -d.psi)
    r.feature_drift.extend(drifts[: cfg.top_drift_features])
    if len(w.live_predictions) and len(w.reference_predictions):
        r.prediction_drift.CopyFrom(
            feedback_pb2.FeatureDrift(
                feature="prediction",
                psi=psi(w.reference_predictions, w.live_predictions),
                ks=ks(w.reference_predictions, w.live_predictions),
            )
        )
    if len(w.resolved_outcomes):
        brier, ece = calibration(w.resolved_probs, w.resolved_outcomes)
        r.calibration.CopyFrom(
            feedback_pb2.Calibration(
                brier=brier, ece=ece, resolved=len(w.resolved_outcomes), reference_ece=exp.reference_ece
            )
        )
    if w.parity is not None:
        r.parity.CopyFrom(w.parity)
    r.parity.slippage_bps_live = w.slippage_bps_live or r.parity.slippage_bps_live
    r.parity.slippage_bps_modeled = exp.slippage_bps_modeled or r.parity.slippage_bps_modeled
    r.error_slices.extend(w.error_slices[:50])
    if w.current_regime:
        share = exp.regime_shares.get(w.current_regime, 0.0)
        r.regime.CopyFrom(
            feedback_pb2.RegimeContext(
                current=w.current_regime, seen_in_training=share >= cfg.unseen_regime_share, training_share=share
            )
        )
    r.execution.CopyFrom(
        feedback_pb2.ExecutionQuality(
            shortfall_bps=w.shortfall_bps,
            modeled_shortfall_bps=exp.shortfall_bps_modeled,
            limit_fill_rate=w.limit_fill_rate,
        )
    )

    # MN-024: never trigger on too few samples. Prediction-based conditions need resolved predictions;
    # return-based conditions need trades.
    enough_trades = len(trades) >= cfg.min_trades
    enough_preds = len(w.resolved_outcomes) >= cfg.min_resolved or not len(w.resolved_outcomes)
    r.sample_sufficient = enough_trades and enough_preds
    if not enough_trades:
        return r
    cond = r.triggered
    if breached:
        cond.add(
            condition="decay",
            value=stat,
            threshold=cfg.cusum_h,
            severity="warning",
            detail="live per-trade returns below expectation",
        )
    for d in drifts:
        if 0 < d.importance_rank <= cfg.psi_top_k and d.psi > cfg.psi_threshold:
            cond.add(
                condition="feature_drift",
                value=d.psi,
                threshold=cfg.psi_threshold,
                severity="warning",
                detail=f"feature {d.feature} (rank {d.importance_rank})",
            )
    if len(w.resolved_outcomes) >= cfg.min_resolved and r.calibration.ece > cfg.ece_threshold:
        cond.add(
            condition="calibration",
            value=r.calibration.ece,
            threshold=cfg.ece_threshold,
            severity="warning",
            detail="expected calibration error",
        )
    tail = list(w.session_returns_bps)[-cfg.below_p10_sessions :]
    p10 = _pct(exp.distributions.get("session_bps", []), 10) if exp.distributions.get("session_bps") else None
    if p10 is not None and len(tail) >= cfg.below_p10_sessions and all(x < p10 for x in tail):
        cond.add(
            condition="below_p10",
            value=float(np.mean(tail)),
            threshold=p10,
            severity="warning",
            detail=f"last {cfg.below_p10_sessions} sessions below the backtest p10",
        )
    mt = next((m for m in r.metrics if m.name == "mean_trade_bps"), None)
    if mt is not None and exp.distributions.get("mean_trade_bps") and mt.percentile < 10:
        cond.add(
            condition="below_p10",
            value=mt.live_value,
            threshold=mt.expected_p10,
            severity="warning",
            detail="mean trade below the backtest p10",
        )
    if w.parity is not None and (
        w.parity.trade_match_rate < cfg.match_rate_floor
        or abs(r.parity.slippage_bps_live - r.parity.slippage_bps_modeled) > cfg.slippage_gap_bps
    ):
        cond.add(
            condition="divergence",
            value=w.parity.trade_match_rate,
            threshold=cfg.match_rate_floor,
            severity="warning",
            detail="live vs backtest parity",
        )
    if w.current_regime and not r.regime.seen_in_training:
        cond.add(
            condition="unseen_regime",
            value=r.regime.training_share,
            threshold=cfg.unseen_regime_share,
            severity="warning",
            detail=f"regime {w.current_regime}",
        )
    names = {c.condition for c in cond}
    if ("decay" in names and "below_p10" in names) or len(names) >= 3:
        for c in cond:
            c.severity = "critical"
    return r


def retrain_suggested(
    rep: feedback_pb2.LiveFeedbackReport, now: datetime | None = None
) -> feedback_pb2.RetrainSuggested | None:
    """MN-023: one event per report with triggered conditions (and enough samples)."""
    if not rep.sample_sufficient or not rep.triggered:
        return None
    now = now or datetime.now(tz=UTC)
    sev = "critical" if any(c.severity == "critical" for c in rep.triggered) else "warning"
    return feedback_pb2.RetrainSuggested(
        trigger_id="rt-" + rep.report_id.removeprefix("fb-"),
        strategy_id=rep.strategy_id,
        lineage_id=rep.lineage_id,
        model_version=rep.model_version,
        mode=rep.mode,
        conditions=rep.triggered,
        report_id=rep.report_id,
        severity=sev,
        occurred_at=int(now.timestamp() * 1e9),
        correlation_id=rep.report_id,
    )


def subjects_for(ev: feedback_pb2.RetrainSuggested) -> list[str]:
    """``monitor.retrain_suggested.<lineage>`` plus the matching decay/drift/divergence subjects."""
    lid = ev.lineage_id or ev.strategy_id
    out = [f"monitor.retrain_suggested.{lid}"]
    names = {c.condition for c in ev.conditions}
    if "decay" in names or "below_p10" in names:
        out.append(f"monitor.decay.{lid}")
    if names & {"feature_drift", "calibration", "unseen_regime"}:
        out.append(f"monitor.drift.{lid}")
    if "divergence" in names:
        out.append(f"monitor.divergence.{lid}")
    return out


# ---- live window from bus events (ORB-style strategies: round trips from executions)


def strategy_short_id(client_order_id: str) -> str:
    parts = client_order_id.split("-")
    return parts[1] if len(parts) >= 4 and parts[0] == "tb1" else ""


def round_trips(executions: Iterable[orders_pb2.ExecutionReport], short_id: str) -> list[tuple[int, float]]:
    """FIFO round trips per instrument for one strategy: [(exit_time_ns, return_bps)]. Uses prices
    only (never quantities in the output, MN-021)."""
    open_lots: dict[str, deque[tuple[float, float, int]]] = defaultdict(deque)  # instrument → (qty, px, side)
    out: list[tuple[int, float]] = []
    for x in sorted(executions, key=lambda e: (e.event_time, e.sequence)):
        if strategy_short_id(x.client_order_id) != short_id:
            continue
        qty, px = float(x.qty.value or 0), float(x.price.value or 0)
        side = 1 if x.side == common_pb2.SIDE_BUY else -1
        lots = open_lots[x.instrument_id]
        while qty > 1e-12 and lots and lots[0][2] != side:
            lq, lp, ls = lots[0]
            take = min(qty, lq)
            out.append((x.event_time, (px - lp) / lp * 10_000 * ls))
            qty -= take
            if take >= lq - 1e-12:
                lots.popleft()
            else:
                lots[0] = (lq - take, lp, ls)
        if qty > 1e-12:
            lots.append((qty, px, side))
    return out


def session_returns(trips: list[tuple[int, float]], session_of: dict[int, str] | None = None) -> list[float]:
    by_day: dict[str, list[float]] = defaultdict(list)
    for t, ret in trips:
        day = session_of.get(t) if session_of else datetime.fromtimestamp(t / 1e9, tz=UTC).date().isoformat()
        by_day[day or ""].append(ret)
    return [float(np.mean(v)) for _, v in sorted(by_day.items())]


def expectation_from(trips: list[float], *, paths: int = 200, seed: int = 11, report_id: str = "") -> Expectation:
    """Bootstrap distributions of the window metrics from backtest per-trade returns (used when the
    backtest report supplies trades rather than path metrics)."""
    arr = np.asarray(trips, dtype=float)
    rng = np.random.default_rng(seed)
    n = max(1, arr.size)
    means, hits = [], []
    for _ in range(paths):
        s = rng.choice(arr, size=n, replace=True) if arr.size else np.zeros(1)
        means.append(float(s.mean()))
        hits.append(float((s > 0).mean()))
    sd = float(arr.std()) if arr.size > 1 else 1.0
    return Expectation(
        trade_mean_bps=float(arr.mean()) if arr.size else 0.0,
        trade_sd_bps=sd or 1.0,
        distributions={"mean_trade_bps": means, "hit_rate": hits},
        backtest_report_id=report_id,
    )


def nan_safe(x: float) -> float:
    return x if math.isfinite(x) else 0.0
