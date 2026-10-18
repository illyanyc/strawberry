"""Wires the full analyst stack on in-memory fakes (tests, the evaluation suite, local demos)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from google.protobuf.message import Message

from tb.analyst.v1 import analyst_pb2
from tb_assistant.audit import MemoryAudit
from tb_assistant.budget import BudgetGuard, default_budget
from tb_assistant.coder import Coder, SandboxClient
from tb_assistant.eval.fake_research import FakeResearch, World
from tb_assistant.guarded import GuardedLLM, SpendLedger
from tb_assistant.journal import MemoryJournal
from tb_assistant.llm.base import LLMProvider, Role
from tb_assistant.llm.fake import FakeLLM
from tb_assistant.loop import AnalystLoop, LoopSettings, StaticSymbols, Trigger
from tb_assistant.planner import Planner
from tb_assistant.quarantine import Quarantine
from tb_assistant.tools import ToolServer


@dataclass
class MemoryPublisher:
    messages: list[tuple[str, Message]] = field(default_factory=list)
    alerts: list[tuple[str, str, str]] = field(default_factory=list)

    async def publish(self, subject: str, msg: Message) -> None:
        self.messages.append((subject, msg))

    async def alert(self, severity: str, subject: str, message: str, dedupe_key: str) -> None:
        self.alerts.append((severity, subject, message))

    def subjects(self) -> list[str]:
        return [s for s, _ in self.messages]


class PassingSandbox:
    """Sandbox stand-in for loop tests; the real pipeline is tested in tests/python/test_analyst_sandbox.py."""

    async def run(self, job_id: str, source: str, request: dict[str, Any]) -> dict[str, Any]:
        return {"passed": True, "checks": [{"name": "future_shift", "passed": True, "messages": []}]}


def _ids() -> Callable[[], str]:
    n = iter(range(10**9))
    return lambda: f"01ANALYST{next(n):017d}"


class Clock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 10, 19, 14, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@dataclass
class Harness:
    research: FakeResearch
    journal: MemoryJournal
    audit: MemoryAudit
    pub: MemoryPublisher
    llm: GuardedLLM
    providers: dict[Role, LLMProvider]
    budget: BudgetGuard
    tools: ToolServer
    loop: AnalystLoop
    clock: Clock

    async def trigger_run(
        self, lineage_id: str, run_id: str, *, notes: list[str] | None = None
    ) -> analyst_pb2.AnalystRound | None:
        return await self.loop.on_trigger(
            Trigger(
                analyst_pb2.ROUND_TRIGGER_RUN_COMPLETED,
                lineage_id,
                f"ml.run.completed:{run_id}",
                run_id=run_id,
                notes=notes or [],
            )
        )

    async def pump(self, max_events: int = 200) -> list[analyst_pb2.AnalystRound]:
        """Deliver pending completion events from the fake services to the loop (the bus, in tests)."""
        rounds: list[analyst_pb2.AnalystRound] = []
        n = 0
        while self.research.completions and n < max_events:
            ev = self.research.completions.pop(0)
            n += 1
            if ev["kind"] == "run":
                t = Trigger(
                    analyst_pb2.ROUND_TRIGGER_RUN_COMPLETED,
                    ev["lineage_id"],
                    f"ml.run.completed:{ev['run_id']}",
                    run_id=ev["run_id"],
                    job_id=ev["job_id"],
                    analyst_round_id=ev["analyst_round_id"],
                    failed=ev["failed"],
                )
            else:
                t = Trigger(
                    analyst_pb2.ROUND_TRIGGER_STUDY_COMPLETED,
                    ev["lineage_id"],
                    f"study:{ev['study_id']}",
                    study_id=ev["study_id"],
                    candidate_id=ev.get("candidate_id", ""),
                    analyst_round_id=ev["analyst_round_id"],
                )
            r = await self.loop.on_trigger(t)
            if r is not None:
                rounds.append(r)
        return rounds

    async def run_until_stopped(self, max_steps: int = 50) -> list[analyst_pb2.AnalystRound]:
        out: list[analyst_pb2.AnalystRound] = []
        for _ in range(max_steps):
            got = await self.pump()
            out.extend(got)
            if not self.research.completions:
                break
        return out


def build(
    *,
    lineages: dict[str, dict[str, Any]] | None = None,
    planner: LLMProvider | None = None,
    quarantine: LLMProvider | None = None,
    settings: LoopSettings | None = None,
    symbols: set[str] | None = None,
    budget: analyst_pb2.Budget | None = None,
    spend: SpendLedger | None = None,
    sandbox: SandboxClient | None = None,
) -> Harness:
    clock = Clock()
    research = FakeResearch()
    for lid, kw in (lineages or {"L1": {}}).items():
        world = kw.pop("world", None) or World()
        research.add_lineage(lid, world=world, **kw)
    journal = MemoryJournal()
    audit = MemoryAudit()
    pub = MemoryPublisher()
    prov: dict[Role, LLMProvider] = {
        Role.PLANNER: planner or FakeLLM(),
        Role.CODER: FakeLLM(),
        Role.CHAT: FakeLLM(),
        Role.QUARANTINED: quarantine or FakeLLM(),
    }
    llm = GuardedLLM(prov, spend=spend or SpendLedger(), call_log=journal, clock=clock)
    bg = BudgetGuard(clock)
    tools = ToolServer(research, audit=audit, budget=bg, call_log=journal, clock=clock)
    s = settings or LoopSettings()
    s.default_budget.CopyFrom(budget or default_budget(max_trials_per_round=5, max_rounds=10))
    ids = _ids()
    coder = Coder(llm, sandbox or PassingSandbox(), journal, audit, ids=ids, clock=clock)
    loop = AnalystLoop(
        tools=tools,
        planner=Planner(llm),
        quarantine=Quarantine(llm),
        journal=journal,
        publisher=pub,
        audit=audit,
        budget=bg,
        settings=s,
        symbols=StaticSymbols(symbols or {"AAPL", "MSFT", "NVDA"}, {"inst-aapl-0001"}),
        clock=clock,
        ids=ids,
        coder=coder.propose,
    )
    return Harness(research, journal, audit, pub, llm, prov, bg, tools, loop, clock)
