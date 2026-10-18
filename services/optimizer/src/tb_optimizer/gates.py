"""Gate reports (EX-024, EX-051; D31 rule 7) built from the nested study and scored by the backtest
scorer (BT-080, which this module may import: the optimizer is on the evaluator side of BT-081).

The candidate's evidence is the outer walk-forward estimate of the whole selection procedure, rerun
event-driven (BT-006) at 1x and 2x costs; DSR and PBO use the lineage trial ledger (EX-021); SPA and
Romano-Wolf compare against the paper champion's outer series on common sessions.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import numpy as np
import polars as pl
from tb_backtest.scorer import CandidateEvidence, GatePolicy, score

from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import optimizer_pb2 as op
from tb_optimizer.nested import NestedResult, OuterResult
from tb_optimizer.stats import EffectiveN, Matrix, Series
from tbcore.backtest.model_eval import evaluate_event, strategy_spec
from tbcore.backtest.result import BacktestResult
from tbcore.ml import config as mlconfig
from tbcore.ml import dataset as mldataset
from tbcore.ml import models
from tbcore.ml.diagnostics import build_report
from tbcore.ml.train import TrainingResult, train
from tbcore.validation.stats import bootstrap_ci, effective_trials, romano_wolf, sharpe, spa_test

POLICY = GatePolicy()


@dataclass
class EventEvidence:
    series: Series
    series_2x: Series
    trades: int
    net_pnl_2x: float
    max_drawdown: float
    warnings: list[str]
    result: BacktestResult | None = None


def strategy_params(cfg: tp.TrainingConfig, benchmark: str) -> dict[str, Any]:
    p: dict[str, Any] = {"horizon": int(cfg.label.horizon), "benchmark": benchmark}
    if models.info(cfg.family).task == "rule_filter":
        prim = mlconfig.to_dict(cfg.primary.params) if cfg.primary.HasField("params") else {}
        if cfg.primary.rule == "residual_reversal":
            prim = {"benchmark": benchmark, "holding": int(cfg.label.horizon), **prim}
        p |= {"primary_class": cfg.primary.rule, "primary_params": prim}
    return p


def event_evidence(nested: NestedResult, ds: mldataset.Dataset, cfg: tp.TrainingConfig) -> EventEvidence | None:
    """Rerun the outer folds' decisions event-driven (live strategy code + tb-simcore)."""
    frames = [o.decisions for o in nested.outer if o.decisions.height]
    if not frames:
        return None
    dec = pl.concat(frames)
    start = min(o.test.start for o in nested.outer)
    end = int(ds.bars["available_at"].max() or 0) + 1  # type: ignore[arg-type]
    uni = sorted(set(ds.frame["instrument_id"].to_list()))
    spec = strategy_spec(
        models.info(cfg.family).task,
        cfg.timeframe,
        uni,
        ds.benchmark,
        strategy_params(cfg, ds.benchmark),
        Decimal(100_000),
    )
    ev = evaluate_event(dec, ds.bars, spec, start, end, snapshot_id=ds.snapshot_id)
    return EventEvidence(
        Series(ev.days, ev.rets),
        Series(ev.days, ev.rets_2x),
        ev.trades,
        ev.net_pnl_2x,
        ev.max_drawdown,
        ev.warnings,
        ev.result,
    )


def _fold_results(outer: list[OuterResult]) -> list[op.FoldResult]:
    out = []
    for o in outer:
        r = o.series.rets
        eq = np.cumsum(r)
        out.append(
            op.FoldResult(
                fold=o.fold,
                test=o.test,
                sharpe=float(sharpe(r)) if r.size >= 3 and math.isfinite(sharpe(r)) else 0.0,
                net_return=float(r.sum()),
                trades=o.trades,
                max_drawdown=float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0,
            )
        )
    return out


def _regimes(ds: mldataset.Dataset, s: Series) -> list[op.RegimeResult]:
    """Per market-volatility regime (majority regime of each session, past data only)."""
    if s.rets.size == 0:
        return []
    days = ds.frame.group_by("era").agg(pl.col("regime").mode().first()).rename({"era": "day"})
    m = dict(zip(days["day"].to_list(), days["regime"].to_list(), strict=True))
    lab = np.array([m.get(int(d), "unknown") for d in s.days])
    pos = float(np.sum(np.maximum(s.rets, 0)))
    out = []
    for g in np.unique(lab):
        r = s.rets[lab == g]
        sr = sharpe(r) if r.size >= 3 else math.nan
        out.append(
            op.RegimeResult(
                regime=str(g),
                n=int(r.size),
                sharpe=float(sr) if math.isfinite(sr) else 0.0,
                net_return=float(r.sum()),
                pnl_share=float(np.sum(np.maximum(r, 0)) / pos) if pos > 0 else 0.0,
            )
        )
    return out


