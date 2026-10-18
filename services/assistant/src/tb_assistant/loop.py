"""Analyst loop controller (AN-001..AN-004, AN-013, AN-020..AN-022, AN-040..AN-042, AN-060..AN-063).

A deterministic orchestrator owns control flow; the LLM only fills in a validated diagnosis and plan.
One round: collect reports by ID through the tool server → quarantine untrusted notes → masked
planner call → evidence and schema validation → deterministic stop rules → journal (before any
launch) → approve or auto-launch per autonomy level within budget → publish. Launched jobs report
back on the bus with the round ID; when the last one finishes, a continuation round evaluates the
deflated outcome and decides whether to go on.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from google.protobuf import json_format
from google.protobuf.message import Message

from tb.analyst.v1 import analyst_pb2
from tb.ml.v1 import ml_pb2, ml_service_pb2, training_pb2
from tb.optimizer.v1 import optimizer_pb2
from tb_assistant import configs
from tb_assistant.audit import AuditRecord, AuditSink
from tb_assistant.budget import BudgetExceededError, BudgetGuard
from tb_assistant.digest import (
    EvidenceTable,
    digest_cost,
    digest_cumulative,
    digest_diagnostics,
    digest_feedback,
    digest_gate,
    digest_ledger,
    digest_regimes,
    digest_run,
    digest_study,
    digest_study_diagnostics,
)
from tb_assistant.guarded import OutputInvalidError, SpendCapError
from tb_assistant.journal import Journal, LoopRecord
from tb_assistant.llm.base import LLMError
from tb_assistant.masking import EgressBlockedError, EgressGuard, Masker
from tb_assistant.planner import Planner, PlannerResult, build_payload
from tb_assistant.quarantine import Quarantine
from tb_assistant.research import OPT, ResearchCallError
from tb_assistant.schemas import UntrustedSummary
from tb_assistant.tools import ToolContext, ToolDeniedError, ToolServer, caller
from tbcore.strategy.api import new_ulid

log = logging.getLogger("tb_assistant.loop")

STOP_DECISIONS = frozenset(
    {
        analyst_pb2.ROUND_DECISION_PLATEAU,
        analyst_pb2.ROUND_DECISION_STOP_NO_EDGE,
        analyst_pb2.ROUND_DECISION_STOP_BUDGET,
        analyst_pb2.ROUND_DECISION_STOP_UNREACHABLE,
        analyst_pb2.ROUND_DECISION_STOP_FAILURES,
        analyst_pb2.ROUND_DECISION_STOP_PAUSED,
    }
)
LAUNCH_TOOLS = frozenset({"launch_training", "launch_ablation", "launch_study"})


@dataclass
class Trigger:
    kind: int
    lineage_id: str
    trigger_id: str
    run_id: str = ""
    study_id: str = ""
    candidate_id: str = ""
    strategy_id: str = ""
    report_id: str = ""
    analyst_round_id: str = ""
    job_id: str = ""
    failed: bool = False
    correlation_id: str = ""
    question: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class LoopSettings:
    plateau_k: int = 3
    plateau_epsilon: float = 0.05
    no_edge_min_trials: int = 20
    no_edge_max_dsr: float = 0.5
    no_edge_min_pbo: float = 0.5
    unreachable_dsr: float = 0.1
    unreachable_min_trials: int = 10
    max_failed_rounds: int = 2
    max_active_loops: int = 3
    default_budget: analyst_pb2.Budget = field(default_factory=analyst_pb2.Budget)
    promotion_limits: analyst_pb2.PromotionLimits = field(default_factory=analyst_pb2.PromotionLimits)


class Publisher(Protocol):
    async def publish(self, subject: str, msg: Message) -> None: ...
    async def alert(self, severity: str, subject: str, message: str, dedupe_key: str) -> None: ...


class SymbolSource(Protocol):
    async def known(self) -> tuple[set[str], set[str]]:
        """(tickers, instrument IDs) the masker and egress guard must hide."""
        ...


class StaticSymbols:
    def __init__(self, symbols: set[str] | None = None, instrument_ids: set[str] | None = None) -> None:
        self.symbols, self.ids = set(symbols or ()), set(instrument_ids or ())

    async def known(self) -> tuple[set[str], set[str]]:
        return set(self.symbols), set(self.ids)


def ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def anchor_for(round_id: str, started_at_ns: int) -> datetime:
    """Per-round masking anchor: the round's start shifted by a round-specific 0..364 days, so day
    offsets cannot be turned back into calendar dates even by a model that knows today's date."""
    shift = int(hashlib.sha256(round_id.encode()).hexdigest()[:8], 16) % 365
    return datetime.fromtimestamp(started_at_ns / 1e9, tz=UTC) + timedelta(days=shift)


def _refs(trig: Trigger) -> list[str]:
    out = []
    for kind, v in (
        ("run", trig.run_id),
        ("study", trig.study_id),
        ("candidate", trig.candidate_id),
        ("strategy", trig.strategy_id),
        ("report", trig.report_id),
        ("job", trig.job_id),
    ):
        if v:
            out.append(f"{kind}:{v}")
    return out


def _ref(rnd: analyst_pb2.AnalystRound, kind: str) -> str:
    for r in rnd.trigger_ref_ids:
        if r.startswith(kind + ":"):
            return str(r.split(":", 1)[1])
    return ""


