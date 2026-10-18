"""Gate 4 analyst-loop checks against the real section 10/11 services (master prompt Gate 4; AN-001..AN-063).

The assistant's loop, tool server, and gRPC client talk over signed gRPC to the real ``MLService``,
``OptimizerService``, and ``HoldoutService`` (in-process servers sharing one document store, as the
``research`` schema is shared in production). Data are seeded synthetic universes; the planner is the
deterministic reference provider. Bus events are delivered from the services' publishers to the loop the
way the NATS consumers do.

Synthetic studies this small do not pass the promotion gate (DSR, PBO), so the shadow-deployment check
forces a passing gate report, as the optimizer's own transition tests do; everything else is real.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import grpc
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from google.protobuf.message import Message
from tb_assistant.audit import MemoryAudit
from tb_assistant.budget import BudgetGuard, default_budget
from tb_assistant.bus import (
    FeedbackCache,
    trigger_from_backtest,
    trigger_from_feedback,
    trigger_from_run,
    trigger_from_study,
)
from tb_assistant.eval.harness import MemoryPublisher
from tb_assistant.guarded import GuardedLLM, SpendLedger
from tb_assistant.journal import LoopRecord, MemoryJournal
from tb_assistant.llm.base import Role
from tb_assistant.llm.fake import FakeLLM
from tb_assistant.loop import AnalystLoop, LoopSettings, StaticSymbols, Trigger
from tb_assistant.planner import Planner
from tb_assistant.quarantine import Quarantine
from tb_assistant.research import ML, OPT, GrpcResearch, ResearchCallError
from tb_assistant.tools import ToolContext, ToolDeniedError, ToolServer
from tb_ml.core import MLCore
from tb_ml.grpc_api import ALLOW as ML_ALLOW
from tb_ml.grpc_api import SERVICE as ML_SERVICE
from tb_ml.grpc_api import MLServicer
from tb_ml.link import GrpcLink
from tb_monitor import feedback as fb
from tb_optimizer.core import OptimizerCore
from tb_optimizer.grpc_api import HOLD, HOLD_ALLOW, OPT_ALLOW, HoldoutServicer, OptimizerServicer
from tb_optimizer.grpc_api import OPT as OPT_SERVICE

from tb.analyst.v1 import analyst_pb2 as ap
from tb.ml.v1 import ml_pb2 as mlp
from tb.ml.v1 import ml_service_pb2 as ms
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import holdout_pb2 as hp
from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.audit import MemoryAudit as ServiceAudit
from tbcore.backtest.recording import Recording
from tbcore.buspub import MemoryPublisher as ServicePublisher
from tbcore.docstore import MemoryDocStore
from tbcore.ml import synthetic
from tbcore.rpc import Client, serve
from tbcore.svcauth import Signer, Verifier

ROOT = Path(__file__).resolve().parents[2]
RECORDING = ROOT / "tests" / "fixtures" / "parity" / "orb-paper-gate.tbrec"
NOISE, EDGE = "synthetic:intraday_noise", "synthetic:intraday_edge"
OWNER = tp.CallerContext(actor=tp.ACTOR_OWNER, actor_id="owner")
STOPPED = {ap.LOOP_STATE_STOPPED, ap.LOOP_STATE_AWAITING_APPROVAL}
TIMEOUT_S = 900.0
SYNTHETIC_IDS = frozenset(
    {*synthetic.universe(synthetic.preset("intraday_edge")), *synthetic.universe(synthetic.preset("intraday_noise"))}
)


def stepup() -> tp.CallerContext:
    c = tp.CallerContext(actor=tp.ACTOR_OWNER, actor_id="owner")
    c.step_up.CopyFrom(tp.StepUp(method="passkey", verified_at=time.time_ns()))
    return c


def _key(tmp: Path, service: str) -> Signer:
    k = Ed25519PrivateKey.generate()
    (tmp / f"svc_{service.replace('-', '_')}_ed25519.pub").write_text(
        base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    )
    return Signer(service, k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))


@dataclass
class Stack:
    ocore: OptimizerCore
    mcore: MLCore
    obus: ServicePublisher
    mbus: ServicePublisher
    endpoints: dict[str, str]
    holdout_addr: str
    assistant: Signer
    og: Client
    mg: Client
    cursor: dict[str, int] = field(default_factory=lambda: {"ml": 0, "opt": 0})

    def owner_lineage(self, universe: str) -> str:
        """Owner setup through the services: lineage, research cycle, level 1 (step-up), first run."""
        lin = self.og.call(
            "CreateLineage",
            op.CreateLineageRequest(
                lineage=op.Lineage(name=universe, family="ridge", timeframe="15m", universe=universe), caller=OWNER
            ),
        ).lineage
        self.og.call("OpenCycle", op.OpenCycleRequest(lineage_id=lin.lineage_id, target_sharpe=1.0, caller=OWNER))
        self.og.call(
            "SetLineageAutonomy",
            op.SetLineageAutonomyRequest(
                lineage_id=lin.lineage_id,
                level=1,
                analyst_budget=op.Budget(max_trials=60, max_cpu_hours=5),
                caller=stepup(),
            ),
        )
        cfg = tp.TrainingConfig(family="ridge", data_source=universe, timeframe="15m", lineage_id=lin.lineage_id)
        self.mg.call("SubmitTrainingJob", ms.SubmitTrainingJobRequest(config=cfg, caller=OWNER))
        return str(lin.lineage_id)

    def events(self) -> list[tuple[str, Message]]:
        m, o = self.mbus.sent[self.cursor["ml"] :], self.obus.sent[self.cursor["opt"] :]
        self.cursor["ml"], self.cursor["opt"] = len(self.mbus.sent), len(self.obus.sent)
        return [(s, msg) for s, msg, _ in m + o]


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    tmp = tmp_path_factory.mktemp("gate4")
    assistant, gateway, ml = _key(tmp, "assistant"), _key(tmp, "api-gateway"), _key(tmp, "ml")
    store, obus, mbus = MemoryDocStore(), ServicePublisher(), ServicePublisher()
    ocore = OptimizerCore(
        store,
        tmp / "artifacts",
        holdout_store=MemoryDocStore(),
        audit=ServiceAudit(),
        bus=obus,
        deploy_registry=str(tmp / "deploy.json"),
    )
    s_opt, p_opt = serve(
        "127.0.0.1:0",
        [(OPT_SERVICE, OptimizerServicer(ocore, ServiceAudit()), OPT_ALLOW)],
        Verifier.from_secrets("optimizer", ["assistant", "api-gateway", "ml"], tmp),
    )
    s_hold, p_hold = serve(
        "127.0.0.1:0",
        [(HOLD, HoldoutServicer(ocore, ServiceAudit()), HOLD_ALLOW)],
        Verifier.from_secrets("holdout", ["assistant", "api-gateway", "ml"], tmp),
    )
    s_opt.start()
    s_hold.start()
    link = GrpcLink(f"127.0.0.1:{p_opt}", f"127.0.0.1:{p_hold}", ml)
    mcore = MLCore(store, tmp / "artifacts", link=link, audit=ServiceAudit(), bus=mbus)
    s_ml, p_ml = serve(
        "127.0.0.1:0",
        [(ML_SERVICE, MLServicer(mcore, ServiceAudit()), ML_ALLOW)],
        Verifier.from_secrets("ml", ["assistant", "api-gateway"], tmp),
    )
    s_ml.start()
    st = Stack(
        ocore=ocore,
        mcore=mcore,
        obus=obus,
        mbus=mbus,
        endpoints={ML: f"127.0.0.1:{p_ml}", OPT: f"127.0.0.1:{p_opt}"},
        holdout_addr=f"127.0.0.1:{p_hold}",
        assistant=assistant,
        og=Client(grpc.insecure_channel(f"127.0.0.1:{p_opt}"), OPT_SERVICE, gateway, "optimizer"),
        mg=Client(grpc.insecure_channel(f"127.0.0.1:{p_ml}"), ML_SERVICE, gateway, "ml"),
    )
    try:
        yield st
    finally:
        for s in (s_ml, s_opt, s_hold):
            s.stop(None)
        mcore.close()


@dataclass
class Analyst:
    """The assistant's state that outlives one event loop (journal, audit, budgets, feedback cache)."""

    journal: MemoryJournal = field(default_factory=MemoryJournal)
    audit: MemoryAudit = field(default_factory=MemoryAudit)
    pub: MemoryPublisher = field(default_factory=MemoryPublisher)
    budget: BudgetGuard = field(default_factory=lambda: BudgetGuard(lambda: datetime.now(tz=UTC)))
    cache: FeedbackCache = field(default_factory=FeedbackCache)
    symbols: StaticSymbols = field(default_factory=lambda: StaticSymbols({"AAPL", "MSFT"}, set(SYNTHETIC_IDS)))

    def build(self, research: GrpcResearch) -> tuple[AnalystLoop, ToolServer]:
        llm = GuardedLLM({r: FakeLLM() for r in Role}, spend=SpendLedger(), call_log=self.journal)
        tools = ToolServer(research, audit=self.audit, budget=self.budget, call_log=self.journal, feedback=self.cache)
        s = LoopSettings()
        s.default_budget.CopyFrom(default_budget(max_trials_per_round=5, max_rounds=8))
        loop = AnalystLoop(
            tools=tools,
            planner=Planner(llm),
            quarantine=Quarantine(llm),
            journal=self.journal,
            publisher=self.pub,
            audit=self.audit,
            budget=self.budget,
            settings=s,
            symbols=self.symbols,
        )
        return loop, tools


