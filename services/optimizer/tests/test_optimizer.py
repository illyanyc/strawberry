"""Optimizer: permission table, holdout service, nested studies, trial ledger and lineage statistics,
gates, registry transitions, cycles, pre-registration, retraining, and gRPC boundaries
(EX-001..EX-061; section 11 acceptance criteria)."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import grpc
import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from tb_optimizer import registry
from tb_optimizer.core import OptimizerCore
from tb_optimizer.grpc_api import HOLD, HOLD_ALLOW, OPT, OPT_ALLOW, HoldoutServicer, OptimizerServicer
from tb_optimizer.stats import Series, align, cumulative, cycle_budget, effective_n

from tb.ml.v1 import ml_pb2 as mlp
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import holdout_pb2 as hp
from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.audit import MemoryAudit
from tbcore.buspub import MemoryPublisher
from tbcore.docstore import MemoryDocStore
from tbcore.ml import synthetic
from tbcore.ml.caller import PermissionDenied
from tbcore.rpc import Client, serve
from tbcore.svcauth import Signer, Verifier

S = mlp
NS = 1_000_000_000


def owner(step_up: bool = False) -> tp.CallerContext:
    c = tp.CallerContext(actor=tp.ACTOR_OWNER, actor_id="owner")
    if step_up:
        c.step_up.CopyFrom(tp.StepUp(method="passkey", verified_at=time.time_ns()))
    return c


ANALYST = tp.CallerContext(actor=tp.ACTOR_ANALYST, actor_id="analyst", analyst_round_id="r1")
SCHEDULE = tp.CallerContext(actor=tp.ACTOR_SCHEDULE, actor_id="retrain-policy")


def spec(lineage_id: str, max_trials: int = 10, metric: str = "sharpe", **kw: Any) -> op.StudySpec:
    base = tp.TrainingConfig(family="lgbm_regression", timeframe="15m")
    base.hyperparams.update({"n_estimators": 60})
    s = op.StudySpec(
        name=kw.pop("name", "lgbm two feature sets two labels"),
        lineage_id=lineage_id,
        families=["lgbm_regression"],
        feature_sets=[
            tp.FeatureSetRef(name="core_intraday", version=1),
            tp.FeatureSetRef(name="mvp_intraday", version=1),
        ],
        label_specs=[tp.LabelSpec(kind="residual_return", horizon=4), tp.LabelSpec(kind="residual_return", horizon=2)],
        objective=op.Objective(metric=metric, direction="maximize", extra_metrics=["max_drawdown"]),
        budget=op.Budget(max_trials=max_trials),
        seed=5,
        pruner="median",
        base_config=base,
        preregistration=op.PreRegistration(
            hypothesis="short-term residual reversal is predictable",
            metric=metric,
            universe="synthetic",
            stop_rule="budget",
        ),
        **kw,
    )
    return s


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    tmp = tmp_path_factory.mktemp("opt")
    audit, bus = MemoryAudit(), MemoryPublisher()
    core = OptimizerCore(
        MemoryDocStore(),
        tmp,
        holdout_store=MemoryDocStore(),
        audit=audit,
        bus=bus,
        deploy_registry=str(tmp / "deploy.json"),
    )
    lin = core.create_lineage(
        op.Lineage(
            name="intraday lgbm", family="lgbm_regression", timeframe="15m", universe="synthetic:intraday_small"
        ),
        owner(),
    )
    core.open_cycle(lin.lineage_id, 1.0, owner())
    st = core.create_study(spec(lin.lineage_id), owner())
    done = core.start_study(st.study_id, None, owner(), wait=True)
    return {"core": core, "audit": audit, "bus": bus, "lin": lin, "study": core.study(done.study_id), "tmp": tmp}


def test_ex001_ex024_study_completes_with_outer_estimate_dsr_and_pbo(world: dict[str, Any]) -> None:
    st: op.Study = world["study"]
    core: OptimizerCore = world["core"]
    assert st.status == op.STUDY_STATUS_COMPLETED, st.message
    assert st.trials_completed + st.trials_pruned + st.trials_failed <= st.spec.budget.max_trials + 2
    assert st.outer_estimate.n_obs > 0 and st.best_trials
    assert any(b.outer_fold == -1 for b in st.best_trials)  # final selection (EX-002)
    rep = core.gate_report(st.candidate_id, owner())
    assert rep.outer.n_obs > 0 and rep.n_effective >= 1 and rep.walk_forward
    assert set(rep.cost_sensitivity.net_return) >= {"1x", "1.5x", "2x"}
    assert rep.engine == "event"  # BT-006: rerun event-driven before the gate
    assert rep.thresholds["dsr_min"] == 0.95
    trials = core.trials(study_id=st.study_id)
    assert {t.preregistration_hash for t in trials} == {trials[0].preregistration_hash}
    assert {"hyperparams", "features", "labels"} <= set(trials[0].dimensions)
    subjects = world["bus"].subjects()
    assert "optimizer.study.completed" in subjects and "backtest.completed" in subjects


def test_ex021_study_diagnostics_and_lineage_stats(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    st: op.Study = world["study"]
    d = core.study_diagnostics(st.study_id)
    assert len(d.convergence) == len(core.trials(study_id=st.study_id))
    assert d.param_importance and 0 <= d.pruned_rate <= 1
    stats = core.cumulative(st.spec.lineage_id)
    assert stats.n_trials == len(core.trials(lineage_id=st.spec.lineage_id))
    assert 1 <= stats.n_effective <= stats.n_trials
    assert 0 <= stats.deflated_sharpe_prob <= 1


def test_ex010_ex012_holdout_is_refused_to_studies_and_the_analyst(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    lin = core._lineage(world["lin"].lineage_id)
    late = spec(lin.lineage_id)
    late.period.end = lin.holdout_boundary + NS
    with pytest.raises(PermissionDenied, match="holdout"):
        core.create_study(late, owner())
    ok, why, b = core.holdout.check_data(lin.lineage_id, tp.Period(start=0, end=lin.holdout_boundary + 1), ANALYST)
    assert not ok and b == lin.holdout_boundary and "EX-010" in why
    audit: MemoryAudit = world["audit"]
    assert audit.actions("holdout.data_refused")
    cand = world["study"].candidate_id
    with pytest.raises(PermissionDenied, match="analyst never"):
        core.holdout.evaluate(cand, "curious", ANALYST)
    assert audit.actions("holdout.evaluate.denied")
    with pytest.raises(PermissionDenied, match="step-up"):
        core.holdout.evaluate(cand, "quarterly check", owner())


def test_ex011_ex013_owner_holdout_touch_once_per_quarter_and_counted(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    lin_id = world["lin"].lineage_id
    before = core.ledger(lin_id).trials_total
    cand = world["study"].candidate_id
    res = core.holdout.evaluate(cand, "pre-paper check", owner(step_up=True))
    assert res.candidate_id == cand and 0.0 <= res.dsr <= 1.0
    assert core.ledger(lin_id).holdout_evaluations == 1 and core.ledger(lin_id).trials_total == before + 1
    with pytest.raises(PermissionDenied, match="already used"):
        core.holdout.evaluate(cand, "again", owner(step_up=True))
    st = core.holdout.status(lin_id)
    assert st.touches_this_quarter == 1 and st.remaining_this_quarter == 0
    # EX-012: no holdout result in the research store; analyst gate view has no reasons or thresholds.
    research_rows = core.store._rows
    assert not {k for k, _ in research_rows} & {"touch", "result", "boundary"}
    assert all(b"pre-paper check" not in v[3] for v in research_rows.values())
    view = core.gate_report(cand, ANALYST)
    assert not view.reasons and not view.thresholds


def test_ex014_boundary_moves_only_forward_with_step_up(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    lin_id = world["lin"].lineage_id
    b = core.holdout.boundary(lin_id)
    with pytest.raises(PermissionDenied):
        core.holdout.set_boundary(lin_id, b + NS, owner())
    with pytest.raises(ValueError, match="forward"):
        core.holdout.set_boundary(lin_id, b - NS, owner(step_up=True))


def test_ex026_ex025_preregistration_and_cycle_budget(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    lin_id = world["lin"].lineage_id
    s = spec(lin_id)
    s.preregistration.Clear()
    st = core.create_study(s, owner())
    with pytest.raises(ValueError, match="pre-register"):
        core.start_study(st.study_id, None, owner())
    s2 = spec(lin_id, metric="ic")
    s2.preregistration.metric = "sharpe"
    st2 = core.create_study(s2, owner())
    with pytest.raises(ValueError, match="pre-registered metric"):
        core.start_study(st2.study_id, None, owner())
    assert cycle_budget(1.0, 2.0) < cycle_budget(5.0, 2.0)
    lin2 = core.create_lineage(
        op.Lineage(name="no cycle", family="ridge", universe="synthetic:intraday_small"), owner()
    )
    st3 = core.create_study(spec(lin2.lineage_id), owner())
    with pytest.raises(PermissionDenied, match="cycle"):
        core.start_study(st3.study_id, None, owner())


def test_ex020_ex041_owner_analyst_and_retrain_trials_share_one_ledger(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    lin_id = world["lin"].lineage_id
    stats0 = core.cumulative(lin_id)
    with pytest.raises(PermissionDenied, match="autonomy"):
        core.create_study(spec(lin_id, max_trials=4), ANALYST)
    core.set_autonomy(lin_id, 1, op.Budget(max_trials=40, max_cpu_hours=5), None, owner(step_up=True))
    with pytest.raises(PermissionDenied):
        core.set_autonomy(lin_id, 2, None, None, ANALYST)
    sa = core.create_study(spec(lin_id, max_trials=4, name="analyst round"), ANALYST)
    core.start_study(sa.study_id, None, ANALYST, wait=True)
    core.set_policy(op.RetrainingPolicy(lineage_id=lin_id, triggers=["scheduled"], enabled=True), owner())
    core.set_autonomy(lin_id, 0, None, None, owner())  # level 0: the default policy runs the retrain
    champion_before = core._lineage(lin_id).champion_candidate_id
    started = core.tick(now=time.time_ns() + 30 * 86_400 * NS)
    assert started
    led = core.ledger(lin_id)
    assert led.by_source.get("owner", 0) > 0 and led.by_source.get("analyst", 0) > 0
    retrain_trials = [t for t in core.trials(lineage_id=lin_id) if t.source == tp.ACTOR_SCHEDULE]
    deadline = time.time() + 600
    while time.time() < deadline and core.study(started[0]).status == op.STUDY_STATUS_RUNNING:
        time.sleep(1)
    retrain_trials = [t for t in core.trials(lineage_id=lin_id) if t.source == tp.ACTOR_SCHEDULE]
    assert retrain_trials, "scheduled retrain trials are ledgered with source schedule"
    stats1 = core.cumulative(lin_id)
    assert stats1.n_trials > stats0.n_trials
    # The lineage's deflation inputs move with every trial, whoever launched it (EX-021).
    before = (stats0.n_effective, stats0.sr_variance, stats0.deflated_sharpe_prob)
    assert (stats1.n_effective, stats1.sr_variance, stats1.deflated_sharpe_prob) != before
    rs = core.study(started[0])
    assert rs.spec.kind == "retrain" and rs.status == op.STUDY_STATUS_COMPLETED, rs.message
    # EX-040: a retrain produces a challenger and never replaces the champion by itself.
    assert core._lineage(lin_id).champion_candidate_id == champion_before
    if rs.candidate_id:
        assert core.candidate(rs.candidate_id).stage in (S.REGISTRY_STAGE_CANDIDATE, S.REGISTRY_STAGE_VALIDATED)


def test_ex021_pure_noise_inner_looks_good_outer_and_dsr_do_not(tmp_path: Path) -> None:
    core = OptimizerCore(MemoryDocStore(), tmp_path, holdout_store=MemoryDocStore())
    lin = core.create_lineage(
        op.Lineage(name="noise", family="lgbm_regression", universe="synthetic:intraday_noise"), owner()
    )
    core.open_cycle(lin.lineage_id, 1.0, owner())
    s = spec(lin.lineage_id, max_trials=8, metric="ic")
    s.universe.instrument_ids.extend(synthetic.universe(synthetic.preset("intraday_noise"))[:10])
    st = core.start_study(core.create_study(s, owner()).study_id, None, owner(), wait=True)
    st = core.study(st.study_id)
    assert st.status == op.STUDY_STATUS_COMPLETED, st.message
    best_inner = max(b.inner_metric for b in st.best_trials)
    assert best_inner > 0  # selection bias: the best of several noise trials looks like skill in sample
    rep = core.gate_report(st.candidate_id, owner())
    assert rep.dsr < 0.95 and not rep.passed and "dsr" in rep.reasons
    assert core.candidate(st.candidate_id).stage == S.REGISTRY_STAGE_CANDIDATE


def _gate(passed: bool = True, spa: float = 0.01, surv: str = "complete", engine: str = "event") -> op.GateReport:
    return op.GateReport(
        passed=passed,
        reasons=[] if passed else ["dsr"],
        engine=engine,
        leakage_passed=True,
        survivorship_status=surv,
        spa_p=spa,
    )


def _ctx(
    level: int, gate: op.GateReport | None, champion: bool = True, promos: int = 0, demoted_at: int = 0
) -> registry.Context:
    lin = op.Lineage(lineage_id="L", autonomy_level=level, promotion_limits=registry.DEFAULT_LIMITS)
    return registry.Context(lin, gate, champion, promos, demoted_at, time.time_ns())


def _cand(stage: int, sessions: int = 12, synthetic_data: bool = False, demoted: bool = False) -> op.Candidate:
    c = op.Candidate(candidate_id="c1", shadow_sessions=sessions, synthetic_data=synthetic_data, demoted=demoted)
    c.stage = stage  # type: ignore[assignment]
    return c


# (actor, level, from, to, expected) — the section 11 permission table (EX-031).
TABLE = [
    ("owner", 0, S.REGISTRY_STAGE_CANDIDATE, S.REGISTRY_STAGE_VALIDATED, True),
    ("analyst", 0, S.REGISTRY_STAGE_CANDIDATE, S.REGISTRY_STAGE_VALIDATED, False),
    ("analyst", 1, S.REGISTRY_STAGE_CANDIDATE, S.REGISTRY_STAGE_VALIDATED, True),
    ("analyst", 2, S.REGISTRY_STAGE_CANDIDATE, S.REGISTRY_STAGE_VALIDATED, True),
    ("owner", 0, S.REGISTRY_STAGE_VALIDATED, S.REGISTRY_STAGE_SHADOW, True),
    ("analyst", 0, S.REGISTRY_STAGE_VALIDATED, S.REGISTRY_STAGE_SHADOW, False),
    ("analyst", 1, S.REGISTRY_STAGE_VALIDATED, S.REGISTRY_STAGE_SHADOW, True),
    ("analyst", 2, S.REGISTRY_STAGE_VALIDATED, S.REGISTRY_STAGE_SHADOW, True),
    ("owner+stepup", 0, S.REGISTRY_STAGE_SHADOW, S.REGISTRY_STAGE_PAPER, True),
    ("owner", 0, S.REGISTRY_STAGE_SHADOW, S.REGISTRY_STAGE_PAPER, False),
    ("analyst", 0, S.REGISTRY_STAGE_SHADOW, S.REGISTRY_STAGE_PAPER, False),
    ("analyst", 1, S.REGISTRY_STAGE_SHADOW, S.REGISTRY_STAGE_PAPER, False),
    ("analyst", 2, S.REGISTRY_STAGE_SHADOW, S.REGISTRY_STAGE_PAPER, True),
    ("owner+stepup", 0, S.REGISTRY_STAGE_PAPER, S.REGISTRY_STAGE_LIVE_ELIGIBLE, True),
    ("owner", 0, S.REGISTRY_STAGE_PAPER, S.REGISTRY_STAGE_LIVE_ELIGIBLE, False),
    ("analyst", 2, S.REGISTRY_STAGE_PAPER, S.REGISTRY_STAGE_LIVE_ELIGIBLE, False),
    ("owner+stepup", 0, S.REGISTRY_STAGE_LIVE_ELIGIBLE, S.REGISTRY_STAGE_LIVE, False),
    ("analyst", 2, S.REGISTRY_STAGE_LIVE_ELIGIBLE, S.REGISTRY_STAGE_LIVE, False),
    ("schedule", 0, S.REGISTRY_STAGE_VALIDATED, S.REGISTRY_STAGE_SHADOW, True),
    ("schedule", 0, S.REGISTRY_STAGE_SHADOW, S.REGISTRY_STAGE_PAPER, False),
]


@pytest.mark.parametrize(("actor", "level", "frm", "to", "expected"), TABLE)
def test_ex031_permission_table(actor: str, level: int, frm: int, to: int, expected: bool) -> None:
    caller = {"owner": owner(), "owner+stepup": owner(step_up=True), "analyst": ANALYST, "schedule": SCHEDULE}[actor]
    d = registry.decide(caller, _cand(frm, sessions=30), to, _ctx(level, _gate()))
    assert d.allowed is expected, d.reason


def test_ex033_ex052_ex053_ex055_promotion_limits_and_special_cases() -> None:
    shadow = _cand(S.REGISTRY_STAGE_SHADOW, sessions=12)
    assert not registry.decide(
        ANALYST, shadow, S.REGISTRY_STAGE_PAPER, _ctx(2, _gate(), champion=False)
    ).allowed  # new spec
    assert not registry.decide(
        ANALYST, _cand(S.REGISTRY_STAGE_SHADOW, sessions=3), S.REGISTRY_STAGE_PAPER, _ctx(2, _gate())
    ).allowed
    assert not registry.decide(ANALYST, shadow, S.REGISTRY_STAGE_PAPER, _ctx(2, _gate(), promos=1)).allowed
    assert not registry.decide(
        ANALYST, shadow, S.REGISTRY_STAGE_PAPER, _ctx(2, _gate(), demoted_at=time.time_ns())
    ).allowed
    assert not registry.decide(
        ANALYST, shadow, S.REGISTRY_STAGE_PAPER, _ctx(2, _gate(spa=0.4))
    ).allowed  # SPA vs champion
    assert not registry.decide(
        owner(True), shadow, S.REGISTRY_STAGE_PAPER, _ctx(0, _gate(surv="unknown"))
    ).allowed  # O3
    assert not registry.decide(owner(True), shadow, S.REGISTRY_STAGE_PAPER, _ctx(0, _gate(engine="vectorized"))).allowed
    assert not registry.decide(owner(True), shadow, S.REGISTRY_STAGE_PAPER, _ctx(0, _gate(passed=False))).allowed
    synth = _cand(S.REGISTRY_STAGE_PAPER, sessions=40, synthetic_data=True)
    assert "synthetic" in registry.decide(owner(True), synth, S.REGISTRY_STAGE_LIVE_ELIGIBLE, _ctx(0, _gate())).reason
    few = _cand(S.REGISTRY_STAGE_PAPER, sessions=5)
    assert "graduation" in registry.decide(owner(True), few, S.REGISTRY_STAGE_LIVE_ELIGIBLE, _ctx(0, _gate())).reason
    demoted = _cand(S.REGISTRY_STAGE_SHADOW, sessions=30, demoted=True)
    assert not registry.decide(ANALYST, demoted, S.REGISTRY_STAGE_PAPER, _ctx(2, _gate())).allowed
    assert registry.decide(owner(True), demoted, S.REGISTRY_STAGE_PAPER, _ctx(2, _gate())).allowed


def test_ex050_ex056_ex057_transitions_deploy_shadow_and_paper_with_evidence(world: dict[str, Any]) -> None:
    core: OptimizerCore = world["core"]
    st: op.Study = world["study"]
    cand = core.candidate(st.candidate_id)
    # Force a passing event-driven gate for the transition mechanics (the gate itself is tested above).
    rep = core.store.get("gate_report", cand.gate_report_id, op.GateReport)
    assert rep is not None
    rep.passed, rep.engine, rep.leakage_passed, rep.survivorship_status = True, "event", True, "complete"
    del rep.reasons[:]
    core.store.put("gate_report", rep.report_id, rep, cand.lineage_id, cand.candidate_id)
    if cand.stage == S.REGISTRY_STAGE_CANDIDATE:
        cand, d = core.promote(cand.candidate_id, S.REGISTRY_STAGE_VALIDATED, owner())
        assert d.allowed
    cand, d = core.deploy_shadow(cand.candidate_id, owner())
    assert d.allowed and cand.stage == S.REGISTRY_STAGE_SHADOW
    deployed = json.loads(Path(world["tmp"] / "deploy.json").read_text())["strategies"]
    entry = next(s for s in deployed if s["strategy_id"] == cand.strategy_id)
    assert entry["mode"] == "shadow" and entry["runtime"]["class"] == "model_signal"
    assert Path(entry["runtime"]["params"]["bundle"]).exists()
    _, denied = core.promote(cand.candidate_id, S.REGISTRY_STAGE_PAPER, owner())
    assert not denied.allowed and denied.requires_step_up
    cand, ok = core.promote(cand.candidate_id, S.REGISTRY_STAGE_PAPER, owner(step_up=True))
    assert ok.allowed and cand.stage == S.REGISTRY_STAGE_PAPER
    assert core._lineage(cand.lineage_id).champion_candidate_id == cand.candidate_id
    changes = [e for e in world["audit"].actions("optimizer.stage.change") if e.target == cand.candidate_id]
    assert changes[-1].after_hash  # evidence pack hash on the human-gated promotion (EX-057)
    pack = core.evidence_pack(cand.candidate_id)
    assert "Evidence pack" in pack.markdown and "<html>" in pack.html
    _, live = core.promote(cand.candidate_id, S.REGISTRY_STAGE_LIVE_ELIGIBLE, owner(step_up=True))
    assert not live.allowed and "synthetic" in live.reason
    nom = core.nominate(cand.candidate_id, "looks good", ANALYST)
    assert not nom.allowed  # level 0 lineage now
    assert core.candidate(cand.candidate_id).stage == S.REGISTRY_STAGE_PAPER
    g = core.lineage_graph(cand.candidate_id)
    assert {n.kind for n in g.nodes} >= {"candidate", "run", "study", "dataset", "trial"}


def test_ex021_stats_count_unusable_trials_and_holdout_inflation() -> None:
    rng = np.random.default_rng(0)
    trials = [op.Trial(trial_id=f"t{i}") for i in range(6)]
    series = {f"t{i}": Series(np.arange(100, dtype=np.int64), rng.normal(0, 0.01, 100)) for i in range(4)}
    mat = align(series)
    e0 = effective_n(trials[:4], mat)
    e1 = effective_n(trials, mat)
    e2 = effective_n(trials, mat, holdout_touches=2)
    assert e1.n_effective > e0.n_effective and e2.n_effective == pytest.approx(e1.n_effective + 2)
    st = cumulative("L", trials, series)
    assert st.n_trials == 6 and st.best_trial_id in series and 0 <= st.pbo <= 1


def _key(tmp: Path, service: str) -> Signer:
    k = Ed25519PrivateKey.generate()
    (tmp / f"svc_{service.replace('-', '_')}_ed25519.pub").write_text(
        base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    )
    return Signer(service, k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))


def test_ex012_ex030_grpc_boundaries_for_the_assistant(world: dict[str, Any], tmp_path: Path) -> None:
    core: OptimizerCore = world["core"]
    assistant, gateway = _key(tmp_path, "assistant"), _key(tmp_path, "api-gateway")
    v_opt = Verifier.from_secrets("optimizer", ["assistant", "api-gateway"], tmp_path)
    v_hold = Verifier.from_secrets("holdout", ["assistant", "api-gateway"], tmp_path)
    s1, p1 = serve("127.0.0.1:0", [(OPT, OptimizerServicer(core, world["audit"]), OPT_ALLOW)], v_opt)
    s2, p2 = serve("127.0.0.1:0", [(HOLD, HoldoutServicer(core, world["audit"]), HOLD_ALLOW)], v_hold)
    s1.start()
    s2.start()
    try:
        cand = world["study"].candidate_id
        ho = Client(grpc.insecure_channel(f"127.0.0.1:{p2}"), HOLD, assistant, "holdout")
        with pytest.raises(grpc.RpcError) as e:
            ho.call("EvaluateOnHoldout", hp.EvaluateOnHoldoutRequest(candidate_id=cand, reason="x"))
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED
        with pytest.raises(grpc.RpcError) as e2:
            ho.call("GetHoldoutStatus", hp.GetHoldoutStatusRequest(lineage_id=world["lin"].lineage_id))
        assert e2.value.code() == grpc.StatusCode.PERMISSION_DENIED
        oc = Client(grpc.insecure_channel(f"127.0.0.1:{p1}"), OPT, assistant, "optimizer")
        claimed = owner(step_up=True)
        r = oc.call("GetGateReport", op.GetGateReportRequest(candidate_id=cand, caller=claimed))
        assert not r.report.reasons and not r.report.thresholds
        pr = oc.call(
            "PromoteCandidate",
            op.PromoteCandidateRequest(candidate_id=cand, to_stage=S.REGISTRY_STAGE_LIVE_ELIGIBLE, caller=claimed),
        )
        assert not pr.decision.allowed
        assert any(a.actor == "analyst" for a in world["audit"].actions("optimizer.promote.denied"))
        with pytest.raises(grpc.RpcError) as e3:
            oc.call("OpenCycle", op.OpenCycleRequest(lineage_id=world["lin"].lineage_id, caller=claimed))
        assert e3.value.code() == grpc.StatusCode.PERMISSION_DENIED
        gw = Client(grpc.insecure_channel(f"127.0.0.1:{p1}"), OPT, gateway, "optimizer")
        full = gw.call("GetGateReport", op.GetGateReportRequest(candidate_id=cand, caller=owner()))
        assert full.report.thresholds
    finally:
        s1.stop(None)
        s2.stop(None)