class AnalystLoop:
    def __init__(
        self,
        *,
        tools: ToolServer,
        planner: Planner,
        quarantine: Quarantine,
        journal: Journal,
        publisher: Publisher,
        audit: AuditSink,
        budget: BudgetGuard,
        settings: LoopSettings,
        symbols: SymbolSource | None = None,
        account_ids: set[str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        ids: Callable[[], str] | None = None,
        coder: Callable[[analyst_pb2.AnalystRound, analyst_pb2.PlanAction], Awaitable[str]] | None = None,
    ) -> None:
        self.tools = tools
        self.planner = planner
        self.quarantine = quarantine
        self.journal = journal
        self.pub = publisher
        self.audit = audit
        self.budget = budget
        self.s = settings
        self.symbols = symbols or StaticSymbols()
        self.account_ids = set(account_ids or ())
        self.clock = clock
        self.ids = ids or _ulid_factory(clock)
        self.coder = coder

    # ------------------------------------------------------------------ triggers

    async def on_trigger(self, trig: Trigger) -> analyst_pb2.AnalystRound | None:
        if not trig.lineage_id or not await self.journal.mark_trigger(trig.trigger_id):
            return None  # AN-001: idempotent on the trigger ID
        loop = await self.journal.get_loop(trig.lineage_id)
        previous: analyst_pb2.AnalystRound | None = None
        job_key = trig.job_id or trig.study_id or trig.run_id
        if job_key and job_key in loop.pending:
            rid, aid = loop.pending.pop(job_key)
            previous = await self.journal.get_round(rid) if rid else None
            if previous is not None:
                self._mark_done(previous, aid, trig)
                await self.journal.save_round(previous)
            self.budget.record_done(trig.lineage_id, job_key)
            if any(v[0] == rid for v in loop.pending.values()):
                await self.journal.save_loop(loop)
                return None  # wait for the round's other launches
            if previous is not None:
                trig.kind = analyst_pb2.ROUND_TRIGGER_CONTINUATION
        elif trig.analyst_round_id:
            # A sub-run of an analyst launch (e.g. one trial of an analyst study): the launch's own
            # completion event drives the continuation.
            return None
        if loop.paused:
            await self.journal.save_loop(loop)
            return None
        external = trig.kind != analyst_pb2.ROUND_TRIGGER_CONTINUATION
        if external and loop.pending:
            # One open round per lineage (AN-001): external results join the next continuation.
            await self.journal.save_loop(loop)
            return None
        if external:
            active = [x for x in await self.journal.list_loops() if x.lineage_id != loop.lineage_id and x.pending]
            if len(active) >= self.s.max_active_loops:  # AN-004
                await self.pub.alert(
                    "info",
                    "analyst loop deferred",
                    f"lineage {loop.lineage_id}: {len(active)} loops active",
                    f"analyst-defer-{loop.lineage_id}",
                )
                await self.journal.save_loop(loop)
                return None
            loop = self._new_loop(loop)
        return await self._run_round(trig, loop, previous)

    def _new_loop(self, old: LoopRecord) -> LoopRecord:
        rec = LoopRecord(
            lineage_id=old.lineage_id, paused=old.paused, budget_json=old.budget_json, strategy_id=old.strategy_id
        )
        self.budget.reset_loop(old.lineage_id)
        return rec

    def _mark_done(self, rnd: analyst_pb2.AnalystRound, action_id: str, trig: Trigger) -> None:
        for a in rnd.plan.actions:
            if (action_id and a.action_id == action_id) or (
                not action_id and a.status == analyst_pb2.ACTION_STATUS_LAUNCHED
            ):
                if trig.run_id:
                    a.launched_ids.append(f"run:{trig.run_id}")
                if trig.study_id:
                    a.launched_ids.append(f"study:{trig.study_id}")
                if trig.candidate_id:
                    a.launched_ids.append(f"candidate:{trig.candidate_id}")
                a.status = analyst_pb2.ACTION_STATUS_FAILED if trig.failed else analyst_pb2.ACTION_STATUS_COMPLETED
                if action_id:
                    break

    # ------------------------------------------------------------------ one round

    async def _run_round(
        self, trig: Trigger, loop: LoopRecord, previous: analyst_pb2.AnalystRound | None
    ) -> analyst_pb2.AnalystRound:
        now = self.clock()
        round_id = self.ids()
        correlation = trig.correlation_id or round_id
        budget = loop.budget() if loop.budget_json else self.s.default_budget
        self.budget.begin_round(trig.lineage_id, round_id)
        loop.rounds += 1
        rnd = analyst_pb2.AnalystRound(
            round_id=round_id,
            lineage_id=trig.lineage_id,
            seq=loop.rounds,
            trigger=analyst_pb2.RoundTrigger.ValueType(trig.kind),
            trigger_ref_ids=_refs(trig),
            started_at=ns(now),
            correlation_id=correlation,
        )
        if previous is not None:
            rnd.trigger_ref_ids.append(f"previous:{previous.round_id}")
            for a in previous.plan.actions:
                rnd.trigger_ref_ids.extend(x for x in a.launched_ids if x not in rnd.trigger_ref_ids)
        ctx = ToolContext(trig.lineage_id, round_id, correlation, budget, paused=loop.paused)
        try:
            lineage = await self.tools.lineage(trig.lineage_id)
        except ResearchCallError as exc:
            return await self._escalate(rnd, loop, f"lineage unavailable: {exc}")
        rnd.cycle_id = lineage.current_cycle_id
        rnd.autonomy_level = lineage.autonomy_level
        table = EvidenceTable()
        current, notes = await self._collect(ctx, trig, rnd, table, lineage)
        rnd.input_report_ids.extend(sorted(table.report_ids))
        trials_total = int(table.get("ledger.trials_total") or 0)
        if loop.trials_at_start < 0:
            loop.trials_at_start = trials_total
        summaries = await self._quarantine(notes, trig, round_id)

        masker = Masker(anchor=anchor_for(round_id, rnd.started_at))
        syms, iids = await self.symbols.known()
        masker.learn(syms, iids)
        guard = EgressGuard(symbols=masker.symbols, instrument_ids=masker.instrument_ids, account_ids=self.account_ids)
        level = int(lineage.autonomy_level)
        allowed = set(self.tools.action_tools_for_level(max(level, 1))) | {"write_code_proposal"}
        history = [{"round": i + 1, "best_dsr": v} for i, v in enumerate(loop.best_dsr_history)]
        payload = build_payload(
            lineage_id=trig.lineage_id,
            trigger=analyst_pb2.RoundTrigger.Name(trig.kind).removeprefix("ROUND_TRIGGER_").lower(),
            level=level,
            action_tools=sorted(allowed),
            table=table,
            current=current,
            history=history,
            notes=summaries,
            budget=json_format.MessageToDict(budget, preserving_proto_field_name=True),
            live_feedback_round=trig.kind == analyst_pb2.ROUND_TRIGGER_LIVE_FEEDBACK,
        )
        if trig.question:
            payload["owner_question"] = trig.question[:2000]
        try:
            pr = await self.planner.plan(
                payload=payload,
                table=table,
                masker=masker,
                guard=guard,
                lineage_id=trig.lineage_id,
                round_id=round_id,
                allowed_tools=allowed,
                ids=self.ids,
            )
        except (OutputInvalidError, EgressBlockedError, SpendCapError, LLMError) as exc:
            if isinstance(exc, EgressBlockedError):
                await self.pub.alert("warning", "analyst egress blocked", str(exc), f"analyst-egress-{trig.lineage_id}")
            rnd.outcome.CopyFrom(self._outcome(table))
            return await self._escalate(rnd, loop, f"{type(exc).__name__}: {exc}")
        rnd.diagnosis.CopyFrom(pr.diagnosis)
        rnd.plan.CopyFrom(pr.plan)
        rnd.provider, rnd.model = pr.record.provider, pr.record.model
        rnd.prompt_hashes.append(pr.record.prompt_sha256)
        rnd.reasoning = pr.output.reasoning
        if pr.output.preregistration is not None:
            p = pr.output.preregistration
            reg = analyst_pb2.PreRegistration(
                hypothesis=p.hypothesis, metric=p.metric, universe=p.universe, period=p.period, stop_rule=p.stop_rule
            )
            reg.prereg_hash = hashlib.sha256(json.dumps(p.model_dump(), sort_keys=True).encode()).hexdigest()
            rnd.preregistration.CopyFrom(reg)
        for t in pr.denied_tools:
            await self.audit.append(
                AuditRecord(
                    "analyst",
                    "analyst.tool.denied",
                    trig.lineage_id,
                    {"tool": t, "round_id": round_id, "reason": "requested by the planner; not a tool"},
                    correlation,
                )
            )
        rnd.outcome.CopyFrom(self._outcome(table))
        decision = self._decide(rnd, loop, table, pr, budget, previous=previous)
        rnd.decision = analyst_pb2.RoundDecision.ValueType(decision)
        rnd.budget_used.CopyFrom(self.budget.usage_proto(trig.lineage_id))
        await self.journal.save_round(rnd)  # AN-003: journaled before anything launches

        level = await self.tools.level(trig.lineage_id)  # read again right before acting (AN-060)
        rnd.autonomy_level = level
        if decision not in {analyst_pb2.ROUND_DECISION_STOP_NO_EDGE, analyst_pb2.ROUND_DECISION_STOP_PAUSED}:
            # A gate-passed challenger goes to shadow even when the search itself stops (level >= 1).
            await self._registry_steps(ctx, rnd, level, budget)
        if decision not in STOP_DECISIONS:
            if level == 0:
                rnd.decision = (
                    analyst_pb2.ROUND_DECISION_AWAITING_APPROVAL
                    if len(rnd.plan.actions)
                    else analyst_pb2.ROUND_DECISION_PLATEAU
                )
                if rnd.decision == analyst_pb2.ROUND_DECISION_AWAITING_APPROVAL:
                    await self.pub.publish(
                        "analyst.plan.ready",
                        analyst_pb2.PlanReady(
                            lineage_id=trig.lineage_id,
                            round_id=round_id,
                            action_ids=[a.action_id for a in rnd.plan.actions],
                            occurred_at=ns(self.clock()),
                            correlation_id=correlation,
                        ),
                    )
            else:
                launched, stopped_by_budget = await self._launch_all(ctx, rnd, loop, budget)
                if launched:
                    rnd.decision = analyst_pb2.ROUND_DECISION_CONTINUE
                elif stopped_by_budget:
                    rnd.decision = analyst_pb2.ROUND_DECISION_STOP_BUDGET
                else:
                    rnd.decision = analyst_pb2.ROUND_DECISION_PLATEAU
                    rnd.reasoning = (rnd.reasoning + " No executable action remained.").strip()
        return await self._finish(rnd, loop, table, trials_total)

    # ------------------------------------------------------------------ evidence

    async def _read(self, ctx: ToolContext, name: str, args: dict[str, Any] | None = None) -> Any:
        try:
            return await self.tools.call(ctx, name, args or {})
        except (ResearchCallError, ToolDeniedError) as exc:
            log.info("evidence read failed", extra={"tool": name, "error": str(exc)[:200]})
            return None

    async def _collect(
        self,
        ctx: ToolContext,
        trig: Trigger,
        rnd: analyst_pb2.AnalystRound,
        table: EvidenceTable,
        lineage: optimizer_pb2.Lineage,
    ) -> tuple[dict[str, Any], list[str]]:
        current: dict[str, Any] = {}
        notes: list[str] = list(trig.notes)
        run_ids = [trig.run_id] if trig.run_id else []
        study_ids = [trig.study_id] if trig.study_id else []
        cand_ids = [trig.candidate_id] if trig.candidate_id else []
        for ref in rnd.trigger_ref_ids:
            kind, _, val = ref.partition(":")
            if kind == "run" and val not in run_ids:
                run_ids.append(val)
            elif kind == "study" and val not in study_ids:
                study_ids.append(val)
            elif kind == "candidate" and val not in cand_ids:
                cand_ids.append(val)
        base_cfg: training_pb2.TrainingConfig | None = None
        base_run = False

        async def read_run(rid: str) -> None:
            nonlocal base_cfg, base_run
            r = await self._read(ctx, "get_training_diagnostics", {"run_id": rid})
            if r is not None and isinstance(r.message, training_pb2.DiagnosticsReport):
                digest_diagnostics(table, r.message)
            r = await self._read(ctx, "get_run", {"run_id": rid})
            if r is not None and isinstance(r.message, training_pb2.TrainingRun):
                digest_run(table, r.message)
                notes.extend(r.message.notes)
                if not base_run:
                    base_cfg, base_run = r.message.config, True
                    if f"base_run:{rid}" not in rnd.trigger_ref_ids:
                        rnd.trigger_ref_ids.append(f"base_run:{rid}")

        for rid in run_ids[:6]:
            await read_run(rid)
        base_spec: optimizer_pb2.StudySpec | None = None
        study_cands: list[str] = []
        for sid in study_ids[:4]:
            r = await self._read(ctx, "get_study", {"study_id": sid})
            if r is not None and isinstance(r.message, optimizer_pb2.Study):
                digest_study(table, r.message)
                base_spec = base_spec or r.message.spec
                if r.message.candidate_id:
                    study_cands.append(r.message.candidate_id)
                    if r.message.candidate_id not in cand_ids:
                        cand_ids.append(r.message.candidate_id)
                if base_cfg is None and r.message.spec.HasField("base_config"):
                    base_cfg = r.message.spec.base_config
            r = await self._read(ctx, "get_study_diagnostics", {"study_id": sid})
            if r is not None and isinstance(r.message, optimizer_pb2.StudyDiagnostics):
                digest_study_diagnostics(table, r.message)
        if not base_run:
            # The study's refit candidate (or else the champion) is the base for training and ablation diffs.
            wanted = study_cands or [lineage.champion_candidate_id]
            r = await self._read(ctx, "list_candidates")
            if r is not None and isinstance(r.message, optimizer_pb2.ListCandidatesResponse):
                with_runs = [c for c in r.message.candidates if c.run_id]
                pick = next((c for c in with_runs if c.candidate_id in wanted), with_runs[0] if with_runs else None)
                if pick is not None:
                    await read_run(pick.run_id)
        if trig.strategy_id:
            r = await self._read(ctx, "get_live_feedback", {"strategy_id": trig.strategy_id})
            if r is not None and r.message is not None:
                digest_feedback(table, r.message)
        if (
            lineage.champion_candidate_id
            and lineage.champion_candidate_id not in cand_ids
            and (trig.strategy_id or not cand_ids)
        ):
            cand_ids.append(lineage.champion_candidate_id)
        for cid in cand_ids[:3]:
            r = await self._read(ctx, "get_gate_report", {"candidate_id": cid})
            if r is not None and isinstance(r.message, optimizer_pb2.GateReport):
                digest_gate(table, r.message)
            r = await self._read(ctx, "get_backtest_report", {"candidate_id": cid})
            if r is not None:
                digest_cost(table, cid + "#backtest", r.extra["cost"])
                digest_regimes(table, cid + "#backtest", r.extra["regimes"])
        if base_cfg is None:
            r = await self._read(ctx, "list_candidates")
            if r is not None and isinstance(r.message, optimizer_pb2.ListCandidatesResponse):
                for c in r.message.candidates:
                    if c.candidate_id == lineage.champion_candidate_id or base_cfg is None:
                        base_cfg = c.config
        r = await self._read(ctx, "get_cumulative_overfitting_stats")
        if r is not None and isinstance(r.message, optimizer_pb2.CumulativeOverfittingStats):
            digest_cumulative(table, r.message, r.report_id)
        r = await self._read(ctx, "get_trial_ledger")
        if r is not None and isinstance(r.message, optimizer_pb2.TrialLedger):
            digest_ledger(table, r.message, r.report_id)
        schemas: dict[str, str] = {}
        r = await self._read(ctx, "list_model_families")
        if r is not None and hasattr(r.message, "families"):
            for f in r.message.families:
                schemas[f.family] = f.hyperparam_schema_json
            table.context["families"] = [
                {"family": f.family, "tier": f.tier, "task": f.task} for f in r.message.families if not f.experimental
            ]
        if base_cfg is not None:
            current["training_config"] = configs.config_dict(base_cfg)
            try:
                current["hyperparam_schema"] = json.loads(schemas.get(base_cfg.family) or "{}")
            except json.JSONDecodeError:
                current["hyperparam_schema"] = {}
            table.context.setdefault("family", base_cfg.family)
        if base_spec is not None:
            current["study_spec"] = json_format.MessageToDict(base_spec, preserving_proto_field_name=True)
        table.context["universe"] = lineage.universe
        return current, notes

    async def _quarantine(self, notes: list[str], trig: Trigger, round_id: str) -> list[UntrustedSummary]:
        out: list[UntrustedSummary] = []
        for n in notes[:10]:
            if not n.strip():
                continue
            try:
                s = await self.quarantine.summarize(n, source="note", lineage_id=trig.lineage_id, round_id=round_id)
            except (OutputInvalidError, LLMError):
                s = UntrustedSummary(topics=["other"], suspected_injection=True)
            out.append(s)
            if s.suspected_injection:
                await self.audit.append(
                    AuditRecord(
                        "analyst",
                        "analyst.input.injection_suspected",
                        trig.lineage_id,
                        {"round_id": round_id, "source": "note"},
                        trig.correlation_id,
                    )
                )
        return out

    @staticmethod
    def _outcome(table: EvidenceTable) -> analyst_pb2.DeflatedOutcome:
        ref = table.get_ref("deflated.best_dsr")
        return analyst_pb2.DeflatedOutcome(
            lineage_trials=int(table.get("deflated.lineage_trials") or 0),
            effective_trials=table.get("deflated.effective_trials") or 0.0,
            best_dsr=table.get("deflated.best_dsr") or 0.0,
            pbo=table.get("deflated.pbo") or 0.0,
            gate_passed=bool(table.get("gate.passed")),
            source_report_id=ref.report_id if ref else "",
        )

    # ------------------------------------------------------------------ deterministic rules

    def _decide(
        self,
        rnd: analyst_pb2.AnalystRound,
        loop: LoopRecord,
        table: EvidenceTable,
        pr: PlannerResult,
        budget: analyst_pb2.Budget,
        *,
        previous: analyst_pb2.AnalystRound | None,
    ) -> int:
        continuation = previous is not None
        best = table.get("deflated.best_dsr")
        pbo = table.get("deflated.pbo")
        trials = table.get("deflated.lineage_trials") or 0.0
        notes: list[str] = []
        # AN-041: deterministic no-edge rule, whatever the LLM concluded.
        forced = (
            best is not None
            and pbo is not None
            and trials >= self.s.no_edge_min_trials
            and best < self.s.no_edge_max_dsr
            and pbo >= self.s.no_edge_min_pbo
        )
        if pr.diagnosis.no_edge or forced:
            if forced and not any(f.category == analyst_pb2.FINDING_CATEGORY_NO_EDGE for f in rnd.diagnosis.findings):
                rnd.diagnosis.findings.append(self._rule_finding(table))
                rnd.diagnosis.no_edge = True
            return analyst_pb2.ROUND_DECISION_STOP_NO_EDGE
        if table.get("cycle.spent") == 1.0:
            notes.append("research cycle budget spent (EX-025)")
            rnd.reasoning = (rnd.reasoning + " " + "; ".join(notes)).strip()
            return analyst_pb2.ROUND_DECISION_STOP_BUDGET
        if self.budget.rounds_exhausted(rnd.lineage_id, budget) and loop.rounds > (budget.max_rounds or 10**9):
            return analyst_pb2.ROUND_DECISION_STOP_BUDGET
        cyc_b, cyc_u = table.get("cycle.budget") or 0.0, table.get("cycle.used") or 0.0
        # EX-023's flag needs a cross-trial variance: with a handful of trials it is degenerate.
        unreachable = table.get("deflated.significance_reachable") == 0.0 and trials >= self.s.unreachable_min_trials
        if unreachable or (best is not None and best < self.s.unreachable_dsr and cyc_b > 0 and cyc_u > cyc_b / 2):
            return analyst_pb2.ROUND_DECISION_STOP_UNREACHABLE
        if continuation:
            assert previous is not None
            launched = [a.status for a in previous.plan.actions if a.tool in LAUNCH_TOOLS and a.launched_ids]
            if launched and all(st == analyst_pb2.ACTION_STATUS_FAILED for st in launched):
                loop.failed_rounds += 1
            else:
                loop.failed_rounds = 0
            if loop.failed_rounds >= self.s.max_failed_rounds:
                return analyst_pb2.ROUND_DECISION_STOP_FAILURES
        if best is not None:
            if loop.best_dsr_history and continuation:
                if best - max(loop.best_dsr_history) < self.s.plateau_epsilon:
                    loop.no_improve += 1
                else:
                    loop.no_improve = 0
            loop.best_dsr_history.append(best)
        if pbo is not None:
            loop.pbo_history.append(pbo)
        if loop.no_improve >= self.s.plateau_k:  # AN-040
            return analyst_pb2.ROUND_DECISION_PLATEAU
        if loop.rounds >= (budget.max_rounds or 10**9):
            rnd.reasoning = (rnd.reasoning + " Loop round budget reached.").strip()
            return analyst_pb2.ROUND_DECISION_STOP_BUDGET
        return analyst_pb2.ROUND_DECISION_CONTINUE

    def _rule_finding(self, table: EvidenceTable) -> analyst_pb2.Finding:
        refs = [table.get_ref(m) for m in ("deflated.best_dsr", "deflated.pbo", "deflated.lineage_trials")]
        return analyst_pb2.Finding(
            finding_id=self.ids(),
            category=analyst_pb2.FINDING_CATEGORY_NO_EDGE,
            severity=analyst_pb2.SEVERITY_HIGH,
            evidence=[
                analyst_pb2.EvidenceRef(report_id=r.report_id, metric=r.metric, value=r.value) for r in refs if r
            ],
            explanation="Deterministic no-edge rule (AN-041): enough trials, low deflated Sharpe probability, "
            "high PBO.",
        )

    # ------------------------------------------------------------------ acting

    async def _registry_steps(
        self, ctx: ToolContext, rnd: analyst_pb2.AnalystRound, level: int, budget: analyst_pb2.Budget
    ) -> None:
        """Level 1: deploy gate-passed (validated) challengers to shadow. Level 2: promote a shadow
        challenger to paper champion within limits (the optimizer enforces gates and limits)."""
        if level < 1:
            return
        r = await self._read(ctx, "list_candidates")
        if r is None or not isinstance(r.message, optimizer_pb2.ListCandidatesResponse):
            return
        for c in r.message.candidates:
            if c.stage == ml_pb2.REGISTRY_STAGE_VALIDATED and not c.demoted:
                await self._system_action(
                    ctx,
                    rnd,
                    "deploy_challenger_shadow",
                    c.candidate_id,
                    f"Deploy validated challenger {c.candidate_id} to shadow",
                )
            elif (
                level >= 2
                and c.stage == ml_pb2.REGISTRY_STAGE_SHADOW
                and not c.demoted
                and c.shadow_sessions >= (self.s.promotion_limits.min_shadow_sessions or 10)
            ):
                await self._system_action(
                    ctx,
                    rnd,
                    "promote_paper_champion",
                    c.candidate_id,
                    f"Promote shadow challenger {c.candidate_id} to paper champion",
                )

    async def _system_action(
        self, ctx: ToolContext, rnd: analyst_pb2.AnalystRound, tool: str, candidate_id: str, desc: str
    ) -> None:
        act = analyst_pb2.PlanAction(
            action_id=self.ids(),
            rank=0,
            category=analyst_pb2.ACTION_CATEGORY_UNSPECIFIED,
            description=desc,
            target="none",
            tool=tool,
            status=analyst_pb2.ACTION_STATUS_PROPOSED,
        )
        try:
            await self.tools.call(ctx, tool, {"candidate_id": candidate_id})
            act.status = analyst_pb2.ACTION_STATUS_COMPLETED
            act.launched_ids.append(f"candidate:{candidate_id}")
        except ToolDeniedError as exc:
            act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, exc.reason
        except ResearchCallError as exc:
            act.status, act.status_reason = analyst_pb2.ACTION_STATUS_FAILED, str(exc)[:300]
        rnd.plan.actions.append(act)

    async def _prepare(
        self, ctx: ToolContext, rnd: analyst_pb2.AnalystRound, act: analyst_pb2.PlanAction
    ) -> tuple[dict[str, Any], tuple[int, float]]:
        """Turn a validated plan action into concrete tool arguments (AN-011)."""
        diff = json.loads(act.config_diff_json or "{}")
        anchor = anchor_for(rnd.round_id, rnd.started_at)
        base_run_id = _ref(rnd, "base_run")
        trials = max(1, act.trial_cost)
        if act.tool in {"launch_training", "launch_ablation"}:
            if not base_run_id:
                raise configs.DiffError("no base run for a training diff")
            r = await self.tools.call(ctx, "get_run", {"run_id": base_run_id})
            assert isinstance(r.message, training_pb2.TrainingRun)
            base = r.message.config
            if act.tool == "launch_ablation":
                variant = configs.ablation_variant(f"{act.category}-{act.action_id[-6:]}", diff)
                return {"run_id": base_run_id, "variants": [variant], "reason": act.description}, (
                    trials,
                    act.est_compute_hours,
                )
            cfg = configs.apply_training_diff(base, diff, anchor=anchor, name_suffix=act.action_id[-6:])
            d = await self.tools.call(ctx, "draft_training_config", {"config": cfg})
            if d.extra.get("errors"):
                raise configs.DiffError("; ".join(d.extra["errors"])[:300])
            est = d.extra.get("estimate")
            cpu = (est.cpu_seconds / 3600.0) if est is not None and est.cpu_seconds else act.est_compute_hours
            return {"config": cfg, "reason": act.description}, (trials, cpu)
        if act.tool == "launch_study":
            base_spec = await self._base_study_spec(ctx, rnd)
            fams = await self.tools.call(ctx, "list_model_families")
            schemas = {f.family: f.hyperparam_schema_json for f in getattr(fams.message, "families", [])}
            prereg = optimizer_pb2.PreRegistration(
                hypothesis=rnd.preregistration.hypothesis or act.hypothesis or act.description,
                metric=base_spec.objective.metric or "sharpe",
                universe=rnd.preregistration.universe or base_spec.preregistration.universe or base_spec.universe.name,
                stop_rule=rnd.preregistration.stop_rule or "plateau or budget",
            )
            prereg.period.CopyFrom(base_spec.period)
            spec = configs.apply_study_diff(
                base_spec,
                diff,
                family_schemas=schemas,
                preregistration=prereg,
                name=f"analyst-{rnd.round_id[-8:]}-{act.rank}",
                max_trials=ctx.budget.max_trials_per_round or trials,
            )
            return {"spec": spec, "reason": act.description}, (
                max(trials, spec.budget.max_trials),
                act.est_compute_hours,
            )
        raise configs.DiffError(f"tool {act.tool} is not a launch tool")

    async def _base_study_spec(self, ctx: ToolContext, rnd: analyst_pb2.AnalystRound) -> optimizer_pb2.StudySpec:
        """The triggering study's spec, or a minimal spec around the base run's config."""
        sid = _ref(rnd, "study")
        if sid:
            sr = await self.tools.call(ctx, "get_study", {"study_id": sid})
            if isinstance(sr.message, optimizer_pb2.Study):
                return sr.message.spec
        run_id = _ref(rnd, "base_run")
        if not run_id:
            raise configs.DiffError("no base study or run for a study diff")
        r = await self.tools.call(ctx, "get_run", {"run_id": run_id})
        assert isinstance(r.message, training_pb2.TrainingRun)
        cfg = r.message.config
        spec = optimizer_pb2.StudySpec(
            lineage_id=cfg.lineage_id, families=[cfg.family], timeframe=cfg.timeframe, seed=cfg.seed
        )
        spec.base_config.CopyFrom(cfg)
        spec.period.CopyFrom(cfg.period)
        spec.universe.CopyFrom(cfg.universe)
        spec.feature_sets.append(cfg.feature_set)
        spec.label_specs.append(cfg.label)
        spec.objective.metric, spec.objective.direction = "sharpe", "maximize"
        spec.sampler = "tpe"
        return spec

    async def _launch_all(
        self,
        ctx: ToolContext,
        rnd: analyst_pb2.AnalystRound,
        loop: LoopRecord,
        budget: analyst_pb2.Budget,
        *,
        only: set[str] | None = None,
    ) -> tuple[int, bool]:
        launched, budget_stop = 0, False
        for act in rnd.plan.actions:
            if only is not None and act.action_id not in only:
                continue
            if act.status not in (analyst_pb2.ACTION_STATUS_PROPOSED, analyst_pb2.ACTION_STATUS_APPROVED):
                continue
            if act.tool == "none":
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, "recommendation only"
                continue
            if act.tool == "write_code_proposal":
                if self.coder is None:
                    act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, "coder not configured"
                    continue
                pid = await self.coder(rnd, act)
                act.status = analyst_pb2.ACTION_STATUS_COMPLETED
                act.launched_ids.append(f"proposal:{pid}")
                continue
            try:
                args, cost = await self._prepare(ctx, rnd, act)
                res = await self.tools.call(ctx, act.tool, args, cost=cost)
            except configs.DiffError as exc:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, f"invalid diff: {exc}"[:300]
                continue
            except BudgetExceededError as exc:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, f"budget: {exc.reason}"[:300]
                budget_stop = budget_stop or launched == 0
                continue
            except ToolDeniedError as exc:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, f"denied: {exc.reason}"[:300]
                if "cycle" in exc.reason.lower() or "budget" in exc.reason.lower():
                    budget_stop = budget_stop or launched == 0
                continue
            except ResearchCallError as exc:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_FAILED, str(exc)[:300]
                if exc.code in {"FAILED_PRECONDITION", "RESOURCE_EXHAUSTED"}:
                    budget_stop = budget_stop or launched == 0
                continue
            act.status = analyst_pb2.ACTION_STATUS_LAUNCHED
            job_ids = list(res.extra.get("job_ids", []))
            act.launched_ids.extend(f"job:{j}" for j in job_ids)
            for j in job_ids:
                loop.pending[j] = [rnd.round_id, act.action_id]
            await self.budget.record_launch(ctx.lineage_id, budget, job_ids=job_ids, trials=cost[0], cpu_hours=cost[1])
            launched += 1
        return launched, budget_stop

    # ------------------------------------------------------------------ finishing

    async def _finish(
        self, rnd: analyst_pb2.AnalystRound, loop: LoopRecord, table: EvidenceTable, trials_total: int
    ) -> analyst_pb2.AnalystRound:
        rnd.completed_at = ns(self.clock())
        rnd.trials_added = max(0, trials_total - loop.trials_at_start) if loop.trials_at_start >= 0 else 0
        rnd.budget_used.CopyFrom(self.budget.usage_proto(rnd.lineage_id))
        loop.last_decision = rnd.decision
        loop.current_round_id = rnd.round_id
        if rnd.decision in STOP_DECISIONS:
            loop.state = analyst_pb2.LOOP_STATE_STOPPED
            for a in rnd.plan.actions:
                if a.status == analyst_pb2.ACTION_STATUS_PROPOSED:
                    a.status, a.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, "loop stopped"
            rnd.recommendation = self._recommend(rnd, table)
            rnd.summary = self._summary(rnd, loop)
            await self._stop_side_effects(rnd, loop)
        elif rnd.decision == analyst_pb2.ROUND_DECISION_AWAITING_APPROVAL:
            loop.state = analyst_pb2.LOOP_STATE_AWAITING_APPROVAL
        elif rnd.decision == analyst_pb2.ROUND_DECISION_ESCALATE_TO_OWNER:
            loop.state = analyst_pb2.LOOP_STATE_STOPPED
        else:
            loop.state = analyst_pb2.LOOP_STATE_AWAITING_RESULTS if loop.pending else analyst_pb2.LOOP_STATE_IDLE
        await self._self_limit(rnd, loop)
        await self.journal.save_round(rnd)
        await self.journal.save_loop(loop)
        await self.pub.publish("analyst.round.completed", analyst_pb2.RoundCompleted(round=rnd))
        await self.audit.append(
            AuditRecord(
                "analyst",
                "analyst.round.completed",
                rnd.lineage_id,
                {
                    "round_id": rnd.round_id,
                    "seq": rnd.seq,
                    "decision": analyst_pb2.RoundDecision.Name(rnd.decision),
                    "level": rnd.autonomy_level,
                    "trials_added": rnd.trials_added,
                    "prompt_sha256": list(rnd.prompt_hashes),
                    "best_dsr": rnd.outcome.best_dsr,
                },
                rnd.correlation_id,
            )
        )
        return rnd

    def _recommend(self, rnd: analyst_pb2.AnalystRound, table: EvidenceTable) -> str:
        if rnd.decision == analyst_pb2.ROUND_DECISION_STOP_NO_EDGE:
            return "retire"
        done = analyst_pb2.ACTION_STATUS_COMPLETED
        if any(a.tool == "promote_paper_champion" and a.status == done for a in rnd.plan.actions):
            return "promoted a challenger to paper champion within limits; nominate for live-eligible after graduation"
        if any(a.tool == "deploy_challenger_shadow" and a.status == done for a in rnd.plan.actions):
            return "keep champion; gated challenger deployed to shadow for comparison"
        if table.get("gate.passed") == 1.0:
            return "promote within level" if rnd.autonomy_level >= 2 else "keep champion; a challenger passed the gate"
        return "keep champion"

    def _summary(self, rnd: analyst_pb2.AnalystRound, loop: LoopRecord) -> str:
        why = analyst_pb2.RoundDecision.Name(rnd.decision).removeprefix("ROUND_DECISION_").lower()
        hist = ", ".join(f"{v:.3f}" for v in loop.best_dsr_history) or "n/a"
        return (
            f"Loop stopped after {loop.rounds} round(s): {why}. Best deflated Sharpe probability by round: {hist}. "
            f"Lineage trials: {rnd.outcome.lineage_trials} (effective {rnd.outcome.effective_trials:.1f}), "
            f"PBO {rnd.outcome.pbo:.2f}. "
            f"Trials added by this loop: {rnd.trials_added}. Recommendation: {rnd.recommendation}. "
            + (rnd.diagnosis.summary or "")
        ).strip()

    async def _stop_side_effects(self, rnd: analyst_pb2.AnalystRound, loop: LoopRecord) -> None:
        await self.pub.publish(
            "analyst.escalation",
            analyst_pb2.Escalation(
                lineage_id=rnd.lineage_id,
                round_id=rnd.round_id,
                reason=rnd.summary[:1000],
                kind="loop_stopped",
                occurred_at=ns(self.clock()),
                correlation_id=rnd.correlation_id,
            ),
        )
        if rnd.decision == analyst_pb2.ROUND_DECISION_STOP_FAILURES:
            await self.lower_autonomy(
                rnd.lineage_id, 0, pause=False, actor="analyst", reason="repeated failed rounds (AN-063)"
            )

    async def _self_limit(self, rnd: analyst_pb2.AnalystRound, loop: LoopRecord) -> None:
        """AN-063: PBO rising in two consecutive rounds to above 0.5 drops the lineage to level 0."""
        h = loop.pbo_history
        if len(h) >= 3 and h[-1] > h[-2] > h[-3] and h[-1] > 0.5 and rnd.autonomy_level > 0:
            await self.lower_autonomy(
                rnd.lineage_id, 0, pause=False, actor="analyst", reason="PBO rising above 0.5 (AN-063)"
            )

    async def _escalate(self, rnd: analyst_pb2.AnalystRound, loop: LoopRecord, reason: str) -> analyst_pb2.AnalystRound:
        rnd.decision = analyst_pb2.ROUND_DECISION_ESCALATE_TO_OWNER
        rnd.reasoning = reason[:2000]
        await self.pub.publish(
            "analyst.escalation",
            analyst_pb2.Escalation(
                lineage_id=rnd.lineage_id,
                round_id=rnd.round_id,
                reason=reason[:1000],
                kind="round_failed",
                occurred_at=ns(self.clock()),
                correlation_id=rnd.correlation_id,
            ),
        )
        await self.pub.alert(
            "warning",
            "analyst round escalated",
            f"lineage {rnd.lineage_id}: {reason[:300]}",
            f"analyst-escalate-{rnd.lineage_id}",
        )
        return await self._finish(rnd, loop, EvidenceTable(), 0)

    # ------------------------------------------------------------------ owner and monitor actions

    async def review_actions(
        self, round_id: str, action_ids: list[str], *, approve: bool, reason: str, actor: str
    ) -> tuple[analyst_pb2.ImprovementPlan, list[analyst_pb2.PreparedLaunch]]:
        """Owner approval or rejection (via api-gateway only, AN-060). At level >= 1 approved actions
        launch as the analyst. At level 0 the analyst may not launch (ML-024, EX-031): approved
        actions come back as prepared, validated requests that api-gateway submits as the owner and
        reports back with ``record_launches``."""
        rnd = await self.journal.get_round(round_id)
        if rnd is None:
            raise KeyError(round_id)
        loop = await self.journal.get_loop(rnd.lineage_id)
        wanted = set(action_ids)
        for a in rnd.plan.actions:
            if a.action_id in wanted and a.status == analyst_pb2.ACTION_STATUS_PROPOSED:
                if approve:
                    a.status = analyst_pb2.ACTION_STATUS_APPROVED
                else:
                    a.status, a.status_reason = analyst_pb2.ACTION_STATUS_REJECTED, reason[:300]
        await self.audit.append(
            AuditRecord(
                actor or "owner",
                "analyst.actions.approve" if approve else "analyst.actions.reject",
                rnd.lineage_id,
                {"round_id": round_id, "action_ids": sorted(wanted), "reason": reason[:300]},
                rnd.correlation_id,
            )
        )
        prepared: list[analyst_pb2.PreparedLaunch] = []
        if approve and not loop.paused:
            budget = loop.budget() if loop.budget_json else self.s.default_budget
            ctx = ToolContext(rnd.lineage_id, round_id, rnd.correlation_id, budget)
            level = await self.tools.level(rnd.lineage_id)
            if level >= 1:
                self.budget.begin_round(rnd.lineage_id, round_id)
                launched, _ = await self._launch_all(ctx, rnd, loop, budget, only=wanted)
                if launched:
                    rnd.decision = analyst_pb2.ROUND_DECISION_CONTINUE
                    loop.state = analyst_pb2.LOOP_STATE_AWAITING_RESULTS
            else:
                prepared = await self._prepare_for_owner(ctx, rnd, wanted)
        await self.journal.save_round(rnd)
        await self.journal.save_loop(loop)
        return rnd.plan, prepared

    async def _prepare_for_owner(
        self, ctx: ToolContext, rnd: analyst_pb2.AnalystRound, wanted: set[str]
    ) -> list[analyst_pb2.PreparedLaunch]:
        out: list[analyst_pb2.PreparedLaunch] = []
        for act in rnd.plan.actions:
            if act.action_id not in wanted or act.status != analyst_pb2.ACTION_STATUS_APPROVED:
                continue
            if act.tool not in LAUNCH_TOOLS:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, "nothing to launch"
                continue
            try:
                args, _ = await self._prepare(ctx, rnd, act)
            except (configs.DiffError, ResearchCallError, ToolDeniedError) as exc:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_SKIPPED, f"invalid diff: {exc}"[:300]
                continue
            owner = training_pb2.CallerContext(
                actor=training_pb2.ACTOR_OWNER,
                analyst_round_id=rnd.round_id,
                reason=act.description[:200],
                correlation_id=rnd.correlation_id,
            )
            req: Message
            if act.tool == "launch_training":
                kind, req = "training", ml_service_pb2.SubmitTrainingJobRequest(config=args["config"], caller=owner)
            elif act.tool == "launch_ablation":
                kind = "ablation"
                req = ml_service_pb2.SubmitAblationRequest(
                    run_id=args["run_id"], variants=args["variants"], caller=owner
                )
            else:
                kind, req = "study", optimizer_pb2.CreateStudyRequest(spec=args["spec"], caller=owner)
            out.append(
                analyst_pb2.PreparedLaunch(
                    round_id=rnd.round_id, action_id=act.action_id, kind=kind, request=req.SerializeToString()
                )
            )
        return out

    async def record_launches(
        self, round_id: str, launches: list[analyst_pb2.LaunchRecord], *, actor: str
    ) -> analyst_pb2.ImprovementPlan:
        """The owner's submissions of approved actions (level 0): map jobs to the round so their
        completions run the continuation round."""
        rnd = await self.journal.get_round(round_id)
        if rnd is None:
            raise KeyError(round_id)
        loop = await self.journal.get_loop(rnd.lineage_id)
        by_id = {a.action_id: a for a in rnd.plan.actions}
        for rec in launches:
            act = by_id.get(rec.action_id)
            if act is None or act.status != analyst_pb2.ACTION_STATUS_APPROVED:
                continue
            if rec.error or not rec.job_ids:
                act.status, act.status_reason = analyst_pb2.ACTION_STATUS_FAILED, (rec.error or "no job")[:300]
                continue
            act.status = analyst_pb2.ACTION_STATUS_LAUNCHED
            for j in rec.job_ids:
                act.launched_ids.append(f"job:{j}")
                loop.pending[j] = [round_id, act.action_id]
        if loop.pending:
            rnd.decision = analyst_pb2.ROUND_DECISION_CONTINUE
            loop.state = analyst_pb2.LOOP_STATE_AWAITING_RESULTS
        await self.audit.append(
            AuditRecord(
                actor or "owner",
                "analyst.actions.launched",
                rnd.lineage_id,
                {"round_id": round_id, "launches": [list(r.job_ids) for r in launches]},
                rnd.correlation_id,
            )
        )
        await self.journal.save_round(rnd)
        await self.journal.save_loop(loop)
        return rnd.plan

    async def set_paused(self, lineage_id: str, paused: bool, *, actor: str, reason: str) -> list[LoopRecord]:
        loops = [await self.journal.get_loop(lineage_id)] if lineage_id else await self.journal.list_loops()
        for loop in loops:
            loop.paused = paused
            loop.state = analyst_pb2.LOOP_STATE_PAUSED if paused else analyst_pb2.LOOP_STATE_IDLE
            await self.journal.save_loop(loop)
            await self.audit.append(
                AuditRecord(
                    actor,
                    "analyst.loop.pause" if paused else "analyst.loop.resume",
                    loop.lineage_id,
                    {"reason": reason[:300]},
                )
            )
        return loops

    async def lower_autonomy(
        self, lineage_id: str, level: int, *, pause: bool, actor: str, reason: str, evidence: str = ""
    ) -> int:
        """AN-063: lower only. Asks the optimizer (which allows the analyst to lower, EX-033)."""
        current = await self.tools.level(lineage_id)
        new = min(current, max(0, level))
        if new < current:
            try:
                await self.tools.api.call(
                    OPT,
                    "SetLineageAutonomy",
                    optimizer_pb2.SetLineageAutonomyRequest(
                        lineage_id=lineage_id,
                        level=new,
                        caller=caller(ToolContext(lineage_id, "", "", self.s.default_budget), reason[:200]),
                    ),
                )
            except ResearchCallError as exc:
                log.error("could not lower autonomy", extra={"lineage_id": lineage_id, "error": str(exc)[:200]})
        if pause:
            await self.set_paused(lineage_id, True, actor=actor, reason=reason)
        await self.audit.append(
            AuditRecord(
                actor,
                "analyst.autonomy.lower",
                lineage_id,
                {"from": current, "to": new, "pause": pause, "reason": reason[:300], "evidence": evidence},
            )
        )
        await self.pub.publish(
            "analyst.escalation",
            analyst_pb2.Escalation(
                lineage_id=lineage_id,
                reason=f"autonomy lowered {current}->{new}: {reason}"[:1000],
                kind="autonomy_lowered",
                occurred_at=ns(self.clock()),
            ),
        )
        return new


def _ulid_factory(clock: Callable[[], datetime]) -> Callable[[], str]:
    n = [0]

    def nxt() -> str:
        n[0] += 1
        return new_ulid(int(clock().timestamp() * 1000), seed=f"analyst-{n[0]}-{id(n)}")

    return nxt