def _trigger(stack: Stack, subject: str, msg: Message) -> Trigger | None:
    if subject == "ml.run.completed":
        assert isinstance(msg, tp.MLRunCompleted)
        return trigger_from_run(msg)
    if subject == "optimizer.study.completed":
        assert isinstance(msg, op.StudyCompleted)
        return trigger_from_study(msg)
    if subject == "backtest.completed":
        assert isinstance(msg, tp.BacktestCompleted)
        return trigger_from_backtest(msg, stack.ocore.candidate(msg.candidate_id).lineage_id)
    return None


async def drive(stack: Stack, an: Analyst, lineage_id: str, first: Trigger | None = None) -> LoopRecord:
    """Deliver service events to the loop until the lineage's loop stops (or waits for approval)."""
    research = GrpcResearch(stack.endpoints, stack.assistant)
    loop, _ = an.build(research)
    try:
        if first is not None:
            await loop.on_trigger(first)
        deadline = time.monotonic() + TIMEOUT_S
        while time.monotonic() < deadline:
            for subject, msg in stack.events():
                t = _trigger(stack, subject, msg)
                if t is not None:
                    await loop.on_trigger(t)
            rec = await an.journal.get_loop(lineage_id)
            if rec.rounds and rec.state in STOPPED and not rec.pending:
                return rec
            await asyncio.sleep(0.5)
        raise AssertionError(f"analyst loop for {lineage_id} did not stop within {TIMEOUT_S:.0f}s")
    finally:
        await research.close()


