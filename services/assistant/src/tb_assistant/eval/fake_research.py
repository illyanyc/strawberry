"""In-memory stand-in for `MLService` + `OptimizerService` (tests and the evaluation suite).

Implements the same single ``call(service, method, request)`` method as
:class:`tb_assistant.research.GrpcResearch`, so the tool server, loop, and guardrails run unchanged.
It enforces what the real services enforce for actor ``analyst`` (EX-030..EX-036, ML-024): writes
need level 1, paper promotion needs level 2, a paper champion to replace (never a new spec), and the
minimum shadow period; live-eligible and live are never reachable; raising autonomy is refused;
launches stop when the cycle's effective-trial budget is spent.

Trial outcomes come from a small deterministic world model (``World``): each training config maps to
an out-of-fold Sharpe from a "true" edge, a regularization sweet spot, and seeded noise. The
lineage's deflated Sharpe probability uses the expected-maximum-Sharpe correction over all trials,
so searching longer on noise does not produce a significant result.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import grpc
from google.protobuf import json_format, struct_pb2
from google.protobuf.message import Message

from tb.ml.v1 import ml_pb2, ml_service_pb2, training_pb2
from tb.optimizer.v1 import optimizer_pb2
from tb_assistant.research import ML, OPT, ResearchCallError

LGBM_SCHEMA = (
    '{"type":"object","properties":{'
    '"num_leaves":{"type":"integer","minimum":2,"maximum":256},'
    '"lambda_l2":{"type":"number","minimum":0,"maximum":1000},'
    '"min_child_samples":{"type":"integer","minimum":1,"maximum":1000},'
    '"learning_rate":{"type":"number","minimum":0.001,"maximum":0.5},'
    '"num_boost_round":{"type":"integer","minimum":10,"maximum":5000}}}'
)
RIDGE_SCHEMA = '{"type":"object","properties":{"alpha":{"type":"number","minimum":0,"maximum":1000}}}'


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _noise(key: str) -> float:
    """Deterministic standard-normal-ish noise from a string (Box-Muller on two hash draws)."""
    h = hashlib.sha256(key.encode()).digest()
    u1 = (int.from_bytes(h[:8], "big") + 1) / (2**64 + 2)
    u2 = int.from_bytes(h[8:16], "big") / 2**64
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)


@dataclass
class World:
    """Ground truth of one research lineage."""

    true_sharpe: float = 0.0  # per-session Sharpe of the best achievable spec (0 = pure noise)
    noise_sd: float = 0.03
    # Regularization sweet spot (lambda_l2) and how fast performance falls off around it (log10 units).
    sweet_lambda: float = 30.0
    base_gap: float = 0.35  # train/valid gap at lambda_l2 = 1
    leaky_feature: str = ""  # a feature that fakes skill until ablated
    regime_share: float = 0.4
    cost_fragile: bool = False
    n_obs: int = 1500
    available_years: float = 3.0

    def outcome(self, cfg: training_pb2.TrainingConfig, seed_key: str) -> dict[str, float]:
        hp = json_format.MessageToDict(cfg.hyperparams)
        lam = float(hp.get("lambda_l2", 1.0)) if cfg.family == "lightgbm" else 50.0
        dist = abs(math.log10(max(lam, 1e-3)) - math.log10(self.sweet_lambda))
        quality = max(0.0, 1.0 - 0.45 * dist)
        gap = max(0.02, self.base_gap * (1.0 - min(1.0, math.log10(max(lam, 1.0)) / 2.5)))
        if cfg.family == "ridge":
            quality, gap = 0.7, 0.03
        sr = self.true_sharpe * quality + self.noise_sd * _noise(seed_key)
        dropped = set(cfg.feature_set.name.split("-minus-")[1:]) if "-minus-" in cfg.feature_set.name else set()
        leak_active = bool(self.leaky_feature) and self.leaky_feature not in dropped
        if leak_active:
            sr += 0.08
        valid = 0.02 + 0.6 * max(sr, 0.0)
        return {"sharpe": sr, "gap": gap, "train": valid + gap, "valid": valid, "leak": 1.0 if leak_active else 0.0}


@dataclass
class Lineage:
    proto: optimizer_pb2.Lineage
    world: World
    trials: list[optimizer_pb2.Trial] = field(default_factory=list)
    cycle: optimizer_pb2.ResearchCycle = field(default_factory=optimizer_pb2.ResearchCycle)
    promotions: int = 0


class FakeResearch:
    def __init__(self) -> None:
        self.lineages: dict[str, Lineage] = {}
        self.runs: dict[str, training_pb2.TrainingRun] = {}
        self.diagnostics: dict[str, training_pb2.DiagnosticsReport] = {}
        self.jobs: dict[str, training_pb2.TrainingJob] = {}
        self.studies: dict[str, optimizer_pb2.Study] = {}
        self.candidates: dict[str, optimizer_pb2.Candidate] = {}
        self.gates: dict[str, optimizer_pb2.GateReport] = {}
        self.completions: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.denials: list[tuple[str, str]] = []
        self.fail_jobs = False
        self._n = 0
        self.on_complete: Callable[[dict[str, Any]], None] | None = None

    # ---- setup helpers

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n:04d}"

    def add_lineage(
        self,
        lineage_id: str,
        *,
        level: int = 0,
        world: World | None = None,
        cycle_budget: float = 30.0,
        family: str = "lightgbm",
    ) -> Lineage:
        p = optimizer_pb2.Lineage(
            lineage_id=lineage_id,
            name=lineage_id,
            family=family,
            timeframe="15m",
            universe="dev-intraday",
            autonomy_level=level,
            current_cycle_id=f"{lineage_id}-c1",
            holdout_boundary=1_790_000_000 * 10**9,
        )
        p.analyst_budget.max_trials = 200
        p.analyst_budget.max_cpu_hours = 100.0
        lin = Lineage(p, world or World())
        lin.cycle = optimizer_pb2.ResearchCycle(
            cycle_id=p.current_cycle_id,
            lineage_id=lineage_id,
            effective_trial_budget=cycle_budget,
            status=optimizer_pb2.CYCLE_STATUS_OPEN,
            available_years=3.0,
            target_sharpe=1.0,
        )
        self.lineages[lineage_id] = lin
        return lin

    def base_config(self, lineage_id: str, **hp: Any) -> training_pb2.TrainingConfig:
        cfg = training_pb2.TrainingConfig(
            name="base",
            family=self.lineages[lineage_id].proto.family,
            lineage_id=lineage_id,
            timeframe="15m",
            seed=7,
            data_source="synthetic:noise",
        )
        cfg.feature_set.name = "intraday_v1"
        cfg.label.kind = "residual_return"
        cfg.label.horizon = 2
        cfg.universe.name = "dev-intraday"
        cfg.period.start = 1_700_000_000 * 10**9
        cfg.period.end = 1_760_000_000 * 10**9
        cfg.hyperparams.update(
            hp or {"num_leaves": 63, "lambda_l2": 1.0, "min_child_samples": 20, "learning_rate": 0.05}
        )
        return cfg

    def run_owner_training(
        self, lineage_id: str, cfg: training_pb2.TrainingConfig | None = None
    ) -> training_pb2.TrainingRun:
        cfg = cfg or self.base_config(lineage_id)
        job = self._submit(cfg, training_pb2.ACTOR_OWNER, "")
        return self.runs[job.run_id]

    # ---- the single entry point

    async def call(self, service: str, method: str, request: Message) -> Message:
        self.calls.append((service, method))
        if service not in {ML, OPT}:
            raise ResearchCallError(service, method, "PERMISSION_DENIED", "unknown service")
        fn = getattr(self, f"_{method}", None)
        if fn is None:
            raise ResearchCallError(service, method, "UNIMPLEMENTED", method)
        try:
            result: Message = fn(request)
        except KeyError as exc:
            raise ResearchCallError(service, method, "NOT_FOUND", str(exc)) from None
        return result

    def _deny(self, method: str, reason: str) -> None:
        self.denials.append((method, reason))
        raise ResearchCallError(OPT, method, grpc.StatusCode.PERMISSION_DENIED.name, reason)

    def _lin(self, lineage_id: str) -> Lineage:
        if lineage_id not in self.lineages:
            raise ResearchCallError(OPT, "GetLineage", "NOT_FOUND", lineage_id)
        return self.lineages[lineage_id]

    # ---- world mechanics

    def _stats(self, lin: Lineage) -> optimizer_pb2.CumulativeOverfittingStats:
        srs = [t.sharpe for t in lin.trials if t.status == optimizer_pb2.TRIAL_STATUS_COMPLETE]
        n = len(lin.trials)
        st = optimizer_pb2.CumulativeOverfittingStats(lineage_id=lin.proto.lineage_id, n_trials=n, dsr_threshold=0.95)
        if not srs:
            st.significance_reachable = True
            return st
        mean = sum(srs) / len(srs)
        var = sum((x - mean) ** 2 for x in srs) / max(1, len(srs) - 1) if len(srs) > 1 else lin.world.noise_sd**2
        sd = math.sqrt(max(var, 1e-8))
        n_eff = max(1.0, min(float(n), 1.0 + (n - 1) * 0.6))
        e_max = sd * math.sqrt(2.0 * math.log(n_eff)) if n_eff > 1 else 0.0
        best = max(srs)
        best_t = max((t for t in lin.trials if t.status == optimizer_pb2.TRIAL_STATUS_COMPLETE), key=lambda t: t.sharpe)
        se = math.sqrt((1 + 0.5 * best**2) / lin.world.n_obs)
        st.n_effective = n_eff
        st.sr_variance = var
        st.best_trial_id = best_t.trial_id
        st.best_sharpe = best
        st.deflated_sharpe_prob = _phi((best - e_max) / se)
        overfit = sum(1 for x in srs if x < mean) / len(srs)
        st.pbo = (
            0.62 if lin.world.true_sharpe == 0 else max(0.05, 0.6 - 4 * lin.world.true_sharpe) * (0.9 + 0.2 * overfit)
        )
        cap = lin.cycle.effective_trial_budget
        e_cap = sd * math.sqrt(2.0 * math.log(max(cap, 2.0)))
        # Early on the cross-trial dispersion is barely estimated: assume at least 0.05.
        sd_reach = max(sd, 0.05) if len(srs) < 5 else sd
        st.significance_reachable = (best + 2 * sd_reach) - e_cap > 1.645 * se
        st.available_years = lin.world.available_years
        return st

    def _submit(
        self, cfg: training_pb2.TrainingConfig, actor: int, round_id: str, kind: str = "training", base: str = ""
    ) -> training_pb2.TrainingJob:
        lin = self._lin(cfg.lineage_id)
        if actor == training_pb2.ACTOR_ANALYST:
            if lin.cycle.status != optimizer_pb2.CYCLE_STATUS_OPEN:
                raise ResearchCallError(ML, "SubmitTrainingJob", "FAILED_PRECONDITION", "cycle_spent")
            if len(lin.trials) >= lin.proto.analyst_budget.max_trials:
                raise ResearchCallError(ML, "SubmitTrainingJob", "RESOURCE_EXHAUSTED", "analyst budget spent")
        job_id, run_id = self._id("job"), self._id("run")
        caller = training_pb2.CallerContext(actor=actor, analyst_round_id=round_id)  # type: ignore[arg-type]
        job = training_pb2.TrainingJob(
            job_id=job_id, run_id=run_id, config=cfg, launched_by=caller, kind=kind, base_run_id=base
        )
        hp_key = json.dumps(json_format.MessageToDict(cfg.hyperparams), sort_keys=True)
        out = lin.world.outcome(
            cfg,
            f"{cfg.lineage_id}:{len(lin.trials)}:{hp_key}:{cfg.feature_set.name}",
        )
        failed = self.fail_jobs and actor == training_pb2.ACTOR_ANALYST
        job.status = training_pb2.JOB_STATUS_FAILED if failed else training_pb2.JOB_STATUS_SUCCEEDED
        self.jobs[job_id] = job
        trial = optimizer_pb2.Trial(
            trial_id=self._id("trial"),
            lineage_id=cfg.lineage_id,
            cycle_id=lin.cycle.cycle_id,
            number=len(lin.trials),
            source=training_pb2.Actor.ValueType(actor),
            status=optimizer_pb2.TRIAL_STATUS_FAILED if failed else optimizer_pb2.TRIAL_STATUS_COMPLETE,
            sharpe=out["sharpe"],
            n_obs=lin.world.n_obs,
            analyst_round_id=round_id,
            run_ids=[run_id],
        )
        lin.trials.append(trial)
        lin.cycle.effective_trials_used = self._stats(lin).n_effective
        if lin.cycle.effective_trials_used >= lin.cycle.effective_trial_budget:
            lin.cycle.status = optimizer_pb2.CYCLE_STATUS_SPENT
        if actor == training_pb2.ACTOR_ANALYST:
            lin.proto.analyst_used.trials += 1
        rep_id = self._id("diag")
        run = training_pb2.TrainingRun(
            run_id=run_id,
            job_id=job_id,
            config=cfg,
            diagnostics_report_id=rep_id,
            lineage_id=cfg.lineage_id,
            trial_id=trial.trial_id,
            launched_by=caller,
            base_run_id=base,
            status=job.status,
        )
        run.oos_metrics["sharpe"] = out["sharpe"]
        run.oos_metrics["ic"] = out["valid"]
        self.runs[run_id] = run
        self.diagnostics[run_id] = self._diag(rep_id, run_id, out, cfg, lin.world)
        self.completions.append(
            {
                "kind": "run",
                "run_id": run_id,
                "job_id": job_id,
                "lineage_id": cfg.lineage_id,
                "analyst_round_id": round_id,
                "failed": failed,
                "report_id": rep_id,
            }
        )
        return job

    def _diag(
        self, rep_id: str, run_id: str, out: dict[str, float], cfg: training_pb2.TrainingConfig, w: World
    ) -> training_pb2.DiagnosticsReport:
        rep = training_pb2.DiagnosticsReport(report_id=rep_id, run_id=run_id, holdout_excluded=True, family=cfg.family)
        lc = rep.learning_curves
        lc.overfitting_gap = out["gap"]
        for f in range(3):
            fc = lc.per_fold.add(fold=f, metric="ic", best_step=60)
            for step in (20, 40, 60, 80, 100):
                fc.points.add(
                    step=step,
                    train_metric=out["train"] * step / 100 + 0.01,
                    val_metric=out["valid"] if step >= 60 else out["valid"] * 0.8,
                )
            fc.points[2].train_metric, fc.points[2].val_metric = out["train"], out["valid"]
        for f in range(4):
            fm = rep.fold_metrics.add(fold=f)
            fm.backtest["sharpe"] = out["sharpe"] + 0.02 * _noise(f"{run_id}:{f}")
            fm.metrics["val_ic"] = out["valid"]
        feats = ["ret_1", "ret_4", "vol_20", "rvol", "gap"] + ([w.leaky_feature] if w.leaky_feature else [])
        for name in feats:
            imp = 5.0 if out["leak"] and name == w.leaky_feature else 1.0
            rep.importance.stability.add(feature=name, mean=imp, std=0.1, sign_flip_rate=0.0)
        rep.importance.method = "gain"
        rep.error_slices.add(
            slice_by="regime", slice="high_vol", n=500, metric=out["valid"] * 0.5, calibration_error=0.04
        )
        if out["gap"] > 0.15:
            rep.findings.add(
                code="overfitting",
                severity=training_pb2.SEVERITY_WARNING,
                message="train/valid gap",
                evidence=[training_pb2.Evidence(name="ic_gap", value=out["gap"], threshold=0.15)],
            )
        rep.worst_samples.add(
            instrument_id="inst-aapl-0001", decision_time=1_750_000_000 * 10**9, label=-0.02, prediction=0.03
        )
        return rep

    # ---- MLService

    def _ListModelFamilies(self, req: Message) -> Message:
        return ml_service_pb2.ListModelFamiliesResponse(
            families=[
                training_pb2.ModelFamily(
                    family="lightgbm", tier=1, task="regression", hyperparam_schema_json=LGBM_SCHEMA
                ),
                training_pb2.ModelFamily(
                    family="ridge", tier=1, task="regression", hyperparam_schema_json=RIDGE_SCHEMA
                ),
            ]
        )

    def _ListFeatureSets(self, req: Message) -> Message:
        return ml_service_pb2.ListFeatureSetsResponse(
            feature_sets=[training_pb2.FeatureSetInfo(name="intraday_v1", version=1)]
        )

    def _ListLabelPresets(self, req: Message) -> Message:
        return ml_service_pb2.ListLabelPresetsResponse()

    def _EstimateTrainingJob(self, req: ml_service_pb2.EstimateTrainingJobRequest) -> Message:
        errors: list[str] = []
        if req.config.family not in {"lightgbm", "ridge"}:
            errors.append(f"unknown family {req.config.family}")
        lin = self.lineages.get(req.config.lineage_id)
        if lin and req.config.period.end > lin.proto.holdout_boundary:
            errors.append("period overlaps the holdout")
        hp = json_format.MessageToDict(req.config.hyperparams)
        allowed = {
            "lightgbm": {"num_leaves", "lambda_l2", "min_child_samples", "learning_rate", "num_boost_round"},
            "ridge": {"alpha"},
        }
        for k in hp:
            if k not in allowed.get(req.config.family, set()):
                errors.append(f"unknown hyperparameter {k}")
        return ml_service_pb2.EstimateTrainingJobResponse(
            estimate=training_pb2.JobEstimate(cpu_seconds=900, fits=4), errors=errors
        )

    def _SubmitTrainingJob(self, req: ml_service_pb2.SubmitTrainingJobRequest) -> Message:
        actor = (
            training_pb2.ACTOR_ANALYST if req.caller.actor == training_pb2.ACTOR_ANALYST else training_pb2.ACTOR_OWNER
        )
        return ml_service_pb2.SubmitTrainingJobResponse(
            job=self._submit(req.config, actor, req.caller.analyst_round_id)
        )

    def _SubmitAblation(self, req: ml_service_pb2.SubmitAblationRequest) -> Message:
        base = self.runs[req.run_id]
        jobs = []
        for v in req.variants:
            cfg = training_pb2.TrainingConfig()
            cfg.CopyFrom(base.config)
            if v.drop_feature_groups:
                cfg.feature_set.name = cfg.feature_set.name + "".join(f"-minus-{g}" for g in v.drop_feature_groups)
            if v.hyperparams.fields:
                cfg.hyperparams.update(json_format.MessageToDict(v.hyperparams))
            jobs.append(
                self._submit(
                    cfg, training_pb2.ACTOR_ANALYST, req.caller.analyst_round_id, kind="ablation", base=req.run_id
                )
            )
        return ml_service_pb2.SubmitAblationResponse(jobs=jobs)

    def _GetJob(self, req: ml_service_pb2.GetJobRequest) -> Message:
        return ml_service_pb2.GetJobResponse(job=self.jobs[req.job_id])

    def _GetRun(self, req: ml_service_pb2.GetRunRequest) -> Message:
        if req.run_id not in self.runs:
            raise ResearchCallError(ML, "GetRun", "NOT_FOUND", req.run_id)
        return ml_service_pb2.GetRunResponse(run=self.runs[req.run_id])

    def _GetDiagnostics(self, req: ml_service_pb2.GetDiagnosticsRequest) -> Message:
        if req.run_id not in self.diagnostics:
            raise ResearchCallError(ML, "GetDiagnostics", "NOT_FOUND", req.run_id)
        return ml_service_pb2.GetDiagnosticsResponse(report=self.diagnostics[req.run_id])

    def _GetLearningCurves(self, req: ml_service_pb2.GetLearningCurvesRequest) -> Message:
        return ml_service_pb2.GetLearningCurvesResponse(curves=self.diagnostics[req.run_id].learning_curves)

    def _GetFeatureImportance(self, req: ml_service_pb2.GetFeatureImportanceRequest) -> Message:
        return ml_service_pb2.GetFeatureImportanceResponse(importance=self.diagnostics[req.run_id].importance)

    def _GetErrorSlices(self, req: ml_service_pb2.GetErrorSlicesRequest) -> Message:
        return ml_service_pb2.GetErrorSlicesResponse(slices=self.diagnostics[req.run_id].error_slices)

    def _AnnotateRun(self, req: ml_service_pb2.AnnotateRunRequest) -> Message:
        self.runs[req.run_id].notes.append(req.note)
        return ml_service_pb2.AnnotateRunResponse()

    # ---- OptimizerService

    def _GetLineage(self, req: optimizer_pb2.GetLineageRequest) -> Message:
        return optimizer_pb2.GetLineageResponse(lineage=self._lin(req.lineage_id).proto)

    def _ListLineages(self, req: Message) -> Message:
        return optimizer_pb2.ListLineagesResponse(lineages=[x.proto for x in self.lineages.values()])

    def _SetLineageAutonomy(self, req: optimizer_pb2.SetLineageAutonomyRequest) -> Message:
        lin = self._lin(req.lineage_id)
        if req.caller.actor == training_pb2.ACTOR_ANALYST and req.level > lin.proto.autonomy_level:
            self._deny("SetLineageAutonomy", "the analyst may only lower its autonomy (EX-033)")
        lin.proto.autonomy_level = req.level
        return optimizer_pb2.SetLineageAutonomyResponse(lineage=lin.proto)

    def _GetTrialLedger(self, req: optimizer_pb2.GetTrialLedgerRequest) -> Message:
        lin = self._lin(req.lineage_id)
        led = optimizer_pb2.TrialLedger(
            lineage_id=req.lineage_id, trials_total=len(lin.trials), holdout_evaluations=1, cycles=[lin.cycle]
        )
        for t in lin.trials:
            src = {training_pb2.ACTOR_OWNER: "owner", training_pb2.ACTOR_ANALYST: "analyst"}.get(t.source, "schedule")
            led.by_source[src] = led.by_source.get(src, 0) + 1
        return optimizer_pb2.GetTrialLedgerResponse(ledger=led)

    def _ListTrials(self, req: optimizer_pb2.ListTrialsRequest) -> Message:
        return optimizer_pb2.ListTrialsResponse(trials=self._lin(req.lineage_id).trials)

    def _GetCumulativeOverfittingStats(self, req: optimizer_pb2.GetCumulativeOverfittingStatsRequest) -> Message:
        return optimizer_pb2.GetCumulativeOverfittingStatsResponse(stats=self._stats(self._lin(req.lineage_id)))

    def _CreateStudy(self, req: optimizer_pb2.CreateStudyRequest) -> Message:
        lin = self._lin(req.spec.lineage_id)
        if req.caller.actor == training_pb2.ACTOR_ANALYST and lin.cycle.status != optimizer_pb2.CYCLE_STATUS_OPEN:
            raise ResearchCallError(OPT, "CreateStudy", "FAILED_PRECONDITION", "cycle_spent")
        if not req.spec.preregistration.hypothesis:
            raise ResearchCallError(OPT, "CreateStudy", "INVALID_ARGUMENT", "pre-registration required (EX-026)")
        st = optimizer_pb2.Study(
            study_id=self._id("study"), spec=req.spec, status=optimizer_pb2.STUDY_STATUS_CREATED, created_by=req.caller
        )
        self.studies[st.study_id] = st
        return optimizer_pb2.CreateStudyResponse(study=st)

    def _StartStudy(self, req: optimizer_pb2.StartStudyRequest) -> Message:
        st = self.studies[req.study_id]
        lin = self._lin(st.spec.lineage_id)
        cfg = training_pb2.TrainingConfig()
        cfg.CopyFrom(st.spec.base_config)
        cfg.lineage_id = st.spec.lineage_id
        n = max(1, min(req.budget.max_trials or st.spec.budget.max_trials or 5, 20))
        best = None
        for i in range(n):
            if lin.cycle.status != optimizer_pb2.CYCLE_STATUS_OPEN:
                break
            c = training_pb2.TrainingConfig()
            c.CopyFrom(cfg)
            for ps in st.spec.search_space:
                frac = (i + 0.5) / n
                v = (
                    ps.low * (ps.high / ps.low) ** frac
                    if ps.kind == "log_float" and ps.low > 0
                    else ps.low + (ps.high - ps.low) * frac
                )
                c.hyperparams.update({ps.name: round(v) if ps.kind == "int" else v})
            job = self._submit(c, req.caller.actor, req.caller.analyst_round_id or st.created_by.analyst_round_id)
            self.completions.pop()  # study trials do not emit run completions to the analyst loop here
            sr = self.runs[job.run_id].oos_metrics["sharpe"]
            if best is None or sr > best[0]:
                best = (sr, job.run_id, c)
        st.status = optimizer_pb2.STUDY_STATUS_COMPLETED
        st.trials_completed = n
        stats = self._stats(lin)
        st.pbo, st.deflated_sharpe = stats.pbo, stats.deflated_sharpe_prob
        if best is not None:
            cand = self._candidate(lin, best[1], best[2], st.study_id, stats)
            st.candidate_id, st.gate_report_id = cand.candidate_id, cand.gate_report_id
        self.completions.append(
            {
                "kind": "study",
                "study_id": st.study_id,
                "lineage_id": lin.proto.lineage_id,
                "analyst_round_id": req.caller.analyst_round_id or st.created_by.analyst_round_id,
                "candidate_id": st.candidate_id,
                "failed": False,
            }
        )
        return optimizer_pb2.StartStudyResponse(study=st)

    def _candidate(
        self,
        lin: Lineage,
        run_id: str,
        cfg: training_pb2.TrainingConfig,
        study_id: str,
        stats: optimizer_pb2.CumulativeOverfittingStats,
    ) -> optimizer_pb2.Candidate:
        cid = self._id("cand")
        gate = optimizer_pb2.GateReport(
            report_id=self._id("gate"),
            candidate_id=cid,
            dsr=stats.deflated_sharpe_prob,
            pbo=stats.pbo,
            engine="event",
            reasons=["dsr below threshold"],
            survivorship_status="complete",
        )
        gate.thresholds["dsr"] = 0.95
        gate.passed = stats.deflated_sharpe_prob >= 0.95 and stats.pbo <= 0.2
        gate.cost_sensitivity.sharpe["1x"] = stats.best_sharpe
        gate.cost_sensitivity.sharpe["2x"] = stats.best_sharpe - (0.08 if lin.world.cost_fragile else 0.01)
        gate.regimes.add(regime="high_vol", n=300, sharpe=stats.best_sharpe * 2, pnl_share=lin.world.regime_share)
        gate.regimes.add(regime="low_vol", n=450, sharpe=stats.best_sharpe * 0.5, pnl_share=1 - lin.world.regime_share)
        self.gates[cid] = gate
        stage = ml_pb2.REGISTRY_STAGE_VALIDATED if gate.passed else ml_pb2.REGISTRY_STAGE_CANDIDATE
        cand = optimizer_pb2.Candidate(
            candidate_id=cid,
            lineage_id=lin.proto.lineage_id,
            study_id=study_id,
            run_id=run_id,
            config=cfg,
            stage=stage,
            gate_report_id=gate.report_id,
            synthetic_data=True,
        )
        self.candidates[cid] = cand
        return cand

    def _GetStudy(self, req: optimizer_pb2.GetStudyRequest) -> Message:
        return optimizer_pb2.GetStudyResponse(study=self.studies[req.study_id])

    def _GetStudyDiagnostics(self, req: optimizer_pb2.GetStudyDiagnosticsRequest) -> Message:
        return optimizer_pb2.GetStudyDiagnosticsResponse(
            diagnostics=optimizer_pb2.StudyDiagnostics(study_id=req.study_id, plateau_estimate=3)
        )

    def _GetGateReport(self, req: optimizer_pb2.GetGateReportRequest) -> Message:
        g = optimizer_pb2.GateReport()
        g.CopyFrom(self.gates[req.candidate_id])
        if req.caller.actor == training_pb2.ACTOR_ANALYST:  # EX-035
            del g.reasons[:]
            g.thresholds.clear()
        return optimizer_pb2.GetGateReportResponse(report=g)

    def _GetWalkForwardBreakdown(self, req: Message) -> Message:
        return optimizer_pb2.GetWalkForwardBreakdownResponse()

    def _GetRegimeBreakdown(self, req: optimizer_pb2.GetRegimeBreakdownRequest) -> Message:
        return optimizer_pb2.GetRegimeBreakdownResponse(regimes=self.gates[req.candidate_id].regimes)

    def _GetCostSensitivity(self, req: optimizer_pb2.GetCostSensitivityRequest) -> Message:
        return optimizer_pb2.GetCostSensitivityResponse(sensitivity=self.gates[req.candidate_id].cost_sensitivity)

    def _ListCandidates(self, req: optimizer_pb2.ListCandidatesRequest) -> Message:
        return optimizer_pb2.ListCandidatesResponse(
            candidates=[c for c in self.candidates.values() if c.lineage_id == req.lineage_id]
        )

    def _CheckTransition(self, req: optimizer_pb2.CheckTransitionRequest) -> Message:
        return optimizer_pb2.CheckTransitionResponse(
            decision=self._permission(req.candidate_id, req.to_stage, req.caller)
        )

    def _permission(self, cid: str, to: int, caller: training_pb2.CallerContext) -> optimizer_pb2.PermissionDecision:
        c = self.candidates.get(cid)
        if c is None:
            return optimizer_pb2.PermissionDecision(allowed=False, reason="unknown candidate")
        lin = self._lin(c.lineage_id)
        lvl = lin.proto.autonomy_level
        analyst = caller.actor == training_pb2.ACTOR_ANALYST
        if not analyst:
            return optimizer_pb2.PermissionDecision(allowed=True)
        if to in (ml_pb2.REGISTRY_STAGE_LIVE_ELIGIBLE, ml_pb2.REGISTRY_STAGE_LIVE):
            return optimizer_pb2.PermissionDecision(
                allowed=False, reason="analyst may never promote to live_eligible or live"
            )
        if to == ml_pb2.REGISTRY_STAGE_SHADOW:
            ok = lvl >= 1 and c.stage == ml_pb2.REGISTRY_STAGE_VALIDATED
            return optimizer_pb2.PermissionDecision(
                allowed=ok, reason="" if ok else "needs level 1 and a validated candidate"
            )
        if to == ml_pb2.REGISTRY_STAGE_PAPER:
            if lvl < 2:
                return optimizer_pb2.PermissionDecision(allowed=False, reason="paper promotion needs level 2")
            if not lin.proto.champion_candidate_id:
                return optimizer_pb2.PermissionDecision(
                    allowed=False, reason="new spec needs owner approval (EX-052)", requires_step_up=True
                )
            if c.shadow_sessions < (lin.proto.promotion_limits.min_shadow_sessions or 10):
                return optimizer_pb2.PermissionDecision(
                    allowed=False, reason="minimum shadow period not reached (EX-033)"
                )
            if c.demoted:
                return optimizer_pb2.PermissionDecision(
                    allowed=False, reason="demoted candidates need the owner (EX-055)"
                )
            if lin.promotions >= (lin.proto.promotion_limits.max_promotions_per_window or 1):
                return optimizer_pb2.PermissionDecision(
                    allowed=False, reason="promotion limit for the window reached (EX-033)"
                )
            return optimizer_pb2.PermissionDecision(allowed=True)
        return optimizer_pb2.PermissionDecision(allowed=False, reason="transition not allowed for the analyst")

    def _PromoteCandidate(self, req: optimizer_pb2.PromoteCandidateRequest) -> Message:
        d = self._permission(req.candidate_id, req.to_stage, req.caller)
        c = self.candidates[req.candidate_id]
        if d.allowed:
            lin = self._lin(c.lineage_id)
            if req.to_stage == ml_pb2.REGISTRY_STAGE_PAPER:
                if lin.proto.champion_candidate_id in self.candidates:
                    self.candidates[lin.proto.champion_candidate_id].stage = ml_pb2.REGISTRY_STAGE_RETIRED
                lin.proto.champion_candidate_id = c.candidate_id
                lin.promotions += 1
            c.stage = req.to_stage
        else:
            self.denials.append(("PromoteCandidate", d.reason))
        return optimizer_pb2.PromoteCandidateResponse(candidate=c, decision=d)

    def _DeployChallengerShadow(self, req: optimizer_pb2.DeployChallengerShadowRequest) -> Message:
        d = self._permission(req.candidate_id, ml_pb2.REGISTRY_STAGE_SHADOW, req.caller)
        c = self.candidates[req.candidate_id]
        if d.allowed:
            c.stage = ml_pb2.REGISTRY_STAGE_SHADOW
        else:
            self.denials.append(("DeployChallengerShadow", d.reason))
        return optimizer_pb2.DeployChallengerShadowResponse(candidate=c, decision=d)

    def _NominateLiveEligible(self, req: optimizer_pb2.NominateLiveEligibleRequest) -> Message:
        lvl = self._lin(self.candidates[req.candidate_id].lineage_id).proto.autonomy_level
        ok = lvl >= 2
        return optimizer_pb2.NominateLiveEligibleResponse(
            decision=optimizer_pb2.PermissionDecision(allowed=ok, reason="" if ok else "level 2 only")
        )

    # ---- test helpers

    def add_shadow_sessions(self, n: int) -> None:
        for c in self.candidates.values():
            if c.stage == ml_pb2.REGISTRY_STAGE_SHADOW:
                c.shadow_sessions += n

    def struct(self, d: dict[str, Any]) -> struct_pb2.Struct:
        s = struct_pb2.Struct()
        s.update(d)
        return s
