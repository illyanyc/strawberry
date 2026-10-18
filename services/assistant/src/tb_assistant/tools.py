"""The analyst tool server (AN-030..AN-032, AN-056, AN-060).

The LLM never calls a platform API. It writes a plan; the deterministic orchestrator executes the
plan's actions and its own evidence reads through this server, which for every call:

1. refuses names that are not registered tools (including every permanently forbidden operation),
2. reads the lineage's autonomy level from the optimizer and refuses tools above it,
3. checks the experiment budget before any write (AN-020),
4. calls the section 10/11 service as the assistant (the server resolves actor ``analyst``),
5. drops every ``holdout*`` key from the result, sanitizes and length-limits it for the LLM,
6. audits the call and logs input/output hashes.

The optimizer enforces the same permission table again (EX-031); this server is the first wall,
not the only one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from google.protobuf.json_format import MessageToDict
from google.protobuf.message import Message

from tb.analyst.v1 import analyst_pb2
from tb.ml.v1 import ml_pb2, ml_service_pb2, training_pb2
from tb.optimizer.v1 import optimizer_pb2
from tb_assistant.audit import AuditRecord, AuditSink
from tb_assistant.budget import BudgetExceededError, BudgetGuard
from tb_assistant.research import ML, OPT, ResearchAPI, ResearchCallError

log = logging.getLogger("tb_assistant.tools")

# AN-032: operations no autonomy level may perform. None is a registered tool; a request for one is
# denied and audited as a red-team signal.
FORBIDDEN_TOOLS = frozenset(
    {
        "promote_live",
        "promote_strategy_live",
        "set_strategy_live",
        "promote_live_eligible",
        "promote_paper_new_spec",
        "request_holdout_eval",
        "evaluate_on_holdout",
        "get_holdout_results",
        "check_data_access_holdout",
        "move_holdout_boundary",
        "submit_order",
        "place_order",
        "cancel_order",
        "replace_order",
        "flatten",
        "engage_kill_switch",
        "release_kill_switch",
        "set_risk_limits",
        "raise_risk_limits",
        "set_allocation",
        "set_strategy_mode",
        "read_credentials",
        "get_broker_credentials",
        "get_scorer_config",
        "modify_scorer",
        "raise_autonomy",
        "set_budget",
        "open_cycle",
        "get_positions",
        "get_account",
    }
)


class ToolDeniedError(RuntimeError):
    def __init__(self, tool: str, reason: str) -> None:
        super().__init__(f"tool {tool!r} denied: {reason}")
        self.tool = tool
        self.reason = reason


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # read | draft | write | notify
    min_level: int
    description: str


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec("get_training_diagnostics", "read", 0, "DiagnosticsReport of a training run"),
        ToolSpec("get_run", "read", 0, "Training run record (config, out-of-sample metrics)"),
        ToolSpec("get_learning_curves", "read", 0, "Learning curves of a run"),
        ToolSpec("get_feature_importance", "read", 0, "Feature importance and stability of a run"),
        ToolSpec("get_error_slices", "read", 0, "Error slices of a run"),
        ToolSpec("get_dataset_profile", "read", 0, "Profile of a dataset snapshot"),
        ToolSpec("get_backtest_report", "read", 0, "Walk-forward, regime, and cost breakdowns of a candidate"),
        ToolSpec("get_study", "read", 0, "Study record (outer estimate, PBO, DSR)"),
        ToolSpec("get_study_diagnostics", "read", 0, "Study convergence and parameter importance"),
        ToolSpec("get_gate_report", "read", 0, "Gate pass/fail and DSR of a candidate (analyst view)"),
        ToolSpec("get_trial_ledger", "read", 0, "Lineage trial ledger summary and cycles"),
        ToolSpec("get_cumulative_overfitting_stats", "read", 0, "Lineage-level DSR, effective N, PBO"),
        ToolSpec("get_live_feedback", "read", 0, "Latest LiveFeedbackReport of a strategy"),
        ToolSpec("get_lineage", "read", 0, "Lineage settings (level, budgets)"),
        ToolSpec("list_candidates", "read", 0, "Candidates of a lineage"),
        ToolSpec("list_model_families", "read", 0, "Model families with hyperparameter schemas"),
        ToolSpec("list_feature_sets", "read", 0, "Feature sets"),
        ToolSpec("list_label_specs", "read", 0, "Label presets"),
        ToolSpec("draft_training_config", "draft", 0, "Validate a training config diff and estimate its cost"),
        ToolSpec("draft_study_spec", "draft", 0, "Validate a study spec diff against the family schemas"),
        ToolSpec("annotate_run", "write", 0, "Attach a note to a run (stored as untrusted text)"),
        ToolSpec("write_journal_entry", "write", 0, "Append to the research journal"),
        ToolSpec("launch_training", "write", 1, "Submit a training job (ledgered as an analyst trial)"),
        ToolSpec("launch_ablation", "write", 1, "Submit one-factor ablations of a run"),
        ToolSpec("launch_study", "write", 1, "Create and start a pre-registered Optuna study"),
        ToolSpec("deploy_challenger_shadow", "write", 1, "Deploy a validated challenger to shadow"),
        ToolSpec("promote_paper_champion", "write", 2, "Promote a gated challenger to paper champion (limits)"),
        ToolSpec("nominate_live_eligible", "notify", 2, "Nominate a paper champion for owner review"),
    )
}
WRITE_KINDS = frozenset({"write", "notify"})
OWNER_APPROVABLE = frozenset({"launch_training", "launch_ablation", "launch_study", "deploy_challenger_shadow"})
LEVEL_FREE_WRITES = frozenset({"annotate_run", "write_journal_entry"})


@dataclass
class ToolContext:
    lineage_id: str
    round_id: str
    correlation_id: str
    budget: analyst_pb2.Budget
    paused: bool = False


@dataclass
class ToolResult:
    tool: str
    message: Message | None
    report_id: str
    safe: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRecord:
    tool: str
    lineage_id: str
    round_id: str
    level: int
    outcome: str  # ok | denied | budget | error
    input_sha256: str
    output_sha256: str
    detail: str
    created_at: datetime


class ToolCallLog(Protocol):
    async def record_tool_call(self, rec: ToolCallRecord) -> None: ...


class FeedbackStore(Protocol):
    def latest_feedback(self, strategy_id: str) -> Message | None: ...


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def strip_holdout(obj: Any) -> Any:
    """AN-030: drop every key mentioning the holdout, at any depth."""
    if isinstance(obj, dict):
        return {k: strip_holdout(v) for k, v in obj.items() if "holdout" not in str(k).lower()}
    if isinstance(obj, list):
        return [strip_holdout(v) for v in obj]
    return obj


def _clean_str(s: str) -> str:
    return "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


def sanitize(obj: Any, max_str: int = 2000) -> Any:
    if isinstance(obj, dict):
        return {_clean_str(str(k))[:200]: sanitize(v, max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, max_str) for v in obj]
    if isinstance(obj, str):
        return _clean_str(obj)[:max_str]
    return obj


def limit_size(obj: Any, max_bytes: int) -> Any:
    """AN-056: shrink the longest lists until the JSON fits, then mark the result truncated."""
    data = obj
    for _ in range(40):
        if len(json.dumps(data, default=str).encode()) <= max_bytes:
            return data
        data = _halve_longest(data)
        if isinstance(data, dict):
            data["_truncated"] = True
    text = json.dumps(data, default=str)[: max_bytes - 64]
    return {"_truncated": True, "excerpt": text}


def _halve_longest(obj: Any) -> Any:
    best: tuple[int, list[Any] | None] = (0, None)

    def walk(o: Any) -> None:
        nonlocal best
        if isinstance(o, list):
            if len(o) > best[0]:
                best = (len(o), o)
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for x in o.values():
                walk(x)

    walk(obj)
    if best[1] is not None and best[0] > 1:
        del best[1][max(1, best[0] // 2) :]
    elif isinstance(obj, dict) and obj:
        # No list left to cut: drop the largest value.
        k = max(obj, key=lambda key: len(json.dumps(obj[key], default=str)))
        obj = {kk: vv for kk, vv in obj.items() if kk != k}
    return obj


def to_safe(msg: Message | None, max_bytes: int) -> dict[str, Any]:
    if msg is None:
        return {}
    d = MessageToDict(msg, preserving_proto_field_name=True)
    out = limit_size(sanitize(strip_holdout(d)), max_bytes)
    return out if isinstance(out, dict) else {"value": out}


def caller(ctx: ToolContext, reason: str = "") -> training_pb2.CallerContext:
    return training_pb2.CallerContext(
        actor=training_pb2.ACTOR_ANALYST,
        actor_id="analyst",
        analyst_round_id=ctx.round_id,
        reason=reason[:200],
        correlation_id=ctx.correlation_id,
    )


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResult]]


class ToolServer:
    def __init__(
        self,
        api: ResearchAPI,
        *,
        audit: AuditSink,
        budget: BudgetGuard,
        call_log: ToolCallLog | None = None,
        feedback: FeedbackStore | None = None,
        journal_writer: Callable[[str, str, str], Awaitable[None]] | None = None,
        max_result_bytes: int = 16384,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self.api = api
        self.audit = audit
        self.budget = budget
        self.call_log = call_log
        self.feedback = feedback
        self.journal_writer = journal_writer
        self.max_result_bytes = max_result_bytes
        self.clock = clock
        self._handlers: dict[str, Handler] = {name: getattr(self, "_t_" + name) for name in TOOLS}

    # ---- level and listing (AN-060)

    async def lineage(self, lineage_id: str) -> optimizer_pb2.Lineage:
        resp = await self.api.call(OPT, "GetLineage", optimizer_pb2.GetLineageRequest(lineage_id=lineage_id))
        assert isinstance(resp, optimizer_pb2.GetLineageResponse)
        return resp.lineage

    async def level(self, lineage_id: str) -> int:
        try:
            return int((await self.lineage(lineage_id)).autonomy_level)
        except ResearchCallError:
            # Unknown lineage or optimizer unreachable: fail closed to suggest-only.
            return 0

    @staticmethod
    def tools_for_level(level: int) -> list[ToolSpec]:
        return [t for t in TOOLS.values() if t.min_level <= level]

    @staticmethod
    def action_tools_for_level(level: int) -> list[str]:
        names = [t.name for t in TOOLS.values() if t.kind in WRITE_KINDS and t.min_level <= level]
        return [n for n in names if n not in LEVEL_FREE_WRITES]

    # ---- the single entry point

    async def call(
        self,
        ctx: ToolContext,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        cost: tuple[int, float] = (0, 0.0),
        approved: bool = False,
    ) -> ToolResult:
        """``approved`` marks an action the owner approved through api-gateway: level-1 launch tools
        may then run at level 0 (level 0 means "every launch needs owner approval"). It never lowers
        the level of promotion or nomination tools."""
        args = args or {}
        in_hash = _sha({"tool": name, "args": args})
        spec = TOOLS.get(name)
        if spec is None:
            reason = "permanently forbidden for the analyst" if name in FORBIDDEN_TOOLS else "no such tool"
            await self._record(ctx, name, -1, "denied", in_hash, "", reason)
            raise ToolDeniedError(name, reason)
        level = await self.level(ctx.lineage_id)
        needed = 0 if approved and name in OWNER_APPROVABLE else spec.min_level
        if needed > level:
            reason = f"needs autonomy level {spec.min_level}, lineage is at {level}"
            await self._record(ctx, name, level, "denied", in_hash, "", reason)
            raise ToolDeniedError(name, reason)
        if spec.kind in WRITE_KINDS and name not in LEVEL_FREE_WRITES:
            if ctx.paused:
                await self._record(ctx, name, level, "denied", in_hash, "", "loop paused")
                raise ToolDeniedError(name, "loop paused")
            trials, cpu = cost
            if trials or cpu:
                try:
                    self.budget.check(ctx.lineage_id, ctx.budget, trials=trials, cpu_hours=cpu)
                except BudgetExceededError as exc:
                    await self._record(ctx, name, level, "budget", in_hash, "", exc.reason)
                    raise
        try:
            result = await self._handlers[name](ctx, args)
        except ResearchCallError as exc:
            outcome = "denied" if exc.code == "PERMISSION_DENIED" else "error"
            await self._record(ctx, name, level, outcome, in_hash, "", str(exc))
            if outcome == "denied":
                raise ToolDeniedError(name, f"refused by the service: {exc.detail}") from None
            raise
        await self._record(ctx, name, level, "ok", in_hash, _sha(result.safe), "")
        return result

    async def _record(
        self, ctx: ToolContext, name: str, level: int, outcome: str, in_hash: str, out_hash: str, detail: str
    ) -> None:
        rec = ToolCallRecord(
            name, ctx.lineage_id, ctx.round_id, level, outcome, in_hash, out_hash, detail[:500], self.clock()
        )
        action = f"analyst.tool.{name}" if outcome == "ok" else f"analyst.tool.{outcome}"
        await self.audit.append(
            AuditRecord(
                actor="analyst",
                action=action,
                target=ctx.lineage_id,
                details={
                    "tool": name,
                    "round_id": ctx.round_id,
                    "level": level,
                    "outcome": outcome,
                    "input_sha256": in_hash,
                    "output_sha256": out_hash,
                    "detail": detail[:300],
                },
                correlation_id=ctx.correlation_id,
            )
        )
        if outcome != "ok":
            log.warning(
                "analyst tool call refused",
                extra={
                    "tool": name,
                    "outcome": outcome,
                    "detail": detail[:200],
                    "lineage_id": ctx.lineage_id,
                    "round_id": ctx.round_id,
                },
            )
        if self.call_log is not None:
            await self.call_log.record_tool_call(rec)

    def _res(self, name: str, msg: Message | None, report_id: str, **extra: Any) -> ToolResult:
        return ToolResult(name, msg, report_id, to_safe(msg, self.max_result_bytes), dict(extra))

    async def _ml(self, method: str, req: Message) -> Message:
        return await self.api.call(ML, method, req)

    async def _opt(self, method: str, req: Message) -> Message:
        return await self.api.call(OPT, method, req)

    # ---- read tools

    async def _t_get_training_diagnostics(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._ml(
            "GetDiagnostics", ml_service_pb2.GetDiagnosticsRequest(run_id=a["run_id"], caller=caller(ctx))
        )
        assert isinstance(r, ml_service_pb2.GetDiagnosticsResponse)
        return self._res("get_training_diagnostics", r.report, r.report.report_id or a["run_id"])

    async def _t_get_run(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._ml("GetRun", ml_service_pb2.GetRunRequest(run_id=a["run_id"]))
        assert isinstance(r, ml_service_pb2.GetRunResponse)
        return self._res("get_run", r.run, a["run_id"])

    async def _t_get_learning_curves(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._ml(
            "GetLearningCurves", ml_service_pb2.GetLearningCurvesRequest(run_id=a["run_id"], caller=caller(ctx))
        )
        assert isinstance(r, ml_service_pb2.GetLearningCurvesResponse)
        return self._res("get_learning_curves", r.curves, a["run_id"] + "#curves")

    async def _t_get_feature_importance(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = ml_service_pb2.GetFeatureImportanceRequest(
            run_id=a["run_id"], method=a.get("method", "gain"), caller=caller(ctx)
        )
        r = await self._ml("GetFeatureImportance", req)
        assert isinstance(r, ml_service_pb2.GetFeatureImportanceResponse)
        return self._res("get_feature_importance", r.importance, a["run_id"] + "#importance")

    async def _t_get_error_slices(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = ml_service_pb2.GetErrorSlicesRequest(
            run_id=a["run_id"], slice_by=a.get("slice_by", ""), caller=caller(ctx)
        )
        r = await self._ml("GetErrorSlices", req)
        assert isinstance(r, ml_service_pb2.GetErrorSlicesResponse)
        return self._res("get_error_slices", r, a["run_id"] + "#slices")

    async def _t_get_dataset_profile(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = ml_service_pb2.GetDatasetProfileRequest(snapshot_id=a["snapshot_id"], caller=caller(ctx))
        r = await self._ml("GetDatasetProfile", req)
        assert isinstance(r, ml_service_pb2.GetDatasetProfileResponse)
        return self._res("get_dataset_profile", r.profile, a["snapshot_id"])

    async def _t_get_backtest_report(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        cid = a["candidate_id"]
        wf = await self._opt(
            "GetWalkForwardBreakdown",
            optimizer_pb2.GetWalkForwardBreakdownRequest(candidate_id=cid, caller=caller(ctx)),
        )
        rg = await self._opt(
            "GetRegimeBreakdown", optimizer_pb2.GetRegimeBreakdownRequest(candidate_id=cid, caller=caller(ctx))
        )
        cs = await self._opt(
            "GetCostSensitivity", optimizer_pb2.GetCostSensitivityRequest(candidate_id=cid, caller=caller(ctx))
        )
        assert isinstance(wf, optimizer_pb2.GetWalkForwardBreakdownResponse)
        assert isinstance(rg, optimizer_pb2.GetRegimeBreakdownResponse)
        assert isinstance(cs, optimizer_pb2.GetCostSensitivityResponse)
        rep = optimizer_pb2.GateReport(
            candidate_id=cid, walk_forward=wf.folds, regimes=rg.regimes, cost_sensitivity=cs.sensitivity
        )
        return self._res(
            "get_backtest_report",
            rep,
            cid + "#backtest",
            folds=list(wf.folds),
            regimes=list(rg.regimes),
            cost=cs.sensitivity,
        )

    async def _t_get_study(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._opt("GetStudy", optimizer_pb2.GetStudyRequest(study_id=a["study_id"]))
        assert isinstance(r, optimizer_pb2.GetStudyResponse)
        return self._res("get_study", r.study, a["study_id"])

    async def _t_get_study_diagnostics(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._opt(
            "GetStudyDiagnostics", optimizer_pb2.GetStudyDiagnosticsRequest(study_id=a["study_id"], caller=caller(ctx))
        )
        assert isinstance(r, optimizer_pb2.GetStudyDiagnosticsResponse)
        return self._res("get_study_diagnostics", r.diagnostics, a["study_id"] + "#diagnostics")

    async def _t_get_gate_report(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._opt(
            "GetGateReport", optimizer_pb2.GetGateReportRequest(candidate_id=a["candidate_id"], caller=caller(ctx))
        )
        assert isinstance(r, optimizer_pb2.GetGateReportResponse)
        rep = optimizer_pb2.GateReport()
        rep.CopyFrom(r.report)
        # AN-031 / EX-035: never pass reasons or thresholds on, even if a server forgot to clear them.
        del rep.reasons[:]
        rep.thresholds.clear()
        return self._res("get_gate_report", rep, rep.report_id or a["candidate_id"])

    async def _t_get_trial_ledger(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._opt(
            "GetTrialLedger", optimizer_pb2.GetTrialLedgerRequest(lineage_id=ctx.lineage_id, caller=caller(ctx))
        )
        assert isinstance(r, optimizer_pb2.GetTrialLedgerResponse)
        led = optimizer_pb2.TrialLedger()
        led.CopyFrom(r.ledger)
        led.holdout_evaluations = 0
        return self._res("get_trial_ledger", led, f"ledger:{ctx.lineage_id}")

    async def _t_get_cumulative_overfitting_stats(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = optimizer_pb2.GetCumulativeOverfittingStatsRequest(lineage_id=ctx.lineage_id, caller=caller(ctx))
        r = await self._opt("GetCumulativeOverfittingStats", req)
        assert isinstance(r, optimizer_pb2.GetCumulativeOverfittingStatsResponse)
        rid = f"stats:{ctx.lineage_id}:{r.stats.n_trials}"
        return self._res("get_cumulative_overfitting_stats", r.stats, rid)

    async def _t_get_live_feedback(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        msg = self.feedback.latest_feedback(a["strategy_id"]) if self.feedback else None
        rid = getattr(msg, "report_id", "") if msg is not None else ""
        return self._res("get_live_feedback", msg, rid)

    async def _t_get_lineage(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        lin = optimizer_pb2.Lineage()
        lin.CopyFrom(await self.lineage(ctx.lineage_id))
        lin.holdout_boundary = 0
        return self._res("get_lineage", lin, f"lineage:{ctx.lineage_id}")

    async def _t_list_candidates(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._opt("ListCandidates", optimizer_pb2.ListCandidatesRequest(lineage_id=ctx.lineage_id))
        assert isinstance(r, optimizer_pb2.ListCandidatesResponse)
        return self._res("list_candidates", r, f"candidates:{ctx.lineage_id}")

    async def _t_list_model_families(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._ml("ListModelFamilies", ml_service_pb2.ListModelFamiliesRequest())
        assert isinstance(r, ml_service_pb2.ListModelFamiliesResponse)
        return self._res("list_model_families", r, "families")

    async def _t_list_feature_sets(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._ml("ListFeatureSets", ml_service_pb2.ListFeatureSetsRequest())
        return self._res("list_feature_sets", r, "feature_sets")

    async def _t_list_label_specs(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        r = await self._ml("ListLabelPresets", ml_service_pb2.ListLabelPresetsRequest())
        return self._res("list_label_specs", r, "label_presets")

    # ---- draft tools

    async def _t_draft_training_config(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        cfg: training_pb2.TrainingConfig = a["config"]
        r = await self._ml(
            "EstimateTrainingJob", ml_service_pb2.EstimateTrainingJobRequest(config=cfg, caller=caller(ctx))
        )
        assert isinstance(r, ml_service_pb2.EstimateTrainingJobResponse)
        return self._res("draft_training_config", r, "", errors=list(r.errors), estimate=r.estimate)

    async def _t_draft_study_spec(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        spec: optimizer_pb2.StudySpec = a["spec"]
        return self._res("draft_study_spec", spec, "", errors=list(a.get("errors", [])))

    async def _t_annotate_run(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        await self._ml(
            "AnnotateRun",
            ml_service_pb2.AnnotateRunRequest(run_id=a["run_id"], note=str(a["note"])[:2000], caller=caller(ctx)),
        )
        return self._res("annotate_run", None, a["run_id"])

    async def _t_write_journal_entry(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        if self.journal_writer:
            await self.journal_writer(ctx.lineage_id, ctx.round_id, str(a.get("text", ""))[:4000])
        return self._res("write_journal_entry", None, ctx.round_id)

    # ---- write tools (level >= 1)

    async def _t_launch_training(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = ml_service_pb2.SubmitTrainingJobRequest(config=a["config"], caller=caller(ctx, a.get("reason", "")))
        r = await self._ml("SubmitTrainingJob", req)
        assert isinstance(r, ml_service_pb2.SubmitTrainingJobResponse)
        return self._res("launch_training", r.job, r.job.job_id, job_ids=[r.job.job_id])

    async def _t_launch_ablation(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = ml_service_pb2.SubmitAblationRequest(
            run_id=a["run_id"], variants=a["variants"], caller=caller(ctx, a.get("reason", ""))
        )
        r = await self._ml("SubmitAblation", req)
        assert isinstance(r, ml_service_pb2.SubmitAblationResponse)
        ids = [j.job_id for j in r.jobs]
        return self._res("launch_ablation", r, ",".join(ids), job_ids=ids)

    async def _t_launch_study(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        c = await self._opt(
            "CreateStudy", optimizer_pb2.CreateStudyRequest(spec=a["spec"], caller=caller(ctx, a.get("reason", "")))
        )
        assert isinstance(c, optimizer_pb2.CreateStudyResponse)
        s = await self._opt(
            "StartStudy",
            optimizer_pb2.StartStudyRequest(study_id=c.study.study_id, budget=a["spec"].budget, caller=caller(ctx)),
        )
        assert isinstance(s, optimizer_pb2.StartStudyResponse)
        return self._res("launch_study", s.study, s.study.study_id, job_ids=[s.study.study_id])

    async def _t_deploy_challenger_shadow(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = optimizer_pb2.DeployChallengerShadowRequest(candidate_id=a["candidate_id"], caller=caller(ctx))
        r = await self._opt("DeployChallengerShadow", req)
        assert isinstance(r, optimizer_pb2.DeployChallengerShadowResponse)
        if not r.decision.allowed:
            raise ResearchCallError(OPT, "DeployChallengerShadow", "PERMISSION_DENIED", r.decision.reason)
        return self._res("deploy_challenger_shadow", r.candidate, a["candidate_id"])

    async def _t_promote_paper_champion(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        chk = await self._opt(
            "CheckTransition",
            optimizer_pb2.CheckTransitionRequest(
                candidate_id=a["candidate_id"], to_stage=ml_pb2.REGISTRY_STAGE_PAPER, caller=caller(ctx)
            ),
        )
        assert isinstance(chk, optimizer_pb2.CheckTransitionResponse)
        if not chk.decision.allowed:
            raise ResearchCallError(OPT, "CheckTransition", "PERMISSION_DENIED", chk.decision.reason)
        r = await self._opt(
            "PromoteCandidate",
            optimizer_pb2.PromoteCandidateRequest(
                candidate_id=a["candidate_id"], to_stage=ml_pb2.REGISTRY_STAGE_PAPER, caller=caller(ctx)
            ),
        )
        assert isinstance(r, optimizer_pb2.PromoteCandidateResponse)
        if not r.decision.allowed:
            raise ResearchCallError(OPT, "PromoteCandidate", "PERMISSION_DENIED", r.decision.reason)
        return self._res("promote_paper_champion", r.candidate, a["candidate_id"])

    async def _t_nominate_live_eligible(self, ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
        req = optimizer_pb2.NominateLiveEligibleRequest(
            candidate_id=a["candidate_id"], rationale=str(a.get("rationale", ""))[:1000], caller=caller(ctx)
        )
        r = await self._opt("NominateLiveEligible", req)
        assert isinstance(r, optimizer_pb2.NominateLiveEligibleResponse)
        return self._res("nominate_live_eligible", r.decision, a["candidate_id"])
