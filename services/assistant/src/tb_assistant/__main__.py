"""assistant service: the AI Research Analyst and chat (section 12; ADR-0041..ADR-0043).

Wires the deterministic loop to its dependencies: Postgres (journal in schema ``analyst``, audit,
refdata for masking), NATS (triggers in, ``analyst.*`` and alerts out), the section 10/11 research
services over signed gRPC, the LLM providers per role (behind ``llm-egress`` in Compose), the
sandbox jobs volume, and the gateway-facing ``AnalystService``. Ops surface on ``:8081``.

``python -m tb_assistant egress-proxy`` runs the ``llm-egress`` proxy instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import threading
from datetime import UTC, datetime, timedelta

import asyncpg
import nats
import uvicorn
from prometheus_client import CollectorRegistry, Gauge

from tb.analyst.v1 import analyst_pb2
from tb.ml.v1 import ml_service_pb2, training_pb2
from tb_assistant import egress_proxy
from tb_assistant.audit import PgAudit
from tb_assistant.budget import BudgetGuard, default_budget
from tb_assistant.bus import FeedbackCache, JetStreamPublisher, subscribe_all, trigger_from_backtest
from tb_assistant.chat import Chat
from tb_assistant.coder import Coder, FileSandbox
from tb_assistant.config import Settings
from tb_assistant.guarded import GuardedLLM, SpendLedger
from tb_assistant.journal import PgJournal, day_start, month_start, retention_cutoff
from tb_assistant.llm.factory import build_providers
from tb_assistant.loop import AnalystLoop, LoopSettings
from tb_assistant.planner import Planner
from tb_assistant.quarantine import Quarantine
from tb_assistant.refdata import PgSymbols
from tb_assistant.research import ML, OPT, GrpcResearch
from tb_assistant.service import ALLOW, AnalystServicer
from tb_assistant.tools import ToolServer
from tbcore.logging import configure
from tbcore.rpc import serve
from tbcore.secrets import get_secret
from tbcore.service import create_ops_app
from tbcore.strategy.api import new_ulid
from tbcore.svcauth import Signer, Verifier

log = logging.getLogger("assistant")


def loop_settings(cfg: Settings) -> LoopSettings:
    s = LoopSettings(
        plateau_k=cfg.plateau_k,
        plateau_epsilon=cfg.plateau_epsilon,
        no_edge_min_trials=cfg.no_edge_min_trials,
        no_edge_max_dsr=cfg.no_edge_max_dsr,
        no_edge_min_pbo=cfg.no_edge_min_pbo,
        unreachable_dsr=cfg.unreachable_dsr,
        unreachable_min_trials=cfg.unreachable_min_trials,
        max_failed_rounds=cfg.max_failed_rounds,
        max_active_loops=cfg.max_active_loops,
    )
    s.default_budget.CopyFrom(
        default_budget(
            max_trials_per_round=cfg.budget_max_trials_per_round,
            max_cpu_hours_per_day=cfg.budget_max_cpu_hours_per_day,
            max_concurrent_jobs=cfg.budget_max_concurrent_jobs,
            max_rounds=cfg.budget_max_rounds,
            max_wall_clock_hours=cfg.budget_max_wall_clock_hours,
            max_llm_usd_per_day=cfg.max_llm_usd_per_day,
            max_llm_usd_per_month=cfg.max_llm_usd_per_month,
        )
    )
    s.promotion_limits.CopyFrom(
        analyst_pb2.PromotionLimits(
            max_promotions=cfg.promo_max_promotions,
            window_days=cfg.promo_window_days,
            min_shadow_sessions=cfg.promo_min_shadow_sessions,
            cooldown_days=cfg.promo_cooldown_days,
        )
    )
    return s


async def run(cfg: Settings, stop: asyncio.Event, ready: dict[str, bool]) -> None:
    secrets = cfg.secrets_dir
    db_pw = get_secret(f"pg_{cfg.db_user}_password", secrets).decode().strip()
    pool = await asyncpg.create_pool(
        host=cfg.db_host,
        port=cfg.db_port,
        database=cfg.db_name,
        user=cfg.db_user,
        password=db_pw,
        min_size=1,
        max_size=8,
    )
    journal, audit = PgJournal(pool), PgAudit(pool)
    nats_pw = get_secret(f"nats_{cfg.nats_user}_password", secrets).decode().strip()
    nc = await nats.connect(
        servers=[cfg.nats_url], user=cfg.nats_user, password=nats_pw, name="assistant", max_reconnect_attempts=-1
    )
    js = nc.jetstream()
    pub = JetStreamPublisher(js)
    signer = Signer.from_secrets("assistant", secrets)
    research = GrpcResearch({ML: cfg.ml_addr, OPT: cfg.optimizer_addr}, signer)

    now = datetime.now(tz=UTC)

    async def spend_alert(msg: str) -> None:
        await pub.alert("warning", "Analyst LLM spend", msg, "analyst-llm-spend")

    spend = SpendLedger(cfg.max_llm_usd_per_day, cfg.max_llm_usd_per_month, on_alert=spend_alert)
    spend.seed(
        now, today_usd=await journal.llm_spend(day_start(now)), month_usd=await journal.llm_spend(month_start(now))
    )
    llm = GuardedLLM(build_providers(cfg), spend=spend, call_log=journal, prices=cfg.prices())

    async def budget_alert(lineage: str, msg: str) -> None:
        await pub.alert("warning", "Analyst budget", f"lineage {lineage}: {msg}", f"analyst-budget-{lineage}")

    budget = BudgetGuard(lambda: datetime.now(tz=UTC), on_alert=budget_alert)
    feedback = FeedbackCache()
    tools = ToolServer(
        research,
        audit=audit,
        budget=budget,
        call_log=journal,
        feedback=feedback,
        max_result_bytes=cfg.tool_result_max_bytes,
    )
    symbols = PgSymbols(pool)
    ids_n = iter(range(1, 10**12))

    def ids() -> str:
        return new_ulid(seed=f"assistant-{next(ids_n)}-{datetime.now(tz=UTC).timestamp()}")

    coder = Coder(
        llm,
        FileSandbox(cfg.sandbox_jobs_dir, cfg.sandbox_timeout_seconds),
        journal,
        audit,
        ids=ids,
        clock=lambda: datetime.now(tz=UTC),
    )
    loop = AnalystLoop(
        tools=tools,
        planner=Planner(llm),
        quarantine=Quarantine(llm),
        journal=journal,
        publisher=pub,
        audit=audit,
        budget=budget,
        settings=loop_settings(cfg),
        symbols=symbols,
        account_ids={a.strip() for a in cfg.account_ids.split(",") if a.strip()},
        ids=ids,
        coder=coder.propose,
    )
    chat = Chat(llm, tools, journal, symbols, ids)

    async def on_backtest(m: training_pb2.BacktestCompleted) -> None:
        if not m.run_id:
            return
        r = await research.call(ML, "GetRun", ml_service_pb2.GetRunRequest(run_id=m.run_id))
        lineage = getattr(getattr(r, "run", None), "lineage_id", "")
        if lineage:
            await loop.on_trigger(trigger_from_backtest(m, lineage))

    async def on_lower(m: analyst_pb2.LowerAutonomy) -> None:
        await loop.lower_autonomy(
            m.lineage_id,
            m.level,
            pause=m.pause,
            actor=m.actor or "monitor",
            reason=m.reason,
            evidence=m.evidence_report_id,
        )

    await subscribe_all(js, on_trigger=loop.on_trigger, on_backtest=on_backtest, feedback=feedback, on_lower=on_lower)

    verifier = Verifier.from_secrets("assistant", ["api-gateway"], secrets)
    servicer = AnalystServicer(loop, journal, tools, chat, coder, asyncio.get_running_loop())
    server, port = serve(
        cfg.grpc_addr, [(analyst_pb2.DESCRIPTOR.services_by_name["AnalystService"], servicer, ALLOW)], verifier
    )
    server.start()
    log.info(
        "assistant ready", extra={"grpc_port": port, "providers": {r.value: p.name for r, p in llm.providers.items()}}
    )
    ready["ok"] = True
    while not stop.is_set():
        with contextlib.suppress(Exception):
            purged = await journal.purge_conversations(retention_cutoff(datetime.now(tz=UTC), cfg.chat_retention_days))
            if purged:
                log.info("purged conversations past retention", extra={"count": purged})
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=timedelta(hours=6).total_seconds())
    server.stop(5)
    await nc.drain()
    await research.close()
    await pool.close()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "egress-proxy":
        egress_proxy.main()
        return
    cfg = Settings()
    configure(cfg.service, cfg.log_level)
    ready: dict[str, bool] = {"ok": False}

    async def check() -> None:
        if not ready["ok"]:
            raise RuntimeError("starting")

    registry = CollectorRegistry()
    Gauge("tb_assistant_up", "Assistant started.", registry=registry).set(1)
    app = create_ops_app(cfg, {"loop": check}, registry)
    threading.Thread(
        target=lambda: uvicorn.run(app, host=cfg.ops_host, port=cfg.ops_port, log_config=None, access_log=False),
        daemon=True,
    ).start()
    asyncio.run(run(cfg, asyncio.Event(), ready))


if __name__ == "__main__":
    main()
