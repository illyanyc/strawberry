"""Gate 4 evidence (sections 10 and 11): the ml, optimizer, and holdout services run as real gRPC
servers with Ed25519 service tokens, and this harness drives them exactly as api-gateway's research
BFF does for the owner (step-up attested only where the gateway would attest it), plus the
assistant identity for the boundaries. Synthetic data only (never live-eligible, EX-052).

Opt-in (minutes): ``make research-gate`` (sets TB_RESEARCH_GATE=1 and writes the evidence JSON to
TB_RESEARCH_EVIDENCE).
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import grpc
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from google.protobuf import json_format
from tb_ml.core import MLCore
from tb_ml.grpc_api import ALLOW as ML_ALLOW
from tb_ml.grpc_api import SERVICE as ML_SVC
from tb_ml.grpc_api import MLServicer
from tb_ml.link import GrpcLink
from tb_optimizer.core import OptimizerCore
from tb_optimizer.grpc_api import HOLD, HOLD_ALLOW, OPT, OPT_ALLOW, HoldoutServicer, OptimizerServicer

from tb.ml.v1 import ml_pb2 as mlp
from tb.ml.v1 import ml_service_pb2 as ms
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import holdout_pb2 as hp
from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.audit import MemoryAudit
from tbcore.buspub import MemoryPublisher
from tbcore.docstore import MemoryDocStore
from tbcore.rpc import Client, serve
from tbcore.svcauth import Signer, Verifier

pytestmark = pytest.mark.skipif(os.environ.get("TB_RESEARCH_GATE") != "1", reason="opt-in: make research-gate")

SERVICES = ["api-gateway", "assistant", "ml", "optimizer", "monitor"]
EVIDENCE: dict[str, Any] = {"gate": "phase-4 sections 10 and 11", "data": "synthetic only", "items": {}}


def _keys(tmp: Path) -> dict[str, Signer]:
    out = {}
    for s in SERVICES:
        k = Ed25519PrivateKey.generate()
        (tmp / f"svc_{s.replace('-', '_')}_ed25519.pub").write_text(
            base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        )
        out[s] = Signer(s, k.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    return out


def owner(step_up: bool = False) -> tp.CallerContext:
    """The caller api-gateway forwards (`researchCaller`): step-up only while a passkey is fresh."""
    c = tp.CallerContext(actor=tp.ACTOR_OWNER, actor_id="owner", correlation_id=f"corr-{time.time_ns()}")
    if step_up:
        c.step_up.CopyFrom(tp.StepUp(method="passkey", verified_at=time.time_ns(), session_id="gate4"))
    return c


def d(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = json_format.MessageToDict(msg, preserving_proto_field_name=True)
    return out


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("gate4")
    keys = _keys(tmp)
    store, audit, bus = MemoryDocStore(), MemoryAudit(), MemoryPublisher()
    opt = OptimizerCore(
        store,
        tmp / "opt",
        holdout_store=MemoryDocStore(),
        audit=audit,
        bus=bus,
        deploy_registry=str(tmp / "deploy.json"),
    )
    s_opt, p_opt = serve(
        "127.0.0.1:0",
        [(OPT, OptimizerServicer(opt, audit), OPT_ALLOW)],
        Verifier.from_secrets("optimizer", SERVICES, tmp),
    )
    s_hold, p_hold = serve(
        "127.0.0.1:0",
        [(HOLD, HoldoutServicer(opt, audit), HOLD_ALLOW)],
        Verifier.from_secrets("holdout", SERVICES, tmp),
    )
    s_opt.start()
    s_hold.start()
    link = GrpcLink(f"127.0.0.1:{p_opt}", f"127.0.0.1:{p_hold}", keys["ml"])
    ml = MLCore(store, tmp / "ml", link=link, audit=audit, bus=bus, budget_cpu_seconds=3600.0)
    s_ml, p_ml = serve(
        "127.0.0.1:0", [(ML_SVC, MLServicer(ml, audit), ML_ALLOW)], Verifier.from_secrets("ml", SERVICES, tmp)
    )
    s_ml.start()

    def client(svc: Any, port: int, who: str, aud: str) -> Client:
        return Client(grpc.insecure_channel(f"127.0.0.1:{port}"), svc, keys[who], aud)

    yield {
        "ml": client(ML_SVC, p_ml, "api-gateway", "ml"),
        "opt": client(OPT, p_opt, "api-gateway", "optimizer"),
        "hold": client(HOLD, p_hold, "api-gateway", "holdout"),
        "a_opt": client(OPT, p_opt, "assistant", "optimizer"),
        "a_hold": client(HOLD, p_hold, "assistant", "holdout"),
        "core": opt,
        "audit": audit,
        "bus": bus,
        "tmp": tmp,
    }
    s_ml.stop(None)
    s_opt.stop(None)
    s_hold.stop(None)
    out = os.environ.get("TB_RESEARCH_EVIDENCE")
    if out:
        Path(out).write_text(json.dumps(EVIDENCE, indent=2, sort_keys=True))


def wait_job(ml: Client, job_id: str, timeout: float = 600) -> tp.TrainingJob:
    end = time.time() + timeout
    while time.time() < end:
        j = ml.call("GetJob", ms.GetJobRequest(job_id=job_id)).job
        if j.status in (tp.JOB_STATUS_SUCCEEDED, tp.JOB_STATUS_FAILED, tp.JOB_STATUS_CANCELED):
            return j
        time.sleep(0.5)
    raise TimeoutError(job_id)


def wait_study(opt: Client, study_id: str, timeout: float = 900) -> op.Study:
    end = time.time() + timeout
    while time.time() < end:
        s = opt.call("GetStudy", op.GetStudyRequest(study_id=study_id)).study
        if s.status in (op.STUDY_STATUS_COMPLETED, op.STUDY_STATUS_STOPPED, op.STUDY_STATUS_FAILED):
            return s
        time.sleep(1.0)
    raise TimeoutError(study_id)


RUNS = [
    ("ridge", "synthetic:intraday_edge", "15m", {}),
    ("lgbm_regression", "synthetic:intraday_edge", "15m", {"n_estimators": 80}),
    ("lgbm_lambdarank", "synthetic:daily_edge", "1d", {"n_estimators": 80}),
    ("orb_meta", "synthetic:intraday_edge", "15m", {"n_estimators": 60}),
]


def test_gate4_item1_item2_owner_trains_the_mvp_zoo(stack: Any) -> None:
    """Item 1: ridge, LightGBM regression (intraday), LambdaRank (swing), and ORB + meta-label train on
    residual labels from the owner's calls; every run has a DiagnosticsReport, a tearsheet, a stored
    reproducibility record, and is registered (ML-010..ML-013, ML-020, ML-040)."""
    ml: Client = stack["ml"]
    fams = {f.family: f for f in ml.call("ListModelFamilies", ms.ListModelFamiliesRequest()).families}
    runs = []
    for family, source, tf, hpd in RUNS:
        assert family in fams
        cfg = tp.TrainingConfig(family=family, data_source=source, timeframe=tf, name=f"gate4 {family}")
        cfg.hyperparams.update(hpd)
        est = ml.call("EstimateTrainingJob", ms.EstimateTrainingJobRequest(config=cfg, caller=owner()))
        assert not est.errors, est.errors
        job = ml.call("SubmitTrainingJob", ms.SubmitTrainingJobRequest(config=cfg, caller=owner())).job
        j = wait_job(ml, job.job_id)
        assert j.status == tp.JOB_STATUS_SUCCEEDED, j.error
        run = ml.call("GetRun", ms.GetRunRequest(run_id=j.run_id)).run
        rep = ml.call("GetDiagnostics", ms.GetDiagnosticsRequest(run_id=run.run_id, caller=owner())).report
        assert rep.holdout_excluded and rep.report_id == run.diagnostics_report_id
        sheet = ml.call("GetTearsheet", ms.GetTearsheetRequest(run_id=run.run_id))
        assert "<script" not in sheet.html.lower() and sheet.html
        mv = ml.call(
            "RegisterModel", ms.RegisterModelRequest(run_id=run.run_id, name=f"gate4-{family}", caller=owner())
        )
        assert mv.model_version.registry_stage == mlp.REGISTRY_STAGE_CANDIDATE
        runs.append(
            {
                "family": family,
                "data_source": source,
                "timeframe": tf,
                "label": run.config.label.kind,
                "splitter": run.config.splitter.kind,
                "run_id": run.run_id,
                "config_hash": run.repro.config_hash,
                "dataset_snapshot": run.dataset.snapshot_id,
                "oos": {k: round(v, 5) for k, v in sorted(run.oos_metrics.items())},
                "diagnostics_findings": [f"{tp.Severity.Name(f.severity)}:{f.code}" for f in rep.findings],
                "tearsheet_engine": sheet.engine,
                "model_version": f"{mv.model_version.name} {mv.model_version.version}",
            }
        )
    labels = {r["family"]: r["label"] for r in runs}
    assert labels["lgbm_regression"] == "residual_return" and labels["lgbm_lambdarank"] == "residual_rank"
    assert labels["orb_meta"] == "meta_label"

    # Reproducible: the same config gives the same config hash, dataset snapshot, and metrics (ML-010).
    family, source, tf, hpd = RUNS[1]
    cfg = tp.TrainingConfig(family=family, data_source=source, timeframe=tf, name="gate4 rerun")
    cfg.hyperparams.update(hpd)
    j2 = wait_job(ml, ml.call("SubmitTrainingJob", ms.SubmitTrainingJobRequest(config=cfg, caller=owner())).job.job_id)
    rerun = ml.call("GetRun", ms.GetRunRequest(run_id=j2.run_id)).run
    first = next(r for r in runs if r["family"] == family)
    assert rerun.repro.config_hash == first["config_hash"] and rerun.dataset.snapshot_id == first["dataset_snapshot"]
    assert {k: round(v, 5) for k, v in sorted(rerun.oos_metrics.items())} == first["oos"]
    subjects = stack["bus"].subjects()
    assert "ml.run.completed" in subjects
    EVIDENCE["items"]["1_training"] = {"runs": runs, "rerun_identical": True, "events": sorted(set(subjects))}


def _spec(lineage_id: str, max_trials: int) -> op.StudySpec:
    base = tp.TrainingConfig(family="lgbm_regression", timeframe="15m")
    base.hyperparams.update({"n_estimators": 60})
    return op.StudySpec(
        name="gate4 intraday lgbm",
        lineage_id=lineage_id,
        families=["lgbm_regression"],
        objective=op.Objective(metric="sharpe", direction="maximize"),
        budget=op.Budget(max_trials=max_trials),
        seed=5,
        pruner="median",
        base_config=base,
        evaluation=op.NestedEvaluation(
            inner=tp.SplitterSpec(kind="cpcv", n_groups=4, n_test_groups=1), event_driven_top_k=1
        ),
        preregistration=op.PreRegistration(
            hypothesis="short-horizon residual reversal is predictable from 4-bar residual returns",
            metric="sharpe",
            universe="synthetic:intraday_edge",
            stop_rule="budget of 12 trials",
        ),
    )


def test_gate4_item3_study_cycle_and_holdout(stack: Any) -> None:
    """Item 3 (and item 2's study half): a pre-registered study in a MinBTL-budgeted cycle completes
    with nested CPCV + walk-forward, reports convergence and lineage DSR/PBO from the trial ledger;
    the holdout goes through the holdout service, owner + step-up only, once per family per quarter."""
    opt: Client = stack["opt"]
    hold: Client = stack["hold"]
    lin = opt.call(
        "CreateLineage",
        op.CreateLineageRequest(
            lineage=op.Lineage(
                name="gate4 intraday", family="lgbm_regression", timeframe="15m", universe="synthetic:intraday_edge"
            ),
            caller=owner(),
        ),
    ).lineage
    # Only the owner opens a cycle (the gateway also demands a passkey, policy research.cycle.open).
    with pytest.raises(grpc.RpcError) as ana_cycle:
        stack["a_opt"].call(
            "OpenCycle", op.OpenCycleRequest(lineage_id=lin.lineage_id, target_sharpe=9.0, caller=owner(True))
        )
    assert ana_cycle.value.code() == grpc.StatusCode.PERMISSION_DENIED
    # Half a year of synthetic data: a target annual Sharpe of 4 leaves room for a real study (EX-025).
    cyc = opt.call(
        "OpenCycle", op.OpenCycleRequest(lineage_id=lin.lineage_id, target_sharpe=4.0, caller=owner(True))
    ).cycle
    st = opt.call("CreateStudy", op.CreateStudyRequest(spec=_spec(lin.lineage_id, 12), caller=owner())).study
    opt.call("StartStudy", op.StartStudyRequest(study_id=st.study_id, caller=owner()))
    done = wait_study(opt, st.study_id)
    assert done.status == op.STUDY_STATUS_COMPLETED, done.message
    trials = opt.call("ListTrials", op.ListTrialsRequest(study_id=st.study_id, limit=500)).trials
    folds = sorted({t.outer_fold for t in trials})
    assert -1 in folds and len(folds) >= 3  # outer walk-forward folds plus the final selection (EX-001, EX-002)
    assert len({t.preregistration_hash for t in trials}) == 1  # EX-026
    diag = opt.call("GetStudyDiagnostics", op.GetStudyDiagnosticsRequest(study_id=st.study_id, caller=owner()))
    assert len(diag.diagnostics.convergence) == len(trials)
    stats = opt.call(
        "GetCumulativeOverfittingStats",
        op.GetCumulativeOverfittingStatsRequest(lineage_id=lin.lineage_id, caller=owner()),
    ).stats
    assert stats.n_trials == len(trials) and 1 <= stats.n_effective <= stats.n_trials and 0 <= stats.pbo <= 1

    # Holdout: never from the assistant; owner needs step-up and a reason; once per family per quarter.
    with pytest.raises(grpc.RpcError) as ana:
        stack["a_hold"].call(
            "EvaluateOnHoldout", hp.EvaluateOnHoldoutRequest(candidate_id=done.candidate_id, reason="x")
        )
    assert ana.value.code() == grpc.StatusCode.PERMISSION_DENIED
    with pytest.raises(grpc.RpcError) as plain:
        hold.call(
            "EvaluateOnHoldout",
            hp.EvaluateOnHoldoutRequest(candidate_id=done.candidate_id, reason="final check", caller=owner()),
        )
    assert plain.value.code() == grpc.StatusCode.PERMISSION_DENIED
    res = hold.call(
        "EvaluateOnHoldout",
        hp.EvaluateOnHoldoutRequest(candidate_id=done.candidate_id, reason="final check", caller=owner(True)),
    ).result
    with pytest.raises(grpc.RpcError) as again:
        hold.call(
            "EvaluateOnHoldout",
            hp.EvaluateOnHoldoutRequest(candidate_id=done.candidate_id, reason="second look", caller=owner(True)),
        )
    assert again.value.code() != grpc.StatusCode.OK
    status = hold.call("GetHoldoutStatus", hp.GetHoldoutStatusRequest(lineage_id=lin.lineage_id)).status
    assert status.touches_this_quarter == 1 and status.remaining_this_quarter == 0
    ledger = opt.call("GetTrialLedger", op.GetTrialLedgerRequest(lineage_id=lin.lineage_id, caller=owner())).ledger
    assert ledger.holdout_evaluations == 1 and ledger.trials_total == len(trials) + 1  # EX-013 inflation
    stack["study"], stack["lineage"] = done, lin
    EVIDENCE["items"]["3_study_and_holdout"] = {
        "cycle": d(cyc),
        "study": {
            "study_id": done.study_id,
            "status": op.StudyStatus.Name(done.status),
            "trials": len(trials),
            "outer_folds": folds,
            "outer_estimate": d(done.outer_estimate),
            "study_pbo": done.pbo,
            "deflated_sharpe": done.deflated_sharpe,
        },
        "convergence_points": len(diag.diagnostics.convergence),
        "plateau_estimate": diag.diagnostics.plateau_estimate,
        "lineage_stats": d(stats),
        "holdout": {
            "assistant": "PERMISSION_DENIED",
            "owner_without_step_up": "PERMISSION_DENIED",
            "owner_with_step_up": {"passed": res.passed, "dsr": res.dsr, "quarter": res.quarter},
            "second_touch_same_quarter": again.value.code().name,
            "status": d(status),
            "ledger_trials_total": ledger.trials_total,
        },
    }


def test_gate4_item4_promotion_gates_shadow_and_champion(stack: Any) -> None:
    """Item 4: the real gate report decides; promotion to paper needs every gate and owner step-up
    with the evidence pack; a challenger runs in shadow next to the champion."""
    opt: Client = stack["opt"]
    core: OptimizerCore = stack["core"]
    study: op.Study = stack["study"]
    lin: op.Lineage = stack["lineage"]
    real = opt.call("GetGateReport", op.GetGateReportRequest(candidate_id=study.candidate_id, caller=owner())).report
    assert real.engine == "event" and real.thresholds["dsr_min"] == 0.95 and real.thresholds["pbo_max"] == 0.2
    assert set(real.cost_sensitivity.net_return) >= {"1x", "2x", "2x_event"}
    to_paper = opt.call(
        "PromoteCandidate",
        op.PromoteCandidateRequest(
            candidate_id=study.candidate_id, to_stage=mlp.REGISTRY_STAGE_PAPER, caller=owner(True)
        ),
    )
    assert not to_paper.decision.allowed  # a candidate stage is never promoted straight to paper
    real_ev = {
        "candidate_id": study.candidate_id,
        "passed": real.passed,
        "reasons": list(real.reasons),
        "dsr": real.dsr,
        "pbo": real.pbo,
        "spa_p": real.spa_p,
        "romano_wolf_p": real.romano_wolf_p,
        "cost_sensitivity": dict(real.cost_sensitivity.net_return),
        "thresholds": dict(real.thresholds),
        "promote_to_paper": d(to_paper.decision),
    }
    if not real.passed:
        v = opt.call(
            "PromoteCandidate",
            op.PromoteCandidateRequest(
                candidate_id=study.candidate_id, to_stage=mlp.REGISTRY_STAGE_VALIDATED, caller=owner(True)
            ),
        )
        assert not v.decision.allowed
        real_ev["promote_to_validated"] = d(v.decision)

    # Transition mechanics with a passing gate. No synthetic candidate passes the event-driven gate under
    # the default execution limits, so the stored report of this candidate is marked passing here (and
    # recorded as injected in the evidence); every other check runs for real.
    def inject_pass(candidate_id: str) -> None:
        c = core.candidate(candidate_id)
        rep = core.store.get("gate_report", c.gate_report_id, op.GateReport)
        assert rep is not None
        rep.passed, rep.leakage_passed, rep.survivorship_status = True, True, "complete"
        del rep.reasons[:]
        core.store.put("gate_report", rep.report_id, rep, c.lineage_id, c.candidate_id)

    inject_pass(study.candidate_id)
    cid = study.candidate_id
    steps = []
    for to, caller in (
        (mlp.REGISTRY_STAGE_VALIDATED, owner()),
        (mlp.REGISTRY_STAGE_SHADOW, owner()),
        (mlp.REGISTRY_STAGE_PAPER, owner()),
        (mlp.REGISTRY_STAGE_PAPER, owner(True)),
    ):
        r = opt.call("PromoteCandidate", op.PromoteCandidateRequest(candidate_id=cid, to_stage=to, caller=caller))
        steps.append(
            {
                "to": mlp.RegistryStage.Name(to),
                "step_up": caller.HasField("step_up"),
                "allowed": r.decision.allowed,
                "requires_step_up": r.decision.requires_step_up,
                "reason": r.decision.reason,
            }
        )
    assert [s["allowed"] for s in steps] == [True, True, False, True]
    assert steps[2]["requires_step_up"]
    champ = opt.call("GetLineage", op.GetLineageRequest(lineage_id=lin.lineage_id)).lineage.champion_candidate_id
    assert champ == cid
    changes = [e for e in stack["audit"].actions("optimizer.stage.change") if e.target == cid]
    assert changes[-1].after_hash  # evidence pack hash on the human-gated promotion (EX-057)
    pack = opt.call("GetEvidencePack", op.GetEvidencePackRequest(candidate_id=cid)).pack
    assert "Evidence pack" in pack.markdown
    live = opt.call(
        "PromoteCandidate",
        op.PromoteCandidateRequest(candidate_id=cid, to_stage=mlp.REGISTRY_STAGE_LIVE_ELIGIBLE, caller=owner(True)),
    )
    assert not live.decision.allowed and "synthetic" in live.decision.reason
    a_paper = stack["a_opt"].call(
        "PromoteCandidate",
        op.PromoteCandidateRequest(candidate_id=cid, to_stage=mlp.REGISTRY_STAGE_LIVE_ELIGIBLE, caller=owner(True)),
    )
    assert not a_paper.decision.allowed

    # Challenger: a retrain study produces a new candidate that deploys to shadow next to the champion.
    rs = opt.call(
        "TriggerRetrain", op.TriggerRetrainRequest(lineage_id=lin.lineage_id, reason="gate4", caller=owner())
    ).study
    rdone = wait_study(opt, rs.study_id)
    assert rdone.status == op.STUDY_STATUS_COMPLETED and rdone.candidate_id and rdone.candidate_id != cid, rdone.message
    inject_pass(rdone.candidate_id)
    opt.call(
        "PromoteCandidate",
        op.PromoteCandidateRequest(
            candidate_id=rdone.candidate_id, to_stage=mlp.REGISTRY_STAGE_VALIDATED, caller=owner()
        ),
    )
    sh = opt.call(
        "DeployChallengerShadow", op.DeployChallengerShadowRequest(candidate_id=rdone.candidate_id, caller=owner())
    )
    assert sh.decision.allowed and sh.candidate.stage == mlp.REGISTRY_STAGE_SHADOW
    deployed = json.loads((stack["tmp"] / "deploy.json").read_text())["strategies"]
    modes = {s["strategy_id"]: s["mode"] for s in deployed}
    champion = opt.call("GetCandidate", op.GetCandidateRequest(candidate_id=cid)).candidate
    assert modes[champion.strategy_id] == "paper" and modes[sh.candidate.strategy_id] == "shadow"
    assert opt.call("GetLineage", op.GetLineageRequest(lineage_id=lin.lineage_id)).lineage.champion_candidate_id == cid
    EVIDENCE["items"]["4_promotion"] = {
        "real_gate_report": real_ev,
        "injected_passing_gate_for_mechanics": True,
        "transitions": steps,
        "champion": cid,
        "evidence_pack_hash_audited": changes[-1].after_hash,
        "live_eligible_refused": live.decision.reason,
        "assistant_live_eligible_refused": a_paper.decision.reason,
        "challenger": {
            "retrain_study": rdone.study_id,
            "candidate_id": rdone.candidate_id,
            "origin": sh.candidate.origin,
            "deployment_modes": modes,
        },
    }
