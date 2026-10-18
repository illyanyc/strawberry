"""``tb.ml.v1.MLService`` over gRPC (ADR-0035): actor from the service token (EX-030), errors mapped to
status codes, denials audited. The per-method allowlist keeps the assistant to the analyst tools."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, TypeVar

import grpc

from tb.ml.v1 import ml_service_pb2 as ms
from tb.ml.v1 import training_pb2 as tp
from tb_ml.core import InvalidConfig, MLCore, NotFound
from tbcore.audit import AuditSink
from tbcore.ml import caller as callers
from tbcore.ml.caller import PermissionDenied
from tbcore.rpc import current_caller

SERVICE = ms.DESCRIPTOR.services_by_name["MLService"]
GATEWAY, OPTIMIZER, ASSISTANT = "api-gateway", "optimizer", "assistant"
ANALYST_TOOLS = [
    "ListModelFamilies",
    "ListFeatureSets",
    "ListLabelPresets",
    "EstimateTrainingJob",
    "SubmitTrainingJob",
    "GetJob",
    "ListJobs",
    "StreamJobProgress",
    "CancelJob",
    "GetRun",
    "ListRuns",
    "GetModelCard",
    "GetTearsheet",
    "GetDiagnostics",
    "GetLearningCurves",
    "GetFeatureImportance",
    "GetErrorSlices",
    "GetDatasetProfile",
    "SubmitAblation",
    "AnnotateRun",
]
ALLOW: dict[str, list[str]] = {m.name: [GATEWAY, OPTIMIZER] for m in SERVICE.methods}
for _m in ANALYST_TOOLS:
    ALLOW[_m] = [GATEWAY, OPTIMIZER, ASSISTANT]

R = TypeVar("R")


class MLServicer:
    def __init__(self, core: MLCore, audit: AuditSink) -> None:
        self.core = core
        self.audit = audit

    def _caller(self, ctx: tp.CallerContext | None) -> tp.CallerContext:
        return callers.resolve(ctx, current_caller())

    def _do(self, context: grpc.ServicerContext, method: str, caller: tp.CallerContext, fn: Callable[[], R]) -> R:
        try:
            return fn()
        except NotFound as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"not found: {exc}")
        except InvalidConfig as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except PermissionDenied as exc:
            self.audit.append(callers.name(caller), "ml.denied", method, {"reason": str(exc)})
            context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except ValueError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        raise AssertionError("unreachable")

    def ListModelFamilies(self, req: ms.ListModelFamiliesRequest, context: grpc.ServicerContext) -> Any:
        return ms.ListModelFamiliesResponse(families=self.core.families())

    def ListFeatureSets(self, req: ms.ListFeatureSetsRequest, context: grpc.ServicerContext) -> Any:
        return ms.ListFeatureSetsResponse(feature_sets=self.core.feature_sets())

    def ListLabelPresets(self, req: ms.ListLabelPresetsRequest, context: grpc.ServicerContext) -> Any:
        return ms.ListLabelPresetsResponse(presets=self.core.label_presets())

    def EstimateTrainingJob(self, req: ms.EstimateTrainingJobRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        rc, _, errors = self.core.prepare(req.config, c)
        if errors:
            return ms.EstimateTrainingJobResponse(errors=errors)
        return ms.EstimateTrainingJobResponse(estimate=self.core.estimate(rc))

    def SubmitTrainingJob(self, req: ms.SubmitTrainingJobRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        confirm = req.confirm_over_budget and c.actor == tp.ACTOR_OWNER
        job = self._do(context, "SubmitTrainingJob", c, lambda: self.core.submit(req.config, c, confirm))
        return ms.SubmitTrainingJobResponse(job=job)

    def GetJob(self, req: ms.GetJobRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        return ms.GetJobResponse(job=self._do(context, "GetJob", c, lambda: self.core.job(req.job_id)))

    def ListJobs(self, req: ms.ListJobsRequest, context: grpc.ServicerContext) -> Any:
        return ms.ListJobsResponse(jobs=self.core.jobs(req.lineage_id, req.limit))

    def StreamJobProgress(self, req: ms.StreamJobProgressRequest, context: grpc.ServicerContext) -> Iterator[Any]:
        try:
            for p in self.core.stream(req.job_id, req.after_seq):
                if not context.is_active():
                    return
                yield ms.StreamJobProgressResponse(progress=p)
        except NotFound:
            context.abort(grpc.StatusCode.NOT_FOUND, f"job {req.job_id} not found")

    def CancelJob(self, req: ms.CancelJobRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        return ms.CancelJobResponse(job=self._do(context, "CancelJob", c, lambda: self.core.cancel(req.job_id, c)))

    def GetRun(self, req: ms.GetRunRequest, context: grpc.ServicerContext) -> Any:
        return ms.GetRunResponse(run=self._do(context, "GetRun", self._caller(None), lambda: self.core.run(req.run_id)))

    def ListRuns(self, req: ms.ListRunsRequest, context: grpc.ServicerContext) -> Any:
        return ms.ListRunsResponse(runs=self.core.list_runs(req.lineage_id, req.family, req.limit))

    def RegisterModel(self, req: ms.RegisterModelRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        mv = self._do(context, "RegisterModel", c, lambda: self.core.register(req.run_id, req.name, c))
        return ms.RegisterModelResponse(model_version=mv)

    def Predict(self, req: ms.PredictRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        sigs = self._do(
            context,
            "Predict",
            c,
            lambda: self.core.predict(req.model_version, list(req.instrument_ids), req.as_of, req.strategy_id),
        )
        return ms.PredictResponse(signals=sigs)

    def GetModelCard(self, req: ms.GetModelCardRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        return ms.GetModelCardResponse(
            card=self._do(context, "GetModelCard", c, lambda: self.core.model_card(req.run_id))
        )

    def GetTearsheet(self, req: ms.GetTearsheetRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(None)
        page, engine = self._do(context, "GetTearsheet", c, lambda: self.core.tearsheet(req.run_id))
        return ms.GetTearsheetResponse(html=page, engine=engine)

    def GetDiagnostics(self, req: ms.GetDiagnosticsRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        rep = self._do(context, "GetDiagnostics", c, lambda: self.core.diagnostics(req.run_id))
        return ms.GetDiagnosticsResponse(report=rep)

    def GetLearningCurves(self, req: ms.GetLearningCurvesRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        rep = self._do(context, "GetLearningCurves", c, lambda: self.core.diagnostics(req.run_id))
        return ms.GetLearningCurvesResponse(curves=rep.learning_curves)

    def GetFeatureImportance(self, req: ms.GetFeatureImportanceRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        imp = self._do(context, "GetFeatureImportance", c, lambda: self.core.importance(req.run_id, req.method))
        return ms.GetFeatureImportanceResponse(importance=imp)

    def GetErrorSlices(self, req: ms.GetErrorSlicesRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        sl = self._do(context, "GetErrorSlices", c, lambda: self.core.error_slices(req.run_id, req.slice_by))
        return ms.GetErrorSlicesResponse(slices=sl)

    def GetDatasetProfile(self, req: ms.GetDatasetProfileRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        p = self._do(context, "GetDatasetProfile", c, lambda: self.core.profile(req.snapshot_id))
        return ms.GetDatasetProfileResponse(profile=p)

    def SubmitAblation(self, req: ms.SubmitAblationRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        jobs = self._do(context, "SubmitAblation", c, lambda: self.core.ablate(req.run_id, list(req.variants), c))
        return ms.SubmitAblationResponse(jobs=jobs)

    def AnnotateRun(self, req: ms.AnnotateRunRequest, context: grpc.ServicerContext) -> Any:
        c = self._caller(req.caller)
        self._do(context, "AnnotateRun", c, lambda: self.core.annotate(req.run_id, req.note, c))
        return ms.AnnotateRunResponse()