def param_stability(nested: NestedResult) -> list[op.ParamStability]:
    """Mean and spread of the inner metric for final-selection trials near the best (EX-024)."""
    final = [t for t in nested.trials if t.outer_fold == -1 and t.status == op.TRIAL_STATUS_COMPLETE]
    best = next((t for t in final if t.trial_id == nested.final_trial_id), None)
    if best is None:
        return []
    out = []
    for k, v in best.params.items():
        if not isinstance(v, float):
            continue
        vals = [t for t in final if isinstance(t.params[k], float)] if final else []
        if not vals:
            continue
        span = max(abs(max(t.params[k] for t in vals) - min(t.params[k] for t in vals)), 1e-12)
        near = [t.inner_metric for t in vals if abs(t.params[k] - v) <= 0.25 * span]
        out.append(
            op.ParamStability(
                param=k,
                best_value=float(v),
                neighbor_mean=float(np.mean(near)) if near else 0.0,
                neighbor_std=float(np.std(near)) if near else 0.0,
            )
        )
    return out


def shuffled_label_check(cfg: tp.TrainingConfig, ds: mldataset.Dataset) -> bool:
    """Leakage check for ML candidates (BT-063 analogue): with labels shuffled inside each era the
    pipeline must find no skill out of fold."""
    rng = np.random.default_rng(int(cfg.seed) + 17)
    f = ds.frame
    y = f["y"].to_numpy().copy()
    eras = f["era"].to_numpy()
    for e in np.unique(eras):
        idx = np.flatnonzero(eras == e)
        y[idx] = y[rng.permutation(idx)]
    sh = mldataset.Dataset(
        f.with_columns(pl.Series("y", y)), ds.features, ds.task, ds.manifest, ds.bars, ds.benchmark, ds.feature_set
    )
    res = train(cfg, sh, size_curve=False, refit_final=False, explain=False)
    sk = (
        res.oos_metrics.get("oos_auc", math.nan) - 0.5
        if res.task == "classification"
        else res.oos_metrics.get("oos_ic", math.nan)
    )
    return not math.isfinite(sk) or abs(sk) < 0.05


def align_pair(a: Series, b: Series) -> tuple[np.ndarray, np.ndarray]:
    common = np.intersect1d(a.days, b.days)
    ia = {int(d): i for i, d in enumerate(a.days)}
    ib = {int(d): i for i, d in enumerate(b.days)}
    return np.array([a.rets[ia[int(d)]] for d in common]), np.array([b.rets[ib[int(d)]] for d in common])


