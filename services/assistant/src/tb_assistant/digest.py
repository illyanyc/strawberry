"""Evidence digest: section 08/10/11/13 reports → citable metric values (AN-010).

The analyst never computes statistics; this module only selects values from reports and applies a
few fixed, documented reductions (means over folds, the largest share) so the planner and the
evidence check work on stable canonical names. Every value is keyed by the ID of the report it came
from; a finding may only cite ``(report_id, metric)`` pairs present in the round's evidence table.

Canonical names (the heuristic reference analyst and the evaluation suite use these):

``learning_curve.{gap,train_score,valid_score,valid_still_improving}``, ``folds.{mean,min,dispersion}``,
``importance.{top_share,top_sign_flip_rate}``, ``data.shift_psi_max``, ``leakage.flags``,
``calibration.ece``, ``finding.<code>.<evidence>``, ``oos.<metric>``,
``gate.{passed,dsr,pbo,spa_p}``, ``outer.{sharpe,positive_fold_fraction}``,
``backtest.{sharpe,sharpe_1_5x,sharpe_2x,net_return_2x}``, ``regime.max_share``,
``deflated.{best_dsr,pbo,lineage_trials,effective_trials,significance_reachable,best_sharpe_raw}``,
``cycle.{budget,used,spent}``, ``study.{pbo,deflated_sharpe,outer_sharpe,trials_completed,plateau_estimate,
pruned_rate,failure_rate}``, ``live.{decay_stat,decay_breached,feature_psi_max,prediction_psi,ece,match_rate,
slippage_gap_bps,below_p10,regime_unseen,shortfall_gap_bps}``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from tb.ml.v1 import training_pb2
from tb.monitor.v1 import feedback_pb2
from tb.optimizer.v1 import optimizer_pb2

REL_TOL = 1e-6
MAX_RAW_LEAVES = 400


@dataclass(frozen=True)
class Evidence:
    report_id: str
    metric: str
    value: float
    context: str = ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"report_id": self.report_id, "metric": self.metric, "value": self.value}
        if self.context:
            d["context"] = self.context
        return d


@dataclass
class EvidenceTable:
    """Every citable value of a round: canonical digest values plus flattened raw report leaves."""

    canonical: list[Evidence] = field(default_factory=list)
    _index: dict[tuple[str, str], float] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    report_ids: set[str] = field(default_factory=set)

    def add(self, report_id: str, metric: str, value: float, context: str = "") -> None:
        if not report_id or value is None or not math.isfinite(float(value)):
            return
        ev = Evidence(report_id, metric, float(value), context)
        self.canonical.append(ev)
        self._index[(report_id, metric)] = float(value)
        self.report_ids.add(report_id)

    def add_raw(self, report_id: str, msg: Message) -> None:
        """Index numeric leaves of a report under their dotted proto field paths (citable, not shown)."""
        self.report_ids.add(report_id)
        for n, (path, value) in enumerate(_leaves(MessageToDict(msg, preserving_proto_field_name=True))):
            if n >= MAX_RAW_LEAVES:
                break
            self._index.setdefault((report_id, path), value)

    def verify(self, report_id: str, metric: str, value: float) -> bool:
        """AN-010: the cited value must equal the reported value (relative tolerance 1e-6)."""
        ref = self._index.get((report_id, metric))
        if ref is None:
            return False
        return math.isclose(ref, float(value), rel_tol=REL_TOL, abs_tol=1e-12)

    def get(self, metric: str) -> float | None:
        for ev in reversed(self.canonical):
            if ev.metric == metric:
                return ev.value
        return None

    def get_ref(self, metric: str) -> Evidence | None:
        for ev in reversed(self.canonical):
            if ev.metric == metric:
                return ev
        return None


def _leaves(obj: Any, prefix: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "holdout" in str(k).lower():
                continue
            yield from _leaves(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            yield from _leaves(v, f"{prefix}[{i}]")
    elif isinstance(obj, bool):
        yield prefix, 1.0 if obj else 0.0
    elif isinstance(obj, int | float):
        f = float(obj)
        if math.isfinite(f):
            yield prefix, f


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _std(xs: list[float]) -> float:
    m = _mean(xs) or 0.0
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)) if xs else 0.0


def _pick_key(d: Any, *names: str) -> float | None:
    for n in names:
        if n in d:
            return float(d[n])
    return None


def digest_diagnostics(t: EvidenceTable, rep: training_pb2.DiagnosticsReport) -> None:
    rid = rep.report_id or rep.run_id
    t.add_raw(rid, rep)
    lc = rep.learning_curves
    if lc.per_fold or lc.overfitting_gap:
        t.add(rid, "learning_curve.gap", lc.overfitting_gap)
    trains, vals, still = [], [], 0
    for fc in lc.per_fold:
        if not fc.points:
            continue
        best = next((p for p in fc.points if p.step == fc.best_step), fc.points[-1])
        trains.append(best.train_metric)
        vals.append(best.val_metric)
        if fc.best_step >= fc.points[-1].step:
            still += 1
    if trains:
        t.add(rid, "learning_curve.train_score", _mean(trains) or 0.0)
        t.add(rid, "learning_curve.valid_score", _mean(vals) or 0.0)
        t.add(rid, "learning_curve.valid_still_improving", 1.0 if still * 2 >= len(trains) else 0.0)
    fold_vals: list[float] = []
    for fm in rep.fold_metrics:
        v = _pick_key(fm.backtest, "sharpe")
        if v is None:
            v = _pick_key(fm.metrics, "val_ic", "val_rank_ic", "val_auc", "val_ndcg")
        if v is not None:
            fold_vals.append(v)
    if len(fold_vals) >= 2:
        m = _mean(fold_vals) or 0.0
        t.add(rid, "folds.mean", m)
        t.add(rid, "folds.min", min(fold_vals))
        t.add(rid, "folds.dispersion", _std(fold_vals) / max(abs(m), 1e-9) if abs(m) > 1e-9 else 99.0)
    stab = list(rep.importance.stability)
    if stab:
        total = sum(abs(s.mean) for s in stab)
        top = max(stab, key=lambda s: abs(s.mean))
        if total > 0:
            t.add(rid, "importance.top_share", abs(top.mean) / total)
            t.add(rid, "importance.top_sign_flip_rate", top.sign_flip_rate)
            t.context["top_feature"] = top.feature
    shift = [s.psi for s in rep.feature_diagnostics.shift]
    if shift:
        t.add(rid, "data.shift_psi_max", max(shift))
    t.add(rid, "leakage.flags", float(len(rep.feature_diagnostics.leakage_flags)))
    cal = [s.calibration_error for s in rep.error_slices if s.calibration_error]
    if cal:
        t.add(rid, "calibration.ece", max(cal))
    for f in rep.findings:
        for e in f.evidence:
            t.add(rid, f"finding.{f.code}.{e.name}", e.value, context=f.message[:120])
    t.context.setdefault("findings", []).extend(
        {"code": f.code, "severity": training_pb2.Severity.Name(f.severity), "report_id": rid} for f in rep.findings
    )


def digest_run(t: EvidenceTable, run: training_pb2.TrainingRun) -> None:
    rid = run.run_id
    t.add_raw(rid, run)
    for k, v in sorted(run.oos_metrics.items()):
        t.add(rid, f"oos.{k}", v)
    t.context["family"] = run.config.family


def _cost(sens: optimizer_pb2.CostSensitivity, mult: str) -> float | None:
    keys = {
        "1": ("1x", "1.0x", "1", "1.0", "x1"),
        "1.5": ("1.5x", "1.5", "x1.5"),
        "2": ("2x", "2.0x", "2", "2.0", "x2"),
    }
    return _pick_key(sens.sharpe, *keys[mult])


def digest_gate(t: EvidenceTable, rep: optimizer_pb2.GateReport) -> None:
    rid = rep.report_id or rep.candidate_id
    t.add_raw(rid, rep)
    t.add(rid, "gate.passed", 1.0 if rep.passed else 0.0)
    t.add(rid, "gate.dsr", rep.dsr)
    t.add(rid, "gate.pbo", rep.pbo)
    if rep.spa_p:
        t.add(rid, "gate.spa_p", rep.spa_p)
    if rep.HasField("outer"):
        t.add(rid, "outer.sharpe", rep.outer.sharpe)
        t.add(rid, "outer.positive_fold_fraction", rep.outer.positive_fold_fraction)
    _digest_cost(t, rid, rep.cost_sensitivity)
    _digest_regimes(t, rid, list(rep.regimes))


def _digest_cost(t: EvidenceTable, rid: str, sens: optimizer_pb2.CostSensitivity) -> None:
    for mult, name in (("1", "backtest.sharpe"), ("1.5", "backtest.sharpe_1_5x"), ("2", "backtest.sharpe_2x")):
        v = _cost(sens, mult)
        if v is not None:
            t.add(rid, name, v)
    nr2 = _pick_key(sens.net_return, "2x", "2.0x", "2", "2.0")
    if nr2 is not None:
        t.add(rid, "backtest.net_return_2x", nr2)


def _digest_regimes(t: EvidenceTable, rid: str, regimes: list[optimizer_pb2.RegimeResult]) -> None:
    if not regimes:
        return
    best = max(regimes, key=lambda r: r.pnl_share)
    t.add(rid, "regime.max_share", best.pnl_share, context=f"regime {best.regime}")
    t.context["best_regime"] = best.regime


def digest_cost(t: EvidenceTable, report_id: str, sens: optimizer_pb2.CostSensitivity) -> None:
    t.add_raw(report_id, sens)
    _digest_cost(t, report_id, sens)


def digest_regimes(t: EvidenceTable, report_id: str, regimes: list[optimizer_pb2.RegimeResult]) -> None:
    _digest_regimes(t, report_id, regimes)


def digest_cumulative(t: EvidenceTable, stats: optimizer_pb2.CumulativeOverfittingStats, report_id: str) -> None:
    t.add_raw(report_id, stats)
    t.add(report_id, "deflated.best_dsr", stats.deflated_sharpe_prob)
    t.add(report_id, "deflated.pbo", stats.pbo)
    t.add(report_id, "deflated.lineage_trials", float(stats.n_trials))
    t.add(report_id, "deflated.effective_trials", stats.n_effective)
    t.add(report_id, "deflated.significance_reachable", 1.0 if stats.significance_reachable else 0.0)
    t.add(report_id, "deflated.best_sharpe_raw", stats.best_sharpe)


def digest_ledger(t: EvidenceTable, ledger: optimizer_pb2.TrialLedger, report_id: str) -> None:
    t.add_raw(report_id, ledger)
    open_cycles = [c for c in ledger.cycles if c.status == optimizer_pb2.CYCLE_STATUS_OPEN]
    cur = open_cycles[-1] if open_cycles else (ledger.cycles[-1] if ledger.cycles else None)
    if cur is not None:
        t.add(report_id, "cycle.budget", cur.effective_trial_budget)
        t.add(report_id, "cycle.used", cur.effective_trials_used)
        t.add(report_id, "cycle.spent", 0.0 if cur.status == optimizer_pb2.CYCLE_STATUS_OPEN else 1.0)
    t.add(report_id, "ledger.trials_total", float(ledger.trials_total))
    for src, n in sorted(ledger.by_source.items()):
        t.add(report_id, f"ledger.by_source.{src}", float(n))


def digest_study(t: EvidenceTable, study: optimizer_pb2.Study) -> None:
    rid = study.study_id
    t.add_raw(rid, study)
    t.add(rid, "study.pbo", study.pbo)
    t.add(rid, "study.deflated_sharpe", study.deflated_sharpe)
    t.add(rid, "study.trials_completed", float(study.trials_completed))
    if study.HasField("outer_estimate"):
        t.add(rid, "study.outer_sharpe", study.outer_estimate.sharpe)


def digest_study_diagnostics(t: EvidenceTable, d: optimizer_pb2.StudyDiagnostics) -> None:
    rid = d.study_id + "#diagnostics"
    t.add_raw(rid, d)
    t.add(rid, "study.plateau_estimate", float(d.plateau_estimate))
    t.add(rid, "study.pruned_rate", d.pruned_rate)
    t.add(rid, "study.failure_rate", d.failure_rate)


def digest_feedback(t: EvidenceTable, rep: feedback_pb2.LiveFeedbackReport) -> None:
    rid = rep.report_id
    t.add_raw(rid, rep)
    if rep.HasField("decay"):
        t.add(rid, "live.decay_stat", rep.decay.statistic)
        t.add(rid, "live.decay_breached", 1.0 if rep.decay.breached else 0.0)
    if rep.feature_drift:
        top = max(rep.feature_drift, key=lambda f: f.psi)
        t.add(rid, "live.feature_psi_max", top.psi, context=f"feature {top.feature}")
    if rep.HasField("prediction_drift"):
        t.add(rid, "live.prediction_psi", rep.prediction_drift.psi)
    if rep.HasField("calibration") and rep.calibration.resolved:
        t.add(rid, "live.ece", rep.calibration.ece)
    if rep.HasField("parity"):
        t.add(rid, "live.match_rate", rep.parity.trade_match_rate)
        t.add(rid, "live.slippage_gap_bps", rep.parity.slippage_bps_live - rep.parity.slippage_bps_modeled)
    for m in rep.metrics:
        t.add(rid, f"live.{m.name}.percentile", m.percentile)
    below = [c for c in rep.triggered if c.condition == "below_p10"]
    t.add(rid, "live.below_p10", 1.0 if below else 0.0)
    if rep.HasField("regime"):
        t.add(rid, "live.regime_unseen", 0.0 if rep.regime.seen_in_training else 1.0)
    if rep.HasField("execution"):
        t.add(rid, "live.shortfall_gap_bps", rep.execution.shortfall_bps - rep.execution.modeled_shortfall_bps)
    t.add(rid, "live.sample_sufficient", 1.0 if rep.sample_sufficient else 0.0)
