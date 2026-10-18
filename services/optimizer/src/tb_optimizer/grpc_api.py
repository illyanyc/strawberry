"""``OptimizerService`` and ``HoldoutService`` over gRPC (ADR-0035): actor from the service token (EX-030),
per-method allowlists (the assistant never reaches the holdout, EX-012), errors mapped to status codes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, TypeVar

import grpc

from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import holdout_pb2 as hp
from tb.optimizer.v1 import optimizer_pb2 as op
from tb_optimizer.core import NotFound, OptimizerCore
from tbcore.audit import AuditSink
from tbcore.ml import caller as callers
from tbcore.ml.caller import PermissionDenied
from tbcore.rpc import current_caller

OPT = op.DESCRIPTOR.services_by_name["OptimizerService"]
HOLD = hp.DESCRIPTOR.services_by_name["HoldoutService"]
GATEWAY, ASSISTANT, ML, OPTIMIZER, MONITOR = "api-gateway", "assistant", "ml", "optimizer", "monitor"
ANALYST_TOOLS = [
    "GetLineage",
    "ListLineages",
    "SetLineageAutonomy",
    "CreateStudy",
    "StartStudy",
    "PauseStudy",
    "ResumeStudy",
    "StopStudy",
    "GetStudy",
    "ListStudies",
    "StreamStudyProgress",
    "ListTrials",
    "GetStudyDiagnostics",
    "GetTrialLedger",
    "GetCumulativeOverfittingStats",
    "GetGateReport",
    "GetWalkForwardBreakdown",
    "GetRegimeBreakdown",
    "GetCostSensitivity",
    "ListCandidates",
    "GetCandidate",
    "CheckTransition",
    "PromoteCandidate",
    "DeployChallengerShadow",
    "NominateLiveEligible",
    "GetLineageGraph",
]
OPT_ALLOW: dict[str, list[str]] = {m.name: [GATEWAY] for m in OPT.methods}
for _m in ANALYST_TOOLS:
    OPT_ALLOW[_m] = [GATEWAY, ASSISTANT]
OPT_ALLOW["GetLineage"] = [GATEWAY, ASSISTANT, ML]
OPT_ALLOW["RecordTrial"] = [ML, OPTIMIZER]
OPT_ALLOW["DemoteCandidate"] = [GATEWAY, MONITOR]
OPT_ALLOW["GetGraduationStatistics"] = [GATEWAY, "execution"]
HOLD_ALLOW: dict[str, list[str]] = {m.name: [GATEWAY] for m in HOLD.methods}
HOLD_ALLOW["CheckDataAccess"] = [GATEWAY, ML, OPTIMIZER]

R = TypeVar("R")


class _Base:
    def __init__(self, core: OptimizerCore, audit: AuditSink) -> None:
        self.core = core
        self.audit = audit

    def _caller(self, ctx: tp.CallerContext | None) -> tp.CallerContext:
        return callers.resolve(ctx, current_caller())

    def _do(self, context: grpc.ServicerContext, method: str, caller: tp.CallerContext, fn: Callable[[], R]) -> R:
        try:
            return fn()
        except NotFound as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"not found: {exc}")
        except PermissionDenied as exc:
            self.audit.append(callers.name(caller), "optimizer.denied", method, {"reason": str(exc)})
            context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except ValueError as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        raise AssertionError("unreachable")


class OptimizerServicer(_Base):
    def CreateLineage(self, req: op.CreateLineageRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        return op.CreateLineageResponse(
            lineage=self._do(ctx, "CreateLineage", c, lambda: self.core.create_lineage(req.lineage, c))
        )

    def GetLineage(self, req: op.GetLineageRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        lin = self._do(ctx, "GetLineage", c, lambda: self.core._lineage(req.lineage_id))
        return op.GetLineageResponse(lineage=lin)

    def ListLineages(self, req: op.ListLineagesRequest, ctx: grpc.ServicerContext) -> Any:
        return op.ListLineagesResponse(lineages=self.core.lineages())

    def SetLineageAutonomy(self, req: op.SetLineageAutonomyRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        budget = req.analyst_budget if req.HasField("analyst_budget") else None
        limits = req.promotion_limits if req.HasField("promotion_limits") else None
        lin = self._do(
            ctx, "SetLineageAutonomy", c, lambda: self.core.set_autonomy(req.lineage_id, req.level, budget, limits, c)
        )
        return op.SetLineageAutonomyResponse(lineage=lin)

    def OpenCycle(self, req: op.OpenCycleRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        cyc = self._do(ctx, "OpenCycle", c, lambda: self.core.open_cycle(req.lineage_id, req.target_sharpe, c))
        return op.OpenCycleResponse(cycle=cyc)

    def GetDeploymentLimits(self, req: op.GetDeploymentLimitsRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        eff, prof = self._do(ctx, "GetDeploymentLimits", c, lambda: self.core.deployment_limits(req.lineage_id))
        return op.GetDeploymentLimitsResponse(limits=eff, profile=prof)

    def GetGraduationStatistics(self, req: op.GetGraduationStatisticsRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        if c.actor == tp.ACTOR_ANALYST:
            self.audit.append(callers.name(c), "optimizer.denied", "GetGraduationStatistics", {"reason": "analyst"})
            ctx.abort(grpc.StatusCode.PERMISSION_DENIED, "the analyst cannot read graduation statistics")
        st = self._do(
            ctx,
            "GetGraduationStatistics",
            c,
            lambda: self.core.graduation_statistics(req.strategy_id, list(req.paper_session_returns), req.horizon),
        )
        return op.GetGraduationStatisticsResponse(statistics=st)

    def SetDeploymentLimits(self, req: op.SetDeploymentLimitsRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        lim, up = self._do(
            ctx,
            "SetDeploymentLimits",
            c,
            lambda: self.core.set_deployment_limits(req.lineage_id, req.limits, req.allocation, c),
        )
        return op.SetDeploymentLimitsResponse(limits=lim, raised=up)

    def CreateStudy(self, req: op.CreateStudyRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        return op.CreateStudyResponse(
            study=self._do(ctx, "CreateStudy", c, lambda: self.core.create_study(req.spec, c))
        )

    def StartStudy(self, req: op.StartStudyRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        b = req.budget if req.HasField("budget") else None
        return op.StartStudyResponse(
            study=self._do(ctx, "StartStudy", c, lambda: self.core.start_study(req.study_id, b, c))
        )

    def _control(
        self, ctx: grpc.ServicerContext, study_id: str, caller: tp.CallerContext | None, action: str
    ) -> op.Study:
        c = self._caller(caller)
        return self._do(ctx, action, c, lambda: self.core.control(study_id, action, c))

    def PauseStudy(self, req: op.PauseStudyRequest, ctx: grpc.ServicerContext) -> Any:
        return op.PauseStudyResponse(study=self._control(ctx, req.study_id, req.caller, "pause"))

    def ResumeStudy(self, req: op.ResumeStudyRequest, ctx: grpc.ServicerContext) -> Any:
        return op.ResumeStudyResponse(study=self._control(ctx, req.study_id, req.caller, "resume"))

    def StopStudy(self, req: op.StopStudyRequest, ctx: grpc.ServicerContext) -> Any:
        return op.StopStudyResponse(study=self._control(ctx, req.study_id, req.caller, "stop"))

    def GetStudy(self, req: op.GetStudyRequest, ctx: grpc.ServicerContext) -> Any:
        return op.GetStudyResponse(
            study=self._do(ctx, "GetStudy", self._caller(None), lambda: self.core.study(req.study_id))
        )

    def ListStudies(self, req: op.ListStudiesRequest, ctx: grpc.ServicerContext) -> Any:
        return op.ListStudiesResponse(studies=self.core.studies(req.lineage_id, req.limit))

    def StreamStudyProgress(self, req: op.StreamStudyProgressRequest, ctx: grpc.ServicerContext) -> Iterator[Any]:
        try:
            for p in self.core.stream(req.study_id, req.after_seq):
                if not ctx.is_active():
                    return
                yield op.StreamStudyProgressResponse(progress=p)
        except NotFound:
            ctx.abort(grpc.StatusCode.NOT_FOUND, f"study {req.study_id} is not running here")

    def ListTrials(self, req: op.ListTrialsRequest, ctx: grpc.ServicerContext) -> Any:
        t = self.core.trials(lineage_id=req.lineage_id or None, study_id=req.study_id or None)
        return op.ListTrialsResponse(trials=t[-req.limit :] if req.limit else t)

    def RecordTrial(self, req: op.RecordTrialRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        return op.RecordTrialResponse(
            trial=self._do(ctx, "RecordTrial", c, lambda: self.core.record_trial(req.trial, c))
        )

    def GetStudyDiagnostics(self, req: op.GetStudyDiagnosticsRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        d = self._do(ctx, "GetStudyDiagnostics", c, lambda: self.core.study_diagnostics(req.study_id))
        return op.GetStudyDiagnosticsResponse(diagnostics=d)

    def GetTrialLedger(self, req: op.GetTrialLedgerRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        return op.GetTrialLedgerResponse(
            ledger=self._do(ctx, "GetTrialLedger", c, lambda: self.core.ledger(req.lineage_id))
        )

    def GetCumulativeOverfittingStats(
        self, req: op.GetCumulativeOverfittingStatsRequest, ctx: grpc.ServicerContext
    ) -> Any:
        c = self._caller(req.caller)
        s = self._do(ctx, "GetCumulativeOverfittingStats", c, lambda: self.core.cumulative(req.lineage_id))
        return op.GetCumulativeOverfittingStatsResponse(stats=s)

    def GetGateReport(self, req: op.GetGateReportRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        r = self._do(ctx, "GetGateReport", c, lambda: self.core.gate_report(req.candidate_id, c))
        return op.GetGateReportResponse(report=r)

    def GetWalkForwardBreakdown(self, req: op.GetWalkForwardBreakdownRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        r = self._do(ctx, "GetWalkForwardBreakdown", c, lambda: self.core.gate_report(req.candidate_id, c))
        return op.GetWalkForwardBreakdownResponse(folds=r.walk_forward)

    def GetRegimeBreakdown(self, req: op.GetRegimeBreakdownRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        r = self._do(ctx, "GetRegimeBreakdown", c, lambda: self.core.gate_report(req.candidate_id, c))
        return op.GetRegimeBreakdownResponse(regimes=r.regimes)

    def GetCostSensitivity(self, req: op.GetCostSensitivityRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        r = self._do(ctx, "GetCostSensitivity", c, lambda: self.core.gate_report(req.candidate_id, c))
        return op.GetCostSensitivityResponse(sensitivity=r.cost_sensitivity)

    def ListCandidates(self, req: op.ListCandidatesRequest, ctx: grpc.ServicerContext) -> Any:
        return op.ListCandidatesResponse(candidates=self.core.candidates(req.lineage_id, req.stage))

    def GetCandidate(self, req: op.GetCandidateRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        return op.GetCandidateResponse(
            candidate=self._do(ctx, "GetCandidate", c, lambda: self.core.candidate(req.candidate_id))
        )

    def CheckTransition(self, req: op.CheckTransitionRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        d = self._do(ctx, "CheckTransition", c, lambda: self.core.check(req.candidate_id, req.to_stage, c))
        return op.CheckTransitionResponse(decision=d)

    def PromoteCandidate(self, req: op.PromoteCandidateRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        cand, d = self._do(ctx, "PromoteCandidate", c, lambda: self.core.promote(req.candidate_id, req.to_stage, c))
        return op.PromoteCandidateResponse(candidate=cand, decision=d)

    def DeployChallengerShadow(self, req: op.DeployChallengerShadowRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        cand, d = self._do(ctx, "DeployChallengerShadow", c, lambda: self.core.deploy_shadow(req.candidate_id, c))
        return op.DeployChallengerShadowResponse(candidate=cand, decision=d)

    def NominateLiveEligible(self, req: op.NominateLiveEligibleRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        d = self._do(ctx, "NominateLiveEligible", c, lambda: self.core.nominate(req.candidate_id, req.rationale, c))
        return op.NominateLiveEligibleResponse(decision=d)

    def DemoteCandidate(self, req: op.DemoteCandidateRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        cand = self._do(
            ctx, "DemoteCandidate", c, lambda: self.core.demote(req.candidate_id, req.to_stage, req.reason, c)
        )
        return op.DemoteCandidateResponse(candidate=cand)

    def GetEvidencePack(self, req: op.GetEvidencePackRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        return op.GetEvidencePackResponse(
            pack=self._do(ctx, "GetEvidencePack", c, lambda: self.core.evidence_pack(req.candidate_id))
        )

    def GetLineageGraph(self, req: op.GetLineageGraphRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        g = self._do(ctx, "GetLineageGraph", c, lambda: self.core.lineage_graph(req.candidate_id))
        return op.GetLineageGraphResponse(graph=g)

    def SetRetrainingPolicy(self, req: op.SetRetrainingPolicyRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        p = self._do(ctx, "SetRetrainingPolicy", c, lambda: self.core.set_policy(req.policy, c))
        return op.SetRetrainingPolicyResponse(policy=p)

    def TriggerRetrain(self, req: op.TriggerRetrainRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        st = self._do(ctx, "TriggerRetrain", c, lambda: self.core.trigger_retrain(req.lineage_id, req.reason, c))
        return op.TriggerRetrainResponse(study=st)


class HoldoutServicer(_Base):
    def CheckDataAccess(self, req: hp.CheckDataAccessRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        ok, why, b = self.core.holdout.check_data(req.lineage_id, req.period, c)
        return hp.CheckDataAccessResponse(allowed=ok, reason=why, boundary=b)

    def EvaluateOnHoldout(self, req: hp.EvaluateOnHoldoutRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        r = self._do(ctx, "EvaluateOnHoldout", c, lambda: self.core.holdout.evaluate(req.candidate_id, req.reason, c))
        return hp.EvaluateOnHoldoutResponse(result=r)

    def GetHoldoutStatus(self, req: hp.GetHoldoutStatusRequest, ctx: grpc.ServicerContext) -> Any:
        return hp.GetHoldoutStatusResponse(status=self.core.holdout.status(req.lineage_id))

    def SetHoldoutBoundary(self, req: hp.SetHoldoutBoundaryRequest, ctx: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        s = self._do(
            ctx, "SetHoldoutBoundary", c, lambda: self.core.holdout.set_boundary(req.lineage_id, req.boundary, c)
        )
        return hp.SetHoldoutBoundaryResponse(status=s)
