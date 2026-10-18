"""optimizer worker entrypoint (section 11): ``OptimizerService`` and ``HoldoutService`` on
``TB_OPT_GRPC_ADDR``, the scheduled-retrain ticker, and the ops surface (OP-010)."""

from __future__ import annotations

import sys
import threading
import time

import uvicorn
from prometheus_client import CollectorRegistry, Gauge
from pydantic_settings import SettingsConfigDict

from tb_optimizer.core import OptimizerCore
from tb_optimizer.grpc_api import HOLD, HOLD_ALLOW, OPT, OPT_ALLOW, HoldoutServicer, OptimizerServicer
from tbcore.audit import AuditSink, MemoryAudit, PostgresAudit
from tbcore.buspub import MemoryPublisher, NatsPublisher, Publisher
from tbcore.docstore import DocStore, MemoryDocStore, PostgresDocStore
from tbcore.logging import configure
from tbcore.ml.tracking import MlflowTracker, NullTracker, Tracker
from tbcore.rpc import serve
from tbcore.secrets import get_secret
from tbcore.service import ServiceSettings, create_ops_app
from tbcore.svcauth import Verifier


class Settings(ServiceSettings):
    model_config = SettingsConfigDict(env_prefix="TB_", extra="ignore")

    service: str = "optimizer"
    opt_grpc_addr: str = "0.0.0.0:9091"
    ml_artifacts_dir: str = "data/ml-artifacts"
    lake_dir: str = ""
    ml_mlflow_url: str = ""
    holdout_months: int = 12
    opt_optuna_storage: str = ""
    opt_deploy_registry: str = ""
    opt_paper_account_intraday: str = "sim-paper"
    opt_paper_account_swing: str = "sim-paper"
    graduation_min_sessions: int = 20
    opt_global_analyst_trials: int = 0
    # Candidate deployment limit profiles per horizon (EX-062); empty = shipped defaults.
    opt_deployment_limits: str = ""
    nats_url: str = ""
    nats_user: str = "optimizer"
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "platform"
    db_user: str = ""
    holdout_db_user: str = ""


def _dsn(s: Settings, user: str) -> str:
    pw = get_secret(f"pg_{user}_password", s.secrets_dir).decode()
    return f"postgresql://{user}:{pw}@{s.db_host}:{s.db_port}/{s.db_name}"


def build(s: Settings) -> tuple[OptimizerCore, AuditSink]:
    store: DocStore = MemoryDocStore()
    holdout: DocStore = MemoryDocStore()
    audit: AuditSink = MemoryAudit()
    if s.db_user:
        store, audit = PostgresDocStore(_dsn(s, s.db_user), "research"), PostgresAudit(_dsn(s, s.db_user))
    if s.holdout_db_user:
        holdout = PostgresDocStore(_dsn(s, s.holdout_db_user), "holdout")  # EX-012: only svc_holdout
    bus: Publisher = MemoryPublisher()
    if s.nats_url:
        pw = get_secret(f"nats_{s.nats_user}_password", s.secrets_dir).decode()
        bus = NatsPublisher(s.nats_url, s.nats_user, pw, "optimizer")
    tracker: Tracker = MlflowTracker(s.ml_mlflow_url) if s.ml_mlflow_url else NullTracker()
    core = OptimizerCore(
        store,
        s.ml_artifacts_dir,
        holdout_store=holdout,
        audit=audit,
        bus=bus,
        tracker=tracker,
        lake_dir=s.lake_dir,
        holdout_months=s.holdout_months,
        optuna_storage=s.opt_optuna_storage or None,
        deploy_registry=s.opt_deploy_registry,
        paper_account_intraday=s.opt_paper_account_intraday,
        paper_account_swing=s.opt_paper_account_swing,
        graduation_min_sessions=s.graduation_min_sessions,
        global_analyst_trials=s.opt_global_analyst_trials,
        deployment_profiles=s.opt_deployment_limits,
    )
    return core, audit


def main(argv: list[str] | None = None) -> int:
    s = Settings()
    log = configure("optimizer", s.log_level)
    core, audit = build(s)
    callers = ["api-gateway", "assistant", "ml", "optimizer", "monitor", "execution"]
    verifier = Verifier.from_secrets("optimizer", callers, s.secrets_dir)
    holdout_verifier = Verifier.from_secrets("holdout", callers, s.secrets_dir)
    server, port = serve(s.opt_grpc_addr, [(OPT, OptimizerServicer(core, audit), OPT_ALLOW)], verifier)
    # The holdout service shares the process but has its own audience ("holdout") and allowlist.
    hold_addr = s.opt_grpc_addr.rsplit(":", 1)[0] + f":{int(s.opt_grpc_addr.rsplit(':', 1)[1]) + 1}"
    hserver, hport = serve(hold_addr, [(HOLD, HoldoutServicer(core, audit), HOLD_ALLOW)], holdout_verifier)
    server.start()
    hserver.start()
    log.info("optimizer listening", extra={"port": port, "holdout_port": hport})

    def ticker() -> None:
        while True:
            time.sleep(60)
            try:
                core.tick()
            except Exception:
                log.exception("retrain tick failed")

    threading.Thread(target=ticker, name="retrain-ticker", daemon=True).start()
    reg = CollectorRegistry()
    running = Gauge("tb_optimizer_studies_running", "Studies running on this worker.", registry=reg)
    running.set_function(lambda: sum(1 for w in core._studies.values() if w.study.status == 2))

    async def grpc_ready() -> None:
        if port <= 0 or hport <= 0:
            raise RuntimeError("grpc not bound")

    uvicorn.run(
        create_ops_app(s, {"grpc": grpc_ready}, registry=reg), host=s.ops_host, port=s.ops_port, log_config=None
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