def _lineage(stack: Stack, lineage_id: str) -> op.Lineage:
    lin = stack.ocore.lineage(lineage_id)
    assert lin is not None
    return lin


def rounds_of(an: Analyst, lineage_id: str) -> list[ap.AnalystRound]:
    """Rounds oldest first."""
    return list(reversed(asyncio.run(an.journal.list_rounds(lineage_id))))


@pytest.fixture(scope="module")
def noise(stack: Stack) -> tuple[str, Analyst]:
    lid, an = stack.owner_lineage(NOISE), Analyst()
    asyncio.run(drive(stack, an, lid))
    return lid, an


@pytest.fixture(scope="module")
def edge(stack: Stack) -> tuple[str, Analyst]:
    lid, an = stack.owner_lineage(EDGE), Analyst()
    asyncio.run(drive(stack, an, lid))
    return lid, an


def test_gate4_an041_noise_concludes_no_edge_and_the_dsr_counts_every_trial(
    stack: Stack, noise: tuple[str, Analyst]
) -> None:
    lid, an = noise
    rounds = rounds_of(an, lid)
    last = rounds[-1]
    assert last.decision == ap.ROUND_DECISION_STOP_NO_EDGE, [ap.RoundDecision.Name(r.decision) for r in rounds]
    assert len(rounds) >= 2, "the analyst searched before concluding"
    assert last.diagnosis.no_edge
    no_edge = [f for f in last.diagnosis.findings if f.category == ap.FINDING_CATEGORY_NO_EDGE]
    assert no_edge and all(f.evidence for f in no_edge)  # AN-010
    ledger, stats = stack.ocore.ledger(lid), stack.ocore.cumulative(lid)
    trials = stack.ocore.trials(lineage_id=lid)
    # The deflated outcome the analyst decided on counts every trial: owner and analyst (EX-021, AN-022).
    assert last.outcome.lineage_trials == stats.n_trials == ledger.trials_total == len(trials)
    assert ledger.by_source.get("owner", 0) >= 1 and ledger.by_source.get("analyst", 0) >= 5
    assert last.outcome.best_dsr < 0.5
    assert all(c.stage == mlp.REGISTRY_STAGE_CANDIDATE for c in stack.ocore.candidates(lid))
    assert "retire" in last.summary.lower() or "no_edge" in last.summary.lower()