def build_gate_report(
    candidate_id: str,
    nested: NestedResult,
    ds: mldataset.Dataset,
    cfg: tp.TrainingConfig,
    lineage_matrix: Matrix,
    eff: EffectiveN,
    champion: Series | None,
    champion_id: str,
    final_run: TrainingResult | None,
    survivorship: str,
    event: EventEvidence | None,
) -> op.GateReport:
    vec = nested.stitched("1x")
    s15 = nested.stitched("1.5x")
    s2 = nested.stitched("2x")
    engine = "event" if event is not None else "vectorized"
    oos = event.series if event is not None else vec
    oos2 = event.series_2x if event is not None else s2
    trades = event.trades if event is not None else sum(o.trades for o in nested.outer)
    eq = np.cumsum(oos.rets)
    max_dd = (
        event.max_drawdown if event is not None else (float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0)
    )
    net2 = event.net_pnl_2x if event is not None else float(np.sum(s2.rets)) * 100_000
    trial_mat = lineage_matrix.values if lineage_matrix.values.size else np.asarray(oos.rets)[:, None]
    champ_aligned = None
    rw_p = spa_p = math.nan
    if champion is not None and champion.rets.size >= 10:
        a, b = align_pair(oos, champion)
        if a.size >= 10:
            champ_aligned = b
            spa_p = spa_test(b, a[:, None]).p_consistent
            rw_p = float(romano_wolf(b, a[:, None])[0])
    oos_for_score = oos.rets if champ_aligned is None else align_pair(oos, champion)[0]  # type: ignore[arg-type]
    onc = effective_trials(trial_mat).n_effective if trial_mat.shape[1] else 1
    extra = max(eff.n_effective - onc, 0.0)
    verdict = score(
        CandidateEvidence(
            candidate_id=candidate_id,
            oos_returns=np.asarray(oos_for_score, dtype=np.float64),
            trial_returns=np.asarray(trial_mat, dtype=np.float64),
            trades=int(trades),
            net_pnl_2x=float(net2),
            max_drawdown=float(max_dd),
            champion_returns=champ_aligned,
            extra_trials=float(extra),
        ),
        POLICY,
    )
    reasons = list(verdict.reasons)
    leak_ok = True
    if final_run is not None:
        rep = build_report(final_run, f"gate_{candidate_id}")
        leak_ok = not any(f.code == "leakage_suspicion" for f in rep.findings)
        leak_ok = leak_ok and shuffled_label_check(cfg, ds)
    if not leak_ok:
        reasons.append("leakage")
    if engine != "event":
        reasons.append("engine")  # EX-051: only event-driven evidence may pass (BT-006)
    warnings = list(event.warnings) if event is not None else []
    if survivorship != "complete":
        warnings.append(f"survivorship status {survivorship} (O3): no promotion to paper (EX-052)")
    lo, hi = bootstrap_ci(oos.rets) if oos.rets.size >= 3 else (math.nan, math.nan)
    sr = sharpe(oos.rets) if oos.rets.size >= 3 else math.nan
    pos_folds = [o for o in nested.outer if o.series.rets.sum() > 0]

    def fin(x: float) -> float:
        return float(x) if x is not None and math.isfinite(x) else 0.0

    rep_msg = op.GateReport(
        report_id=f"gate_{candidate_id}",
        candidate_id=candidate_id,
        created_at=time.time_ns(),
        passed=not reasons,
        reasons=reasons,
        outer=op.OuterEstimate(
            sharpe=fin(sr),
            sharpe_annualized=fin(sr * math.sqrt(252)) if math.isfinite(sr) else 0.0,
            ci_low=fin(lo),
            ci_high=fin(hi),
            net_return=float(np.sum(oos.rets)),
            max_drawdown=float(max_dd),
            n_obs=int(oos.rets.size),
            positive_fold_fraction=len(pos_folds) / max(len(nested.outer), 1),
            trades=int(trades),
        ),
        dsr=fin(verdict.dsr),
        n_effective=fin(verdict.details.get("n_effective", math.nan)),
        pbo=fin(verdict.details.get("pbo", math.nan)),
        spa_p=fin(spa_p),
        romano_wolf_p=fin(rw_p),
        walk_forward=_fold_results(nested.outer),
        regimes=_regimes(ds, oos),
        cost_sensitivity=op.CostSensitivity(
            net_return={"1x": float(vec.rets.sum()), "1.5x": float(s15.rets.sum()), "2x": float(s2.rets.sum())},
            sharpe={
                k: fin(sharpe(v.rets)) if v.rets.size >= 3 else 0.0 for k, v in (("1x", vec), ("1.5x", s15), ("2x", s2))
            },
        ),
        param_stability=param_stability(nested),
        leakage_passed=leak_ok,
        survivorship_status=survivorship,
        warnings=warnings,
        thresholds={
            "dsr_min": POLICY.dsr_min,
            "pbo_max": POLICY.pbo_max,
            "spa_p_max": POLICY.spa_p_max,
            "min_trades": float(POLICY.min_trades),
            "max_drawdown": POLICY.max_drawdown,
        },
        engine=engine,
        champion_candidate_id=champion_id,
    )
    if oos2.rets.size:
        rep_msg.cost_sensitivity.net_return["2x_event"] = float(np.sum(oos2.rets))
    return rep_msg


def analyst_view(r: op.GateReport) -> op.GateReport:
    """EX-035 / BT-080: reasons and thresholds cleared for actor ``analyst``."""
    out = op.GateReport()
    out.CopyFrom(r)
    del out.reasons[:]
    out.thresholds.clear()
    return out
