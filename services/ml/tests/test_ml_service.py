"""ml worker: submit validation, cost guard, devices, queue priority, runs, diagnostics, events, analyst
tagging and budgets, ablations, registration, prediction, MLflow mirror, and gRPC actor resolution
(ML-011, ML-013, ML-022, ML-024, ML-025, ML-030..ML-032, ML-040, ML-042, EX-030)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import grpc
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from tb_ml.core import InvalidConfig, MLCore
from tb_ml.devices import BurstHook
from tb_ml.grpc_api import ALLOW, SERVICE, MLServicer

from tb.ml.v1 import ml_service_pb2 as ms
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.audit import MemoryAudit
from tbcore.buspub import MemoryPublisher
from tbcore.docstore import MemoryDocStore
from tbcore.ml.caller import PermissionDenied
from tbcore.ml.tracking import MlflowTracker
from tbcore.rpc import Client, serve
from tbcore.svcauth import Signer, Verifier

OWNER = tp.CallerContext(actor=tp.ACTOR_OWNER, actor_id="owner")
ANALYST = tp.CallerContext(actor=tp.ACTOR_ANALYST, actor_id="analyst", analyst_round_id="round-1")


class FakeLink:
    def __init__(self, autonomy: int = 1, max_trials: int = 10, boundary: int = 0) -> None:
        self.lin = op.Lineage(
            lineage_id="lin-1",
            family="ridge",
            autonomy_level=autonomy,
            analyst_budget=op.Budget(max_trials=max_trials, max_cpu_hours=10.0),
            holdout_boundary=boundary,
        )
        self.trials: list[tuple[op.Trial, tp.CallerContext]] = []

    def lineage(self, lineage_id: str) -> op.Lineage | None:
        return self.lin if lineage_id == "lin-1" else None

    def check_data(self, lineage_id: str, period: tp.Period, caller: tp.CallerContext) -> tuple[bool, str, int]:
        b = self.lin.holdout_boundary
        return (not b or period.end <= b), "reaches the holdout", b

    def record_trial(self, trial: op.Trial, caller: tp.CallerContext) -> None:
        self.trials.append((trial, caller))
        self.lin.analyst_used.trials += 1 if caller.actor == tp.ACTOR_ANALYST else 0


def _cfg(family: str = "ridge", lineage: str = "", **hp: Any) -> tp.TrainingConfig:
    c = tp.TrainingConfig(family=family, data_source="synthetic:intraday_small", timeframe="15m", lineage_id=lineage)
    if hp:
        c.hyperparams.update(hp)
    return c


def _core(tmp_path: Path, link: Any = None, budget: float = 3600.0) -> tuple[MLCore, MemoryPublisher, MemoryAudit]:
    bus, audit = MemoryPublisher(), MemoryAudit()
    core = MLCore(MemoryDocStore(), tmp_path, link=link, audit=audit, bus=bus, budget_cpu_seconds=budget, start=False)
    return core, bus, audit


def test_ml011_invalid_configs_are_refused_at_submit(tmp_path: Path) -> None:
    core, _, _ = _core(tmp_path)
    with pytest.raises(InvalidConfig, match="unknown model family"):
        core.submit(tp.TrainingConfig(family="nope", data_source="synthetic:intraday_small"), OWNER)
    with pytest.raises(InvalidConfig, match="num_leaves"):
        core.submit(_cfg("lgbm_regression", num_leaves=1), OWNER)
    late = _cfg()
    late.period.end = 4_000_000_000_000_000_000
    with pytest.raises(InvalidConfig, match="holdout boundary"):
        core.submit(late, OWNER)
    with pytest.raises(InvalidConfig, match="swing"):
        core.submit(_cfg("lgbm_lambdarank"), OWNER)


def test_ml031_cost_guard_needs_owner_confirmation(tmp_path: Path) -> None:
    core, _, _ = _core(tmp_path, link=FakeLink(), budget=0.001)
    with pytest.raises(InvalidConfig, match="confirm"):
        core.submit(_cfg(), OWNER)
    job = core.submit(_cfg(), OWNER, confirm_over_budget=True)
    assert job.estimate.over_budget and job.estimate.cpu_seconds > 0
    with pytest.raises(PermissionDenied, match="budget"):
        core.submit(_cfg(lineage="lin-1"), ANALYST)


def test_ml032_cloud_gpu_fails_closed_without_a_hook(tmp_path: Path) -> None:
    core, _, _ = _core(tmp_path)
    c = _cfg()
    c.resources.device = "cloud_gpu"
    with pytest.raises(InvalidConfig, match="GPU"):
        core.submit(c, OWNER)
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"auth": req.headers.get("authorization"), "body": json.loads(req.content)})
        return httpx.Response(200, json={"remote_job_id": "remote-1"})

    hook = BurstHook("https://burst.invalid/jobs", "tok", httpx.Client(transport=httpx.MockTransport(handler)))
    assert hook.submit("j1", _cfg(), "ds_x") == "remote-1"
    assert seen[0]["auth"] == "Bearer tok" and set(seen[0]["body"]) == {"job_id", "config", "snapshot_id"}


def test_ml030_queue_runs_owner_jobs_before_analyst_jobs(tmp_path: Path) -> None:
    core, _, _ = _core(tmp_path, link=FakeLink())
    a = core.submit(_cfg(lineage="lin-1"), ANALYST)
    o = core.submit(_cfg(), OWNER)
    order = [jid for _, _, jid in sorted(core._queue)]
    assert order == [o.job_id, a.job_id]
    assert a.launched_by.actor == tp.ACTOR_ANALYST and a.launched_by.analyst_round_id == "round-1"


def test_ml022_ml013_run_is_stored_with_diagnostics_card_and_event(tmp_path: Path) -> None:
    core, bus, audit = _core(tmp_path)
    job = core.submit(_cfg("lgbm_regression", n_estimators=60), OWNER)
    core.run_pending()
    done = core.job(job.job_id)
    assert done.status == tp.JOB_STATUS_SUCCEEDED, done.error
    run = core.run(done.run_id)
    assert run.dataset.snapshot_id and run.repro.config_hash and run.repro.env_lock_hash
    assert run.folds and "oos_ic" in run.oos_metrics
    rep = core.diagnostics(run.run_id)
    assert rep.holdout_excluded and rep.run_id == run.run_id
    assert Path(run.model_card_uri).read_text().startswith("# Model card")
    assert (Path(run.artifact_uri) / "model" / "bundle.sha256").exists()
    assert core.importance(run.run_id, "permutation").per_fold
    assert core.profile(run.dataset.snapshot_id).rows == run.dataset.rows
    subj, msg, _ = bus.sent[-1]
    assert subj == "ml.run.completed" and msg.run_id == run.run_id and msg.report_id == rep.report_id
    progress = list(core.stream(job.job_id))
    assert progress[0].phase == "dataset" and progress[-1].phase == "done"
    assert audit.actions("ml.training.submit")


def test_ml024_analyst_jobs_are_tagged_budgeted_and_ledgered(tmp_path: Path) -> None:
    link = FakeLink(autonomy=1, max_trials=1)
    core, bus, _ = _core(tmp_path, link=link)
    job = core.submit(_cfg(lineage="lin-1"), ANALYST)
    core.run_pending()
    run = core.run(core.job(job.job_id).run_id)
    trial, caller = link.trials[0]
    assert trial.source == tp.ACTOR_ANALYST and trial.analyst_round_id == "round-1" and caller.actor == tp.ACTOR_ANALYST
    assert run.trial_id == trial.trial_id and trial.oof_returns_ref.endswith("returns.parquet")
    assert bus.sent[-1][1].launched_by == tp.ACTOR_ANALYST
    with pytest.raises(PermissionDenied, match="trial budget"):
        core.submit(_cfg(lineage="lin-1"), ANALYST)
    with pytest.raises(PermissionDenied, match="autonomy"):
        _core(tmp_path, link=FakeLink(autonomy=0))[0].submit(_cfg(lineage="lin-1"), ANALYST)
    with pytest.raises(PermissionDenied, match="lineage"):
        core.submit(_cfg(), ANALYST)


def test_ml025_ablation_reuses_snapshot_folds_and_seeds(tmp_path: Path) -> None:
    link = FakeLink()
    core, _, _ = _core(tmp_path, link=link)
    base_job = core.submit(_cfg(lineage="lin-1"), OWNER)
    core.run_pending()
    base = core.run(core.job(base_job.job_id).run_id)
    jobs = core.ablate(base.run_id, [tp.AblationVariant(name="no_calendar", drop_feature_groups=["calendar"])], OWNER)
    core.run_pending()
    abl = core.run(core.job(jobs[0].job_id).run_id)
    assert abl.base_run_id == base.run_id and abl.dataset.snapshot_id == base.dataset.snapshot_id
    assert [(f.train, f.test) for f in abl.folds] == [(f.train, f.test) for f in base.folds]
    assert abl.config.seed == base.config.seed
    assert "tod_sin" in base.dataset.features and "tod_sin" not in core.diagnostics(abl.run_id).data_quality.features
    assert [t.dimensions[0] for t, _ in link.trials] == ["single_run", "ablation"]


def test_ml040_ml042_registered_model_predicts_with_feature_vector_ids(tmp_path: Path) -> None:
    core, _, _ = _core(tmp_path)
    job = core.submit(_cfg(), OWNER)
    core.run_pending()
    run = core.run(core.job(job.job_id).run_id)
    mv = core.register(run.run_id, "ridge-intraday", OWNER)
    assert mv.registry_stage == 1 and mv.artifact_uri.endswith("model")
    with pytest.raises(PermissionDenied):
        core.register(run.run_id, "x", ANALYST)
    sigs = core.predict(f"{mv.model_id}:{mv.version}", [], run.dataset.period.end, "strat-1")
    assert sigs and all(s.feature_snapshot_id.startswith("fv_") and s.model_version for s in sigs)


def test_ml013_mlflow_mirror_uses_the_rest_api() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        if req.url.path.endswith("experiments/get-by-name"):
            return httpx.Response(404, json={})
        if req.url.path.endswith("experiments/create"):
            return httpx.Response(200, json={"experiment_id": "7"})
        if req.url.path.endswith("runs/create"):
            return httpx.Response(200, json={"run": {"info": {"run_id": "r1"}}})
        return httpx.Response(200, json={})

    t = MlflowTracker("http://mlflow:5000", client=httpx.Client(transport=httpx.MockTransport(handler)))
    rid = t.log_run("lineage/x", "run", {"family": "ridge"}, {"oos_ic": 0.1}, {"tb.run_id": "a"}, {})
    assert rid == "r1"
    assert calls[-1].endswith("runs/update") and any("log-batch" in c for c in calls)


def _keypair(tmp: Path, service: str) -> Signer:
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    (tmp / f"svc_{service.replace('-', '_')}_ed25519.pub").write_text(base64.b64encode(pub).decode())
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return Signer(service, seed)


def test_ex030_grpc_forces_actor_analyst_for_the_assistant(tmp_path: Path) -> None:
    core, _, audit = _core(tmp_path / "a", link=FakeLink())
    assistant = _keypair(tmp_path, "assistant")
    gateway = _keypair(tmp_path, "api-gateway")
    verifier = Verifier.from_secrets("ml", ["assistant", "api-gateway"], tmp_path)
    server, port = serve("127.0.0.1:0", [(SERVICE, MLServicer(core, audit), ALLOW)], verifier)
    server.start()
    try:
        ch = grpc.insecure_channel(f"127.0.0.1:{port}")
        as_assistant = Client(ch, SERVICE, assistant, "ml")
        claimed_owner = tp.CallerContext(actor=tp.ACTOR_OWNER, step_up=tp.StepUp(method="passkey", verified_at=1))
        r = as_assistant.call(
            "SubmitTrainingJob", ms.SubmitTrainingJobRequest(config=_cfg(lineage="lin-1"), caller=claimed_owner)
        )
        assert r.job.launched_by.actor == tp.ACTOR_ANALYST and not r.job.launched_by.HasField("step_up")
        with pytest.raises(grpc.RpcError) as e:
            as_assistant.call("RegisterModel", ms.RegisterModelRequest(run_id="x", name="y"))
        assert e.value.code() == grpc.StatusCode.PERMISSION_DENIED
        with pytest.raises(grpc.RpcError) as e2:
            Client(ch, SERVICE, None, "ml").call("ListModelFamilies", ms.ListModelFamiliesRequest())
        assert e2.value.code() == grpc.StatusCode.UNAUTHENTICATED
        fams = Client(ch, SERVICE, gateway, "ml").call("ListModelFamilies", ms.ListModelFamiliesRequest())
        names = {f.family for f in fams.families}
        assert {"ridge", "lgbm_regression", "lgbm_lambdarank", "orb_meta", "residual_reversal_meta"} <= names
        schema = json.loads(next(f for f in fams.families if f.family == "lgbm_regression").hyperparam_schema_json)
        assert schema["properties"]["num_leaves"]["minimum"] == 2
    finally:
        server.stop(None)