def test_gate4_an040_level1_runs_preregistered_rounds_unattended_and_stops_with_a_summary(
    stack: Stack, edge: tuple[str, Analyst]
) -> None:
    lid, an = edge
    rounds = rounds_of(an, lid)
    assert len(rounds) >= 3, [ap.RoundDecision.Name(r.decision) for r in rounds]
    assert rounds[-1].decision in (ap.ROUND_DECISION_PLATEAU, ap.ROUND_DECISION_STOP_BUDGET)
    assert rounds[-1].summary.startswith("Loop stopped after")
    analyst_studies = [s for s in stack.ocore.studies(lid) if s.created_by.actor == tp.ACTOR_ANALYST]
    assert len(analyst_studies) >= 2
    for st in analyst_studies:
        pr = st.spec.preregistration
        assert pr.hypothesis and pr.stop_rule and pr.metric == (st.spec.objective.metric or "sharpe")  # EX-026
        assert st.status == op.STUDY_STATUS_COMPLETED, st.message
        assert st.created_by.analyst_round_id in {r.round_id for r in rounds}
    launched = [r for r in rounds if any(a.tool == "launch_study" and a.launched_ids for a in r.plan.actions)]
    assert all(r.preregistration.hypothesis and r.preregistration.prereg_hash for r in launched)
    ledger = stack.ocore.ledger(lid)
    assert ledger.by_source.get("analyst", 0) == sum(
        1 for t in stack.ocore.trials(lineage_id=lid) if t.source == tp.ACTOR_ANALYST
    )
    # These small synthetic studies fail the promotion gate, so nothing reached shadow (EX-051).
    for c in stack.ocore.candidates(lid):
        if not stack.ocore.gate_report(c.candidate_id, OWNER).passed:
            assert c.stage == mlp.REGISTRY_STAGE_CANDIDATE
    assert set(an.audit.actions("analyst.round.completed")) and len(an.audit.actions("analyst.round.")) >= len(rounds)


