"""Chat assistant (AN-080): owner questions about runs, studies, rounds, and the journal.

Answers come from the chat role through the same guarded path as the analyst (masking, egress
guard, spend caps, call log). Evidence is gathered deterministically with read tools only: IDs found
in the question (runs, studies, candidates, rounds) and the lineage's latest journal rounds. The
reply must cite the IDs it relies on; citations of IDs that were not in the evidence are dropped.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tb.analyst.v1 import analyst_pb2
from tb.ml.v1 import training_pb2
from tb.optimizer.v1 import optimizer_pb2
from tb_assistant.budget import default_budget
from tb_assistant.digest import (
    EvidenceTable,
    digest_cumulative,
    digest_diagnostics,
    digest_gate,
    digest_run,
    digest_study,
)
from tb_assistant.guarded import GuardedLLM
from tb_assistant.journal import Journal
from tb_assistant.llm.base import Role
from tb_assistant.loop import SymbolSource, anchor_for
from tb_assistant.masking import EgressGuard, Masker
from tb_assistant.research import ResearchCallError
from tb_assistant.schemas import ChatAnswerOut
from tb_assistant.tools import ToolContext, ToolDeniedError, ToolServer

SYSTEM = """You are the research assistant of a systematic trading platform, answering its owner. Use only \
the evidence and journal entries provided; never compute or recall statistics yourself; cite the report, run, \
study, or round IDs you used in cited_ids. Identifiers and dates are masked. If the evidence does not answer \
the question, say so. You cannot place orders, change limits, see the holdout, or promote anything; if asked, \
explain that the owner does that in the platform. Reply with JSON: answer, cited_ids."""
ID_RE = re.compile(
    r"\b(?:run|study|cand|candidate|job|diag|gate|fb)[-_:][A-Za-z0-9_-]{3,64}\b|\b[0-9A-HJKMNP-TV-Z]{26}\b"
)


@dataclass
class Chat:
    llm: GuardedLLM
    tools: ToolServer
    journal: Journal
    symbols: SymbolSource
    ids: Callable[[], str]
    clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC)

    async def create(self, title: str, lineage_id: str) -> analyst_pb2.Conversation:
        now = time.time_ns()
        c = analyst_pb2.Conversation(
            conversation_id=self.ids(),
            title=title[:120] or "Conversation",
            lineage_id=lineage_id,
            created_at=now,
            updated_at=now,
        )
        await self.journal.save_conversation(c)
        return c

    async def post(self, conversation_id: str, content: str, actor: str) -> analyst_pb2.ChatMessage:
        conv = await self.journal.get_conversation(conversation_id)
        if conv is None:
            raise KeyError(conversation_id)
        now = time.time_ns()
        conv.messages.append(
            analyst_pb2.ChatMessage(message_id=self.ids(), role="owner", content=content[:4000], created_at=now)
        )
        table = EvidenceTable()
        journal_ctx = await self._gather(conv.lineage_id, content, table)
        masker = Masker(anchor=anchor_for(conversation_id, now))
        syms, iids = await self.symbols.known()
        masker.learn(syms, iids)
        payload = {
            "task": "answer_question",
            "question": content[:2000],
            "history": [{"role": m.role, "content": m.content[:1000]} for m in conv.messages[-8:-1]],
            "evidence": [e.as_dict() for e in table.canonical],
            "journal": journal_ctx,
        }
        res = await self.llm.complete_json(
            Role.CHAT,
            system=SYSTEM,
            payload=payload,
            schema=ChatAnswerOut,
            masker=masker,
            guard=EgressGuard(symbols=masker.symbols, instrument_ids=masker.instrument_ids),
            lineage_id=conv.lineage_id,
        )
        known = table.report_ids | {r["round_id"] for r in journal_ctx}
        cited = [i for i in res.output.cited_ids if i in known]
        reply = analyst_pb2.ChatMessage(
            message_id=self.ids(),
            role="assistant",
            content=res.output.answer,
            cited_ids=cited,
            created_at=time.time_ns(),
        )
        conv.messages.append(reply)
        conv.updated_at = reply.created_at
        await self.journal.save_conversation(conv)
        return reply

    async def _gather(self, lineage_id: str, question: str, table: EvidenceTable) -> list[dict[str, object]]:
        ctx = ToolContext(lineage_id or "-", "chat", "chat", default_budget())

        async def read(name: str, args: dict[str, str]) -> object:
            try:
                return (await self.tools.call(ctx, name, args)).message
            except (ResearchCallError, ToolDeniedError, KeyError):
                return None

        for ref in sorted(set(ID_RE.findall(question)))[:6]:
            for name, key in (
                ("get_training_diagnostics", "run_id"),
                ("get_run", "run_id"),
                ("get_study", "study_id"),
                ("get_gate_report", "candidate_id"),
            ):
                msg = await read(name, {key: ref})
                if isinstance(msg, training_pb2.DiagnosticsReport):
                    digest_diagnostics(table, msg)
                elif isinstance(msg, training_pb2.TrainingRun):
                    digest_run(table, msg)
                elif isinstance(msg, optimizer_pb2.Study):
                    digest_study(table, msg)
                elif isinstance(msg, optimizer_pb2.GateReport):
                    digest_gate(table, msg)
        out: list[dict[str, object]] = []
        if lineage_id:
            stats = await read("get_cumulative_overfitting_stats", {})
            if isinstance(stats, optimizer_pb2.CumulativeOverfittingStats):
                digest_cumulative(table, stats, f"stats:{lineage_id}:{stats.n_trials}")
            for r in await self.journal.list_rounds(lineage_id, limit=5):
                out.append(
                    {
                        "round_id": r.round_id,
                        "seq": r.seq,
                        "decision": analyst_pb2.RoundDecision.Name(r.decision),
                        "summary": (r.summary or r.diagnosis.summary)[:800],
                        "best_dsr": r.outcome.best_dsr,
                        "actions": [a.description[:200] for a in r.plan.actions[:5]],
                    }
                )
        return out
