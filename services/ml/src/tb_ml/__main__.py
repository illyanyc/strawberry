"""ml worker entrypoint (section 10, ADR-0037).

``python -m tb_ml`` serves ``MLService`` on ``TB_ML_GRPC_ADDR`` plus the ops surface (OP-010).
``python -m tb_ml train --config cfg.json`` trains one config in process (owner CLI; prints the run ID,
findings, and out-of-sample metrics).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import uvicorn
from google.protobuf import json_format
from prometheus_client import CollectorRegistry, Gauge
from pydantic_settings import SettingsConfigDict

from tb.ml.v1 import training_pb2 as tp
from tb_ml.core import MLCore
from tb_ml.devices import BurstHook
from tb_ml.grpc_api import ALLOW, SERVICE, MLServicer
from tb_ml.link import GrpcLink, NoLink, ResearchLink
from tbcore.audit import AuditSink, MemoryAudit, PostgresAudit
from tbcore.buspub import MemoryPublisher, NatsPublisher, Publisher
from tbcore.docstore import DocStore, MemoryDocStore, PostgresDocStore
from tbcore.logging import configure
from tbcore.ml.tracking import MlflowTracker, NullTracker, Tracker
from tbcore.rpc import serve
from tbcore.secrets import get_secret
from tbcore.service import ServiceSettings, create_ops_app
from tbcore.svcauth import Signer, Verifier


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="TB_", extra="ignore")

    service: str = "ml"
    ml_grpc_addr: str = "0.0.0.0:9090"
    ml_artifacts_dir: str = "data/ml-artifacts"
    lake_dir: str = ""
    ml_mlflow_url: str = ""
    ml_job_budget_cpu_seconds: float = 3600.0
    ml_workers: int = 1
    ml_burst_url: str = ""
    optimizer_addr: str = ""
    holdout_addr: str = ""
    nats_url: str = ""
    nats_user: str = "ml"
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "platform"
    db_user: str = ""


def _dsn(s: Settings) -> str:
    pw = get_secret(f"pg_{s.db_user}_password", s.secrets_dir).decode()
    return f"postgresql://{s.db_user}:{pw}@{s.db_host}:{s.db_port}/{s.db_name}"


def build(s: Settings) -> tuple[MLCore, AuditSink]:
    store: DocStore = MemoryDocStore()
    audit: AuditSink = MemoryAudit()
    if s.db_user:
        dsn = _dsn(s)
        store, audit = PostgresDocStore(dsn, "research"), PostgresAudit(dsn)
    bus: Publisher = MemoryPublisher()
    if s.nats_url:
        bus = NatsPublisher(
            s.nats_url, s.nats_user, get_secret(f"nats_{s.nats_user}_password", s.secrets_dir).decode(), "ml"
        )
    tracker: Tracker = MlflowTracker(s.ml_mlflow_url) if s.ml_mlflow_url else NullTracker()
    link: ResearchLink = NoLink()
    if s.optimizer_addr:
        signer = Signer.from_secrets("ml", s.secrets_dir)
        link = GrpcLink(s.optimizer_addr, s.holdout_addr or s.optimizer_addr, signer)
    burst = None
    if s.ml_burst_url:
        token_path = Path(s.secrets_dir) / "ml_burst_token"
        burst = BurstHook(
            s.ml_burst_url, get_secret("ml_burst_token", s.secrets_dir).decode() if token_path.exists() else ""
        )
    core = MLCore(
        store,
        s.ml_artifacts_dir,
        link=link,
        audit=audit,
        bus=bus,
        tracker=tracker,
        lake_dir=s.lake_dir,
        budget_cpu_seconds=s.ml_job_budget_cpu_seconds,
        workers=s.ml_workers,
        burst=burst,
    )
    return core, audit


def _serve(s: Settings) -> None:
    log = configure("ml", s.log_level)
    core, audit = build(s)
    verifier = Verifier.from_secrets("ml", ["api-gateway", "optimizer", "assistant"], s.secrets_dir)
    server, port = serve(s.ml_grpc_addr, [(SERVICE, MLServicer(core, audit), ALLOW)], verifier)
    server.start()
    log.info("MLService listening", extra={"port": port, "store": "postgres" if s.db_user else "memory"})
    reg = CollectorRegistry()
    queued = Gauge("tb_ml_jobs_queued", "Training jobs waiting in the queue.", registry=reg)
    queued.set_function(lambda: len(core._queue))

    async def grpc_ready() -> None:
        if port <= 0:
            raise RuntimeError("grpc not bound")

    app = create_ops_app(s, {"grpc": grpc_ready}, registry=reg)
    uvicorn.run(app, host=s.ops_host, port=s.ops_port, log_config=None, access_log=False)


def _train(args: argparse.Namespace, s: Settings) -> int:
    configure("ml", s.log_level)
    cfg = json_format.Parse(Path(args.config).read_text(), tp.TrainingConfig())
    core = MLCore(MemoryDocStore(), args.artifacts or s.ml_artifacts_dir, lake_dir=s.lake_dir, start=False)
    owner = tp.CallerContext(actor=tp.ACTOR_OWNER, actor_id="cli")
    job = core.submit(cfg, owner, confirm_over_budget=args.confirm)
    core.run_pending()
    done = core.job(job.job_id)
    if done.status != tp.JOB_STATUS_SUCCEEDED:
        print(json.dumps({"job_id": done.job_id, "status": tp.JobStatus.Name(done.status), "error": done.error}))
        return 1
    run = core.run(done.run_id)
    rep = core.diagnostics(run.run_id)
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "artifacts": run.artifact_uri,
                "findings": [f.code for f in rep.findings],
                "oos_metrics": dict(run.oos_metrics),
            },
            indent=1,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tb_ml")
    sub = p.add_subparsers(dest="cmd")
    t = sub.add_parser("train", help="train one TrainingConfig (JSON) in process")
    t.add_argument("--config", required=True)
    t.add_argument("--artifacts", default="")
    t.add_argument("--confirm", action="store_true", help="confirm a job over the compute budget (ML-031)")
    args = p.parse_args(argv)
    s = Settings()
    if args.cmd == "train":
        return _train(args, s)
    _serve(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
