"""gRPC ``tb.analyst.v1.AnalystService`` for api-gateway (the only caller, SE-040).

Served with ``tbcore.rpc.serve`` (sync handlers on a thread pool, the service-token interceptor
with an allowlist of ``api-gateway`` only); each handler runs the async analyst code on the
service's event loop. Owner identity and step-up are established in the gateway (AN-061); actions
here are audited with the actor the gateway passes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

import grpc

from tb.analyst.v1 import analyst_pb2
from tb.ml.v1 import training_pb2
from tb.optimizer.v1 import optimizer_pb2
from tb_assistant.budget import default_budget
from tb_assistant.chat import Chat
from tb_assistant.coder import Coder
from tb_assistant.journal import Journal, LoopRecord
from tb_assistant.loop import AnalystLoop, Trigger
from tb_assistant.research import OPT, ResearchCallError
from tb_assistant.tools import ToolContext, ToolServer

T = TypeVar("T")
ALLOW = {"*": ["api-gateway"]}
SOURCES = {
    training_pb2.ACTOR_OWNER: "owner",
    training_pb2.ACTOR_ANALYST: "analyst",
    training_pb2.ACTOR_SCHEDULE: "schedule",
}
REF_KINDS = {"run": "run_id", "study": "study_id", "candidate": "candidate_id", "strategy": "strategy_id"}


@dataclass
class AnalystServicer:
    loop: AnalystLoop
    journal: Journal
    tools: ToolServer
    chat: Chat
    coder: Coder
    aloop: asyncio.AbstractEventLoop
    timeout: float = 120.0

    def _run(self, coro: Coroutine[Any, Any, T], context: grpc.ServicerContext) -> T:
        try:
            return asyncio.run_coroutine_threadsafe(coro, self.aloop).result(self.timeout)
        except KeyError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, f"not found: {exc}")
        except PermissionError as exc:
            context.abort(grpc.StatusCode.PERMISSION_DENIED, str(exc))
        except (ValueError, ResearchCallError) as exc:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT if isinstance(exc, ValueError) else grpc.StatusCode.UNAVAILABLE,
                str(exc),
            )
        raise AssertionError("unreachable")

    # ---- journal and status

    def GetJournal(
        self, req: analyst_pb2.GetJournalRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.GetJournalResponse:
        rounds = self._run(self.journal.list_rounds(req.lineage_id, req.limit or 50, req.decision), context)
        return analyst_pb2.GetJournalResponse(rounds=rounds)

    def GetRound(self, req: analyst_pb2.GetRoundRequest, context: grpc.ServicerContext) -> analyst_pb2.GetRoundResponse:
        rnd = self._run(self.journal.get_round(req.round_id), context)
        if rnd is None:
            context.abort(grpc.StatusCode.NOT_FOUND, req.round_id)
        return analyst_pb2.GetRoundResponse(round=rnd)

    async def _status(self, lineage: optimizer_pb2.Lineage | None, rec: LoopRecord) -> analyst_pb2.LoopStatus:
        today = await self.journal.llm_spend(_day_start(self.loop.clock()))
        month = await self.journal.llm_spend(_month_start(self.loop.clock()))
        st = analyst_pb2.LoopStatus(
            lineage_id=rec.lineage_id,
            state=analyst_pb2.LoopState.ValueType(rec.state or analyst_pb2.LOOP_STATE_IDLE),
            current_round_id=rec.current_round_id,
            rounds_completed=rec.rounds,
            rounds_without_improvement=rec.no_improve,
            last_decision=analyst_pb2.RoundDecision.ValueType(rec.last_decision),
            llm_usd_today=today,
            llm_usd_month=month,
        )
        st.budget_used.CopyFrom(self.loop.budget.usage_proto(rec.lineage_id))
        a = st.autonomy
        a.lineage_id, a.paused = rec.lineage_id, rec.paused
        a.budget.CopyFrom(rec.budget() if rec.budget_json else self.loop.s.default_budget)
        if lineage is not None:
            a.level = lineage.autonomy_level
            lim = lineage.promotion_limits
            a.promotion_limits.CopyFrom(
                analyst_pb2.PromotionLimits(
                    max_promotions=lim.max_promotions_per_window or 1,
                    window_days=int((lim.window_seconds or 14 * 86400) // 86400),
                    min_shadow_sessions=lim.min_shadow_sessions or 10,
                    cooldown_days=int((lim.cooldown_after_demotion_seconds or 14 * 86400) // 86400),
                )
            )
        return st

    async def _all_status(self) -> list[analyst_pb2.LoopStatus]:
        resp = await self.tools.api.call(OPT, "ListLineages", optimizer_pb2.ListLineagesRequest())
        assert isinstance(resp, optimizer_pb2.ListLineagesResponse)
        out = []
        for lin in resp.lineages:
            out.append(await self._status(lin, await self.journal.get_loop(lin.lineage_id)))
        return out

    def ListLineages(
        self, req: analyst_pb2.ListLineagesRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.ListLineagesResponse:
        return analyst_pb2.ListLineagesResponse(lineages=self._run(self._all_status(), context))

    async def _one_status(self, lineage_id: str) -> analyst_pb2.LoopStatus:
        try:
            lin: optimizer_pb2.Lineage | None = await self.tools.lineage(lineage_id)
        except ResearchCallError:
            lin = None
        return await self._status(lin, await self.journal.get_loop(lineage_id))

    def GetLoopStatus(
        self, req: analyst_pb2.GetLoopStatusRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.GetLoopStatusResponse:
        return analyst_pb2.GetLoopStatusResponse(status=self._run(self._one_status(req.lineage_id), context))

    def NotifyAutonomyChanged(
        self, req: analyst_pb2.NotifyAutonomyChangedRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.NotifyAutonomyChangedResponse:
        return analyst_pb2.NotifyAutonomyChangedResponse(status=self._run(self._one_status(req.lineage_id), context))

    # ---- owner actions

    def ReviewActions(
        self, req: analyst_pb2.ReviewActionsRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.ReviewActionsResponse:
        plan, prepared = self._run(
            self.loop.review_actions(
                req.round_id, list(req.action_ids), approve=req.approve, reason=req.reason, actor=req.actor or "owner"
            ),
            context,
        )
        return analyst_pb2.ReviewActionsResponse(plan=plan, launches=prepared)

    def RecordLaunches(
        self, req: analyst_pb2.RecordLaunchesRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.RecordLaunchesResponse:
        plan = self._run(
            self.loop.record_launches(req.round_id, list(req.launches), actor=req.actor or "owner"), context
        )
        return analyst_pb2.RecordLaunchesResponse(plan=plan)

    def SetLoopPaused(
        self, req: analyst_pb2.SetLoopPausedRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.SetLoopPausedResponse:
        async def go() -> list[analyst_pb2.LoopStatus]:
            recs = await self.loop.set_paused(req.lineage_id, req.paused, actor=req.actor or "owner", reason=req.reason)
            return [await self._one_status(r.lineage_id) for r in recs]

        return analyst_pb2.SetLoopPausedResponse(lineages=self._run(go(), context))

    def RequestAnalysis(
        self, req: analyst_pb2.RequestAnalysisRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.RequestAnalysisResponse:
        if req.ref_kind and req.ref_kind not in REF_KINDS:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, f"ref_kind must be one of {sorted(REF_KINDS)}")
        trig = Trigger(
            analyst_pb2.ROUND_TRIGGER_OWNER_REQUEST, req.lineage_id, f"owner:{uuid.uuid4()}", question=req.question
        )
        if req.ref_kind:
            setattr(trig, REF_KINDS[req.ref_kind], req.ref_id)
        rnd = self._run(self.loop.on_trigger(trig), context)
        return analyst_pb2.RequestAnalysisResponse(round_id=rnd.round_id if rnd is not None else "")

    async def _ledger(self, lineage_id: str, limit: int) -> analyst_pb2.GetTrialLedgerResponse:
        ctx = ToolContext(lineage_id, "ui", "ui", default_budget())
        led = (await self.tools.call(ctx, "get_trial_ledger")).message
        trials = (await self.tools.call(ctx, "list_trials", {"limit": limit or 200})).message
        stats = (await self.tools.call(ctx, "get_cumulative_overfitting_stats")).message
        out = analyst_pb2.GetTrialLedgerResponse(lineage_id=lineage_id)
        if isinstance(trials, optimizer_pb2.ListTrialsResponse):
            for t in trials.trials:
                out.trials.append(
                    analyst_pb2.TrialRow(
                        trial_id=t.trial_id,
                        cycle_id=t.cycle_id,
                        source=SOURCES.get(t.source, "system"),
                        status=optimizer_pb2.TrialStatus.Name(t.status).removeprefix("TRIAL_STATUS_").lower(),
                        spec_hash=t.spec_hash,
                        sharpe=t.sharpe,
                        created_at=t.created_at,
                        family=",".join(t.dimensions),
                    )
                )
        if isinstance(stats, optimizer_pb2.CumulativeOverfittingStats):
            out.outcome.CopyFrom(
                analyst_pb2.DeflatedOutcome(
                    lineage_trials=stats.n_trials,
                    effective_trials=stats.n_effective,
                    best_dsr=stats.deflated_sharpe_prob,
                    pbo=stats.pbo,
                    best_candidate_id=stats.best_trial_id,
                )
            )
        if isinstance(led, optimizer_pb2.TrialLedger):
            cur = [c for c in led.cycles if c.status == optimizer_pb2.CYCLE_STATUS_OPEN] or list(led.cycles[-1:])
            if cur:
                out.cycle_budget, out.cycle_used = (
                    int(cur[-1].effective_trial_budget),
                    int(cur[-1].effective_trials_used),
                )
        return out

    def GetTrialLedger(
        self, req: analyst_pb2.GetTrialLedgerRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.GetTrialLedgerResponse:
        return self._run(self._ledger(req.lineage_id, req.limit), context)

    def ListCodeProposals(
        self, req: analyst_pb2.ListCodeProposalsRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.ListCodeProposalsResponse:
        return analyst_pb2.ListCodeProposalsResponse(
            proposals=self._run(self.journal.list_proposals(req.lineage_id), context)
        )

    def ReviewCodeProposal(
        self, req: analyst_pb2.ReviewCodeProposalRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.ReviewCodeProposalResponse:
        p = self._run(
            self.coder.review(req.proposal_id, approve=req.approve, note=req.note, actor=req.actor or "owner"), context
        )
        return analyst_pb2.ReviewCodeProposalResponse(proposal=p)

    # ---- chat (AN-080)

    def CreateConversation(
        self, req: analyst_pb2.CreateConversationRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.CreateConversationResponse:
        return analyst_pb2.CreateConversationResponse(
            conversation=self._run(self.chat.create(req.title, req.lineage_id), context)
        )

    def GetConversation(
        self, req: analyst_pb2.GetConversationRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.GetConversationResponse:
        c = self._run(self.journal.get_conversation(req.conversation_id), context)
        if c is None:
            context.abort(grpc.StatusCode.NOT_FOUND, req.conversation_id)
        return analyst_pb2.GetConversationResponse(conversation=c)

    def ListConversations(
        self, req: analyst_pb2.ListConversationsRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.ListConversationsResponse:
        convs = self._run(self.journal.list_conversations(), context)
        return analyst_pb2.ListConversationsResponse(conversations=[_header(c) for c in convs])

    def PostMessage(
        self, req: analyst_pb2.PostMessageRequest, context: grpc.ServicerContext
    ) -> analyst_pb2.PostMessageResponse:
        return analyst_pb2.PostMessageResponse(
            reply=self._run(self.chat.post(req.conversation_id, req.content, req.actor or "owner"), context)
        )


def _header(c: analyst_pb2.Conversation) -> analyst_pb2.Conversation:
    return analyst_pb2.Conversation(
        conversation_id=c.conversation_id,
        title=c.title,
        lineage_id=c.lineage_id,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _day_start(now: Any) -> Any:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(now: Any) -> Any:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