def test_gate4_an062_gated_challenger_goes_to_shadow_and_no_further(stack: Stack, edge: tuple[str, Analyst]) -> None:
    lid, an = edge
    cand = sorted(stack.ocore.candidates(lid), key=lambda c: c.created_at)[-1]
    rep = stack.ocore.store.get("gate_report", cand.gate_report_id, op.GateReport)
    assert rep is not None
    rep.passed, rep.engine, rep.leakage_passed, rep.survivorship_status = True, "event", True, "complete"
    del rep.reasons[:]
    stack.ocore.store.put("gate_report", rep.report_id, rep, cand.lineage_id, cand.candidate_id)
    stack.og.call(
        "PromoteCandidate",
        op.PromoteCandidateRequest(candidate_id=cand.candidate_id, to_stage=mlp.REGISTRY_STAGE_VALIDATED, caller=OWNER),
    )
    trig_id = f"backtest.completed:{rep.report_id}:forced"
    first = Trigger(ap.ROUND_TRIGGER_BACKTEST_COMPLETED, lid, trig_id, candidate_id=cand.candidate_id)
    asyncio.run(drive(stack, an, lid, first))
    assert stack.ocore.candidate(cand.candidate_id).stage == mlp.REGISTRY_STAGE_SHADOW
    deployed = [a for r in rounds_of(an, lid) for a in r.plan.actions if a.tool == "deploy_challenger_shadow"]
    assert any(a.status == ap.ACTION_STATUS_COMPLETED for a in deployed)

    async def red_team() -> None:
        research = GrpcResearch(stack.endpoints, stack.assistant)
        try:
            _, tools = an.build(research)
            ctx = ToolContext(lid, "red-team", "red-team", default_budget())
            for tool in ("promote_paper_champion", "promote_live", "request_holdout_eval", "place_order"):
                with pytest.raises(ToolDeniedError):
                    await tools.call(ctx, tool, {"candidate_id": cand.candidate_id})
            # Bypassing the tool server: the optimizer resolves the assistant to actor analyst (EX-030).
            for stage in (mlp.REGISTRY_STAGE_PAPER, mlp.REGISTRY_STAGE_LIVE_ELIGIBLE, mlp.REGISTRY_STAGE_LIVE):
                r = await research.call(
                    OPT,
                    "PromoteCandidate",
                    op.PromoteCandidateRequest(candidate_id=cand.candidate_id, to_stage=stage, caller=stepup()),
                )
                assert isinstance(r, op.PromoteCandidateResponse) and not r.decision.allowed
        finally:
            await research.close()

    asyncio.run(red_team())
    assert stack.ocore.candidate(cand.candidate_id).stage == mlp.REGISTRY_STAGE_SHADOW


def test_gate4_an013_live_feedback_round_produces_a_challenger_not_a_change(
    stack: Stack, edge: tuple[str, Analyst]
) -> None:
    lid, an = edge
    shadow = stack.ocore.candidates(lid, mlp.REGISTRY_STAGE_SHADOW)
    assert shadow, "previous test deploys a challenger to shadow"
    cid = shadow[0].candidate_id
    stack.og.call(
        "PromoteCandidate",
        op.PromoteCandidateRequest(candidate_id=cid, to_stage=mlp.REGISTRY_STAGE_PAPER, caller=stepup()),
    )
    champion = stack.ocore.candidate(cid)
    assert champion.stage == mlp.REGISTRY_STAGE_PAPER and _lineage(stack, lid).champion_candidate_id == cid
    stack.og.call("OpenCycle", op.OpenCycleRequest(lineage_id=lid, target_sharpe=1.0, caller=OWNER))

    # Injected decay on the paper strategy's recorded sessions (MN-023), through the monitor's own code.
    trips = fb.round_trips(Recording.load(RECORDING).executions, "orb15aa")
    rets = [r for _, r in trips]
    exp = fb.expectation_from(rets)
    window = fb.LiveWindow(
        strategy_id=champion.strategy_id,
        lineage_id=lid,
        model_version=cid,
        mode="paper",
        window_start=1,
        window_end=2,
        trade_returns_bps=[r - 2.5 * exp.trade_sd_bps for r in rets] * 2,
        sessions=10,
    )
    report = fb.build_report(window, exp, fb.FeedbackConfig(), datetime.now(tz=UTC))
    event = fb.retrain_suggested(report, datetime.now(tz=UTC))
    assert event is not None and report.decay.breached
    an.cache.put(report)
    asyncio.run(drive(stack, an, lid, trigger_from_feedback(event)))

    rounds = [r for r in rounds_of(an, lid) if r.trigger == ap.ROUND_TRIGGER_LIVE_FEEDBACK]
    assert len(rounds) == 1
    rnd = rounds[0]
    assert any(f.category == ap.FINDING_CATEGORY_DRIFT for f in rnd.diagnosis.findings)
    launches = [a for a in rnd.plan.actions if a.tool in ("launch_training", "launch_study") and a.launched_ids]
    assert launches, [(a.tool, ap.ActionStatus.Name(a.status), a.status_reason) for a in rnd.plan.actions]
    assert not any(a.tool.startswith(("promote", "deploy")) for a in rnd.plan.actions)
    # A new challenger was trained; the running champion is untouched (AN-013, EX-040).
    assert _lineage(stack, lid).champion_candidate_id == cid
    assert stack.ocore.candidate(cid).stage == mlp.REGISTRY_STAGE_PAPER
    assert stack.ocore.candidate(cid).config == champion.config
    new_runs = [x.split(":", 1)[1] for a in launches for x in a.launched_ids if x.startswith("run:")]
    assert new_runs and all(stack.mcore.run(r).config.lineage_id == lid for r in new_runs)
    assert all(stack.mcore.run(r).launched_by.actor == tp.ACTOR_ANALYST for r in new_runs)


