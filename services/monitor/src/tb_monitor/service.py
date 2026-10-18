"""``MonitorService`` over gRPC (ADR-0045). Callers: api-gateway (UI; owner actor and step-up attested
by the gateway), assistant (``GetLiveFeedback`` only). Only api-gateway may assert an actor or a step-up;
any other caller is treated as itself (SE-040)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import grpc

from tb.monitor.v1 import feedback_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tb_monitor.alertbox import Inbox
from tb_monitor.core import K_DAILY, K_DIV, K_DRIFT, K_EXEC, K_GRAD, K_HEALTH, K_PERF, Monitor
from tbcore.rpc import current_caller

SERVICE = mp.DESCRIPTOR.services_by_name["MonitorService"]
GATEWAY, ASSISTANT = "api-gateway", "assistant"
ALLOW: dict[str, list[str]] = {m.name: [GATEWAY] for m in SERVICE.methods}
ALLOW["GetLiveFeedback"] = [GATEWAY, ASSISTANT]
ALLOW["GetGraduationEvidence"] = [GATEWAY, "execution"]
R = TypeVar("R")


def actor(a: ex.Actor) -> ex.Actor:
    c = current_caller()
    if c and c != GATEWAY:
        return ex.Actor(name=f"service:{c}")
    out = ex.Actor()
    out.CopyFrom(a)
    out.name = out.name or "owner"
    return out


class MonitorServicer:
    def __init__(self, monitor: Monitor, inbox: Inbox) -> None:
        self.m, self.inbox = monitor, inbox

    def _do(self, ctx: grpc.ServicerContext, fn: Callable[[], R]) -> R:
        try:
            return fn()
        except KeyError as exc:
            ctx.abort(grpc.StatusCode.NOT_FOUND, f"not found: {exc}")
        except PermissionError as exc:
            ctx.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except ValueError as exc:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        except RuntimeError as exc:
            ctx.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        raise AssertionError("unreachable")

    def _known(self, sid: str) -> str:
        if sid not in self.m.strategies:
            raise KeyError(sid)
        return sid

    def _stored(self, kind: str, sid: str, cls: type[Any], build: Callable[[str], Any]) -> Any:
        self._known(sid)
        got = self.m.store.get(kind, sid, cls)
        return got if got is not None else build(sid)

    def ListStrategyHealth(self, req: mp.ListStrategyHealthRequest, ctx: grpc.ServicerContext) -> Any:
        rows = []
        for sid in sorted(self.m.strategies):
            h = self.m.store.get(K_HEALTH, sid, mp.StrategyHealth)
            rows.append(
                h
                if h is not None
                else mp.StrategyHealth(
                    strategy_id=sid,
                    mode=self.m.strategies[sid].mode,
                    status=mp.HEALTH_STATUS_INSUFFICIENT_DATA,
                    score=100.0,
                )
            )
        return mp.ListStrategyHealthResponse(strategies=rows)

    def GetStrategyHealth(self, req: mp.GetStrategyHealthRequest, ctx: grpc.ServicerContext) -> Any:
        h = self._do(
            ctx,
            lambda: self._stored(K_HEALTH, req.strategy_id, mp.StrategyHealth, lambda s: self.m.evaluate(s, act=False)),
        )
        return mp.GetStrategyHealthResponse(health=h)

    def GetPerformanceReport(self, req: mp.GetPerformanceReportRequest, ctx: grpc.ServicerContext) -> Any:
        r = self._do(ctx, lambda: self._stored(K_PERF, req.strategy_id, mp.PerformanceReport, self.m.performance))
        return mp.GetPerformanceReportResponse(report=r)

    def GetDriftReport(self, req: mp.GetDriftReportRequest, ctx: grpc.ServicerContext) -> Any:
        r = self._do(ctx, lambda: self._stored(K_DRIFT, req.strategy_id, mp.DriftReport, self.m.drift))
        return mp.GetDriftReportResponse(report=r)

    def GetDivergenceReport(self, req: mp.GetDivergenceReportRequest, ctx: grpc.ServicerContext) -> Any:
        hist = self._do(ctx, lambda: self.m.divergence_history(self._known(req.strategy_id)))
        pick = next((r for r in hist if r.session_date == req.session_date), None) if req.session_date else None
        cur = pick or (hist[0] if hist else mp.DivergenceReport(strategy_id=req.strategy_id))
        return mp.GetDivergenceReportResponse(report=cur, history=hist[:30])

    def GetExecutionQuality(self, req: mp.GetExecutionQualityRequest, ctx: grpc.ServicerContext) -> Any:
        r = self._do(ctx, lambda: self._stored(K_EXEC, req.strategy_id, mp.ExecutionQualityReport, self.m.execution))
        return mp.GetExecutionQualityResponse(report=r)

    def GetLiveFeedback(self, req: mp.GetLiveFeedbackRequest, ctx: grpc.ServicerContext) -> Any:
        r = self.m.store.get("feedback", req.strategy_id, feedback_pb2.LiveFeedbackReport)
        if r is None:
            ctx.abort(grpc.StatusCode.NOT_FOUND, f"no live feedback for {req.strategy_id}")
        return mp.GetLiveFeedbackResponse(report=r)

    def ListAlerts(self, req: mp.ListAlertsRequest, ctx: grpc.ServicerContext) -> Any:
        return mp.ListAlertsResponse(alerts=self.inbox.list(req.severity, req.status, req.since, req.limit))

    def AckAlert(self, req: mp.AckAlertRequest, ctx: grpc.ServicerContext) -> Any:
        r = self._do(ctx, lambda: self.inbox.ack(req.dedupe_key, actor(req.actor), req.note))
        return mp.AckAlertResponse(alert=r)

    def SilenceAlert(self, req: mp.SilenceAlertRequest, ctx: grpc.ServicerContext) -> Any:
        sid, ends = self._do(
            ctx, lambda: self.inbox.silence(req.dedupe_key, int(req.duration_seconds), req.reason, actor(req.actor))
        )
        return mp.SilenceAlertResponse(silence_id=sid, ends_at=ends)

    def GetDailyReport(self, req: mp.GetDailyReportRequest, ctx: grpc.ServicerContext) -> Any:
        if req.date:
            r = self.m.store.get(K_DAILY, req.date, mp.DailyReport)
        else:
            rows = self.m.store.list(K_DAILY, mp.DailyReport)
            r = max(rows, key=lambda x: x.date) if rows else None
        if r is None:
            ctx.abort(grpc.StatusCode.NOT_FOUND, "no daily report")
        return mp.GetDailyReportResponse(report=r)

    def GetContainmentPolicy(self, req: mp.GetContainmentPolicyRequest, ctx: grpc.ServicerContext) -> Any:
        p, inherited = self.m.containment.policy(req.strategy_id)
        return mp.GetContainmentPolicyResponse(policy=p, inherited=inherited)

    def SetContainmentPolicy(self, req: mp.SetContainmentPolicyRequest, ctx: grpc.ServicerContext) -> Any:
        if req.policy.strategy_id:
            self._do(ctx, lambda: self._known(req.policy.strategy_id))
        p, loose = self._do(ctx, lambda: self.m.containment.set_policy(req.policy, actor(req.actor), req.reason))
        return mp.SetContainmentPolicyResponse(policy=p, loosened=loose)

    def ListContainmentActions(self, req: mp.ListContainmentActionsRequest, ctx: grpc.ServicerContext) -> Any:
        return mp.ListContainmentActionsResponse(actions=self.m.containment.actions(req.strategy_id, req.limit or 100))

    def GetGraduationEvidence(self, req: mp.GetGraduationEvidenceRequest, ctx: grpc.ServicerContext) -> Any:
        e = self._do(ctx, lambda: self.m.graduation(self._known(req.strategy_id)))
        return mp.GetGraduationEvidenceResponse(evidence=e)


__all__ = ["ALLOW", "K_DIV", "K_GRAD", "SERVICE", "MonitorServicer"]
