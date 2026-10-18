"""Research journal and analyst state (AN-003, AN-055; schema ``analyst``).

Stores every round (`AnalystRound` as protobuf bytes plus indexed columns), the per-lineage loop
state, handled trigger IDs (idempotency, AN-001), job → round mapping for continuations, the LLM and
tool call logs, code proposals, and chat conversations. ``MemoryJournal`` backs tests and the
evaluation suite; ``PgJournal`` is the service store (``svc_assistant``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from google.protobuf import json_format

from tb.analyst.v1 import analyst_pb2
from tb_assistant.guarded import LLMCallRecord
from tb_assistant.tools import ToolCallRecord


@dataclass
class LoopRecord:
    lineage_id: str
    state: int = analyst_pb2.LOOP_STATE_IDLE
    paused: bool = False
    rounds: int = 0
    best_dsr_history: list[float] = field(default_factory=list)
    pbo_history: list[float] = field(default_factory=list)
    no_improve: int = 0
    failed_rounds: int = 0
    last_decision: int = analyst_pb2.ROUND_DECISION_UNSPECIFIED
    current_round_id: str = ""
    # job or study id → [round_id, action_id]
    pending: dict[str, list[str]] = field(default_factory=dict)
    budget_json: str = ""
    trials_at_start: int = -1
    strategy_id: str = ""

    def budget(self) -> analyst_pb2.Budget:
        b = analyst_pb2.Budget()
        if self.budget_json:
            json_format.Parse(self.budget_json, b)
        return b

    def set_budget(self, b: analyst_pb2.Budget) -> None:
        self.budget_json = json_format.MessageToJson(b, preserving_proto_field_name=True, indent=None)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> LoopRecord:
        return cls(**json.loads(s))


class Journal(Protocol):
    async def mark_trigger(self, trigger_id: str) -> bool:
        """Record a trigger; False if it was already handled (AN-001)."""
        ...

    async def save_round(self, rnd: analyst_pb2.AnalystRound) -> None: ...
    async def get_round(self, round_id: str) -> analyst_pb2.AnalystRound | None: ...
    async def list_rounds(
        self, lineage_id: str, limit: int = 50, decision: int = 0
    ) -> list[analyst_pb2.AnalystRound]: ...
    async def get_loop(self, lineage_id: str) -> LoopRecord: ...
    async def save_loop(self, rec: LoopRecord) -> None: ...
    async def list_loops(self) -> list[LoopRecord]: ...
    async def record_llm_call(self, rec: LLMCallRecord) -> None: ...
    async def record_tool_call(self, rec: ToolCallRecord) -> None: ...
    async def save_proposal(self, p: analyst_pb2.CodeProposal) -> None: ...
    async def get_proposal(self, proposal_id: str) -> analyst_pb2.CodeProposal | None: ...
    async def list_proposals(self, lineage_id: str = "") -> list[analyst_pb2.CodeProposal]: ...
    async def save_conversation(self, c: analyst_pb2.Conversation) -> None: ...
    async def get_conversation(self, conversation_id: str) -> analyst_pb2.Conversation | None: ...
    async def list_conversations(self) -> list[analyst_pb2.Conversation]: ...
    async def purge_conversations(self, older_than: datetime) -> int: ...
    async def llm_spend(self, since: datetime) -> float: ...


class MemoryJournal:
    def __init__(self) -> None:
        self.triggers: set[str] = set()
        self.rounds: dict[str, analyst_pb2.AnalystRound] = {}
        self.loops: dict[str, LoopRecord] = {}
        self.llm_calls: list[LLMCallRecord] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.proposals: dict[str, analyst_pb2.CodeProposal] = {}
        self.conversations: dict[str, analyst_pb2.Conversation] = {}

    async def mark_trigger(self, trigger_id: str) -> bool:
        if trigger_id in self.triggers:
            return False
        self.triggers.add(trigger_id)
        return True

    async def save_round(self, rnd: analyst_pb2.AnalystRound) -> None:
        c = analyst_pb2.AnalystRound()
        c.CopyFrom(rnd)
        self.rounds[rnd.round_id] = c

    async def get_round(self, round_id: str) -> analyst_pb2.AnalystRound | None:
        r = self.rounds.get(round_id)
        if r is None:
            return None
        c = analyst_pb2.AnalystRound()
        c.CopyFrom(r)
        return c

    async def list_rounds(self, lineage_id: str, limit: int = 50, decision: int = 0) -> list[analyst_pb2.AnalystRound]:
        rs = [
            r
            for r in self.rounds.values()
            if (not lineage_id or r.lineage_id == lineage_id) and (not decision or r.decision == decision)
        ]
        rs.sort(key=lambda r: (r.started_at, r.seq), reverse=True)
        return rs[: limit or 50]

    async def get_loop(self, lineage_id: str) -> LoopRecord:
        rec = self.loops.get(lineage_id)
        return LoopRecord.from_json(rec.to_json()) if rec else LoopRecord(lineage_id=lineage_id)

    async def save_loop(self, rec: LoopRecord) -> None:
        self.loops[rec.lineage_id] = LoopRecord.from_json(rec.to_json())

    async def list_loops(self) -> list[LoopRecord]:
        return [LoopRecord.from_json(r.to_json()) for r in self.loops.values()]

    async def record_llm_call(self, rec: LLMCallRecord) -> None:
        self.llm_calls.append(rec)

    async def record_tool_call(self, rec: ToolCallRecord) -> None:
        self.tool_calls.append(rec)

    async def save_proposal(self, p: analyst_pb2.CodeProposal) -> None:
        c = analyst_pb2.CodeProposal()
        c.CopyFrom(p)
        self.proposals[p.proposal_id] = c

    async def get_proposal(self, proposal_id: str) -> analyst_pb2.CodeProposal | None:
        return self.proposals.get(proposal_id)

    async def list_proposals(self, lineage_id: str = "") -> list[analyst_pb2.CodeProposal]:
        return [p for p in self.proposals.values() if not lineage_id or p.lineage_id == lineage_id]

    async def save_conversation(self, c: analyst_pb2.Conversation) -> None:
        cc = analyst_pb2.Conversation()
        cc.CopyFrom(c)
        self.conversations[c.conversation_id] = cc

    async def get_conversation(self, conversation_id: str) -> analyst_pb2.Conversation | None:
        return self.conversations.get(conversation_id)

    async def list_conversations(self) -> list[analyst_pb2.Conversation]:
        return sorted(self.conversations.values(), key=lambda c: -c.updated_at)

    async def purge_conversations(self, older_than: datetime) -> int:
        cutoff = int(older_than.timestamp() * 1e9)
        old = [k for k, c in self.conversations.items() if c.updated_at < cutoff]
        for k in old:
            del self.conversations[k]
        return len(old)

    async def llm_spend(self, since: datetime) -> float:
        return sum(c.cost_usd for c in self.llm_calls if c.created_at >= since)


class PgJournal:
    """Postgres store in schema ``analyst`` (migrations under ``libs/go/db/migrations/analyst``)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def mark_trigger(self, trigger_id: str) -> bool:
        async with self._pool.acquire() as c:
            r = await c.execute(
                "INSERT INTO analyst.triggers (trigger_id) VALUES ($1) ON CONFLICT DO NOTHING", trigger_id
            )
        return bool(r.endswith(" 1"))

    async def save_round(self, rnd: analyst_pb2.AnalystRound) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                """INSERT INTO analyst.rounds (round_id, lineage_id, seq, decision, started_at, completed_at, payload)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (round_id) DO UPDATE SET decision = EXCLUDED.decision,
                     completed_at = EXCLUDED.completed_at, payload = EXCLUDED.payload""",
                rnd.round_id,
                rnd.lineage_id,
                rnd.seq,
                rnd.decision,
                rnd.started_at,
                rnd.completed_at,
                rnd.SerializeToString(),
            )

    async def get_round(self, round_id: str) -> analyst_pb2.AnalystRound | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT payload FROM analyst.rounds WHERE round_id = $1", round_id)
        return analyst_pb2.AnalystRound.FromString(row["payload"]) if row else None

    async def list_rounds(self, lineage_id: str, limit: int = 50, decision: int = 0) -> list[analyst_pb2.AnalystRound]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                """SELECT payload FROM analyst.rounds
                   WHERE ($1 = '' OR lineage_id = $1) AND ($2 = 0 OR decision = $2)
                   ORDER BY started_at DESC, seq DESC LIMIT $3""",
                lineage_id,
                decision,
                limit or 50,
            )
        return [analyst_pb2.AnalystRound.FromString(r["payload"]) for r in rows]

    async def get_loop(self, lineage_id: str) -> LoopRecord:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT state FROM analyst.loops WHERE lineage_id = $1", lineage_id)
        return LoopRecord.from_json(row["state"]) if row else LoopRecord(lineage_id=lineage_id)

    async def save_loop(self, rec: LoopRecord) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                """INSERT INTO analyst.loops (lineage_id, state, updated_at) VALUES ($1, $2, now())
                   ON CONFLICT (lineage_id) DO UPDATE SET state = EXCLUDED.state, updated_at = now()""",
                rec.lineage_id,
                rec.to_json(),
            )

    async def list_loops(self) -> list[LoopRecord]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT state FROM analyst.loops ORDER BY lineage_id")
        return [LoopRecord.from_json(r["state"]) for r in rows]

    async def record_llm_call(self, rec: LLMCallRecord) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                """INSERT INTO analyst.llm_calls (call_id, role, provider, model, prompt_sha256, prompt, response,
                     input_tokens, output_tokens, cost_usd, latency_ms, ok, error, lineage_id, round_id, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)""",
                rec.call_id,
                rec.role,
                rec.provider,
                rec.model,
                rec.prompt_sha256,
                rec.prompt,
                rec.response,
                rec.input_tokens,
                rec.output_tokens,
                rec.cost_usd,
                rec.latency_ms,
                rec.ok,
                rec.error,
                rec.lineage_id,
                rec.round_id,
                rec.created_at,
            )

    async def record_tool_call(self, rec: ToolCallRecord) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                """INSERT INTO analyst.tool_calls (tool, lineage_id, round_id, level, outcome, input_sha256,
                     output_sha256, detail, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                rec.tool,
                rec.lineage_id,
                rec.round_id,
                rec.level,
                rec.outcome,
                rec.input_sha256,
                rec.output_sha256,
                rec.detail,
                rec.created_at,
            )

    async def save_proposal(self, p: analyst_pb2.CodeProposal) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                """INSERT INTO analyst.code_proposals (proposal_id, lineage_id, status, payload, updated_at)
                   VALUES ($1, $2, $3, $4, now())
                   ON CONFLICT (proposal_id) DO UPDATE SET status = EXCLUDED.status, payload = EXCLUDED.payload,
                     updated_at = now()""",
                p.proposal_id,
                p.lineage_id,
                p.status,
                p.SerializeToString(),
            )

    async def get_proposal(self, proposal_id: str) -> analyst_pb2.CodeProposal | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow("SELECT payload FROM analyst.code_proposals WHERE proposal_id = $1", proposal_id)
        return analyst_pb2.CodeProposal.FromString(row["payload"]) if row else None

    async def list_proposals(self, lineage_id: str = "") -> list[analyst_pb2.CodeProposal]:
        async with self._pool.acquire() as c:
            rows = await c.fetch(
                """SELECT payload FROM analyst.code_proposals WHERE ($1 = '' OR lineage_id = $1)
                   ORDER BY updated_at DESC""",
                lineage_id,
            )
        return [analyst_pb2.CodeProposal.FromString(r["payload"]) for r in rows]

    async def save_conversation(self, conv: analyst_pb2.Conversation) -> None:
        async with self._pool.acquire() as c:
            await c.execute(
                """INSERT INTO analyst.conversations (conversation_id, updated_at, payload)
                   VALUES ($1, to_timestamp($2 / 1e9), $3)
                   ON CONFLICT (conversation_id) DO UPDATE
                   SET updated_at = EXCLUDED.updated_at, payload = EXCLUDED.payload""",
                conv.conversation_id,
                conv.updated_at,
                conv.SerializeToString(),
            )

    async def get_conversation(self, conversation_id: str) -> analyst_pb2.Conversation | None:
        async with self._pool.acquire() as c:
            row = await c.fetchrow(
                "SELECT payload FROM analyst.conversations WHERE conversation_id = $1", conversation_id
            )
        return analyst_pb2.Conversation.FromString(row["payload"]) if row else None

    async def list_conversations(self) -> list[analyst_pb2.Conversation]:
        async with self._pool.acquire() as c:
            rows = await c.fetch("SELECT payload FROM analyst.conversations ORDER BY updated_at DESC LIMIT 200")
        return [analyst_pb2.Conversation.FromString(r["payload"]) for r in rows]

    async def purge_conversations(self, older_than: datetime) -> int:
        async with self._pool.acquire() as c:
            r = await c.execute("DELETE FROM analyst.conversations WHERE updated_at < $1", older_than)
        return int(r.split()[-1])

    async def llm_spend(self, since: datetime) -> float:
        async with self._pool.acquire() as c:
            v = await c.fetchval(
                "SELECT coalesce(sum(cost_usd), 0) FROM analyst.llm_calls WHERE created_at >= $1", since
            )
        return float(v or 0.0)


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def retention_cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