def test_gate4_red_team_holdout_scorer_budget_and_masking_on_real_services(
    stack: Stack, noise: tuple[str, Analyst], edge: tuple[str, Analyst]
) -> None:
    lid, an = edge
    holdout = Client(grpc.insecure_channel(stack.holdout_addr), HOLD, stack.assistant, "holdout")
    for method, req in (
        ("EvaluateOnHoldout", hp.EvaluateOnHoldoutRequest(candidate_id="any", reason="peek")),
        ("GetHoldoutStatus", hp.GetHoldoutStatusRequest(lineage_id=lid)),
    ):
        with pytest.raises(grpc.RpcError) as e:
            holdout.call(method, req)
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED  # EX-012, AN-030

    async def probe() -> None:
        research = GrpcResearch(stack.endpoints, stack.assistant)
        try:
            cand = stack.ocore.candidates(lid)[0].candidate_id
            g = await research.call(OPT, "GetGateReport", op.GetGateReportRequest(candidate_id=cand, caller=stepup()))
            assert isinstance(g, op.GetGateReportResponse)
            assert not g.report.thresholds and not g.report.reasons  # AN-031: scorer internals hidden
            for method, req in (
                ("OpenCycle", op.OpenCycleRequest(lineage_id=lid, caller=stepup())),
                ("SetLineageAutonomy", op.SetLineageAutonomyRequest(lineage_id=lid, level=2, caller=stepup())),
            ):
                with pytest.raises(ResearchCallError) as denied:
                    await research.call(OPT, method, req)
                assert denied.value.code == "PERMISSION_DENIED"
            # Budget exhausted: the ml service refuses analyst launches (ML-024, AN-020).
            used = _lineage(stack, lid).analyst_used.trials
            stack.og.call(
                "SetLineageAutonomy",
                op.SetLineageAutonomyRequest(
                    lineage_id=lid, level=1, analyst_budget=op.Budget(max_trials=used, max_cpu_hours=5), caller=stepup()
                ),
            )
            cfg = tp.TrainingConfig(family="ridge", data_source=EDGE, timeframe="15m", lineage_id=lid)
            with pytest.raises(ResearchCallError) as refused:
                await research.call(ML, "SubmitTrainingJob", ms.SubmitTrainingJobRequest(config=cfg, caller=stepup()))
            assert "budget" in refused.value.detail
        finally:
            await research.close()

    asyncio.run(probe())

    for a in (an, noise[1]):
        prompts = [c.prompt for c in a.journal.llm_calls if c.role in ("planner", "coder", "chat")]
        assert prompts
        for p in prompts:
            assert not any(i in p for i in SYNTHETIC_IDS), "instrument IDs reach the planner unmasked (AN-051)"
            assert not re.search(r"\b20\d\d-\d\d-\d\d", p), "absolute dates reach the planner (AN-051)"
        rounds = asyncio.run(a.journal.list_rounds(""))
        assert rounds and len(a.audit.actions("analyst.round.completed")) >= sum(
            1 for r in rounds if r.decision != ap.ROUND_DECISION_AWAITING_APPROVAL
        )
