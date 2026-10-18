"""Gate 5 monitoring driver (section 13 acceptance; MN-035, MN-037, MN-038, MN-032, RM-018).

Runs the real monitor core (statistics, drift, divergence on recorded sessions, containment) against
the paper harness's execution service over gRPC with the monitor's Ed25519 service token, and
publishes the monitor's alerts and events on the harness's NATS. One phase per invocation; the Go
test (``monitoring_gate_test.go``) checks execution's side between phases. Prints one JSON line.

    uv run --frozen --package monitor python tests/paper/monitor_gate.py --phase drift ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import grpc
import nats
import numpy as np
from google.protobuf.json_format import MessageToDict
from tb_monitor import containment as ct
from tb_monitor.core import Monitor
from tb_monitor.recorder import SessionRecorder, xnys_session
from tb_monitor.strategies import Expectation, Monitored, Reference

from tb.common.v1 import common_pb2
from tb.features.v1 import features_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tbcore.audit import MemoryAudit
from tbcore.backtest.recording import iter_recording
from tbcore.docstore import MemoryDocStore
from tbcore.rpc import Client
from tbcore.svcauth import Signer

NS = 1_000_000_000
ACCOUNT = "sim-paper"
OWNER = ex.Actor(name="owner", step_up_method="passkey")


class Bus:
    """Collects the monitor's publications; they go to NATS JetStream at the end of the phase."""

    def __init__(self) -> None:
        self.msgs: list[tuple[str, Any, str]] = []

    def __call__(self, subject: str, msg: Any, msg_id: str) -> None:
        self.msgs.append((subject, msg, msg_id))

    async def flush(self, url: str) -> None:
        nc = await nats.connect(url)
        js = nc.jetstream()
        for subject, msg, msg_id in self.msgs:
            await js.publish(subject, msg.SerializeToString(), headers={"Nats-Msg-Id": msg_id})
        await nc.drain()


def executor(addr: str, secrets_dir: str) -> ct.GrpcExecutor:
    svc = ex.DESCRIPTOR.services_by_name["ExecutionService"]
    signer = Signer.from_secrets("monitor", secrets_dir)
    channel = grpc.insecure_channel(addr)  # type: ignore[no-untyped-call]
    return ct.GrpcExecutor(Client(channel, svc, signer, "execution", timeout=10.0))


def expectation(seed: int, mean: float, sd: float) -> Expectation:
    rng = np.random.default_rng(seed)
    return Expectation(rng.normal(mean, sd, 250), rng.normal(3, 60, 600), 1.0, 4.0, "gate5-backtest", "gate5-run", 2.4)


def model_strategy(sid: str, short: str, mode: str, ref: Reference | None = None) -> Monitored:
    return Monitored(
        strategy_id=sid,
        short_id=short,
        horizon="intraday",
        timeframe="15m",
        mode=mode,
        allocation=Decimal(10_000),
        lineage_id="gate5",
        model_version="v1",
        entry={},
        expectation=expectation(3, 5, 40),
        reference=ref,
        instruments=["I1"],
    )


def drift_reference() -> Reference:
    rng = np.random.default_rng(5)
    return Reference(
        features={"f_mom": rng.normal(0, 1, 3000), "f_vol": rng.normal(0, 1, 3000), "f_low": rng.normal(0, 1, 3000)},
        predictions=rng.normal(0, 1, 3000),
        labels=rng.normal(0, 1, 3000),
        importance={"f_mom": 1, "f_vol": 2, "f_low": 9},
        run_id="gate5-run",
        feature_set="core_intraday@1",
    )


def feed_features(mon: Monitor, sid: str, n: int, shift: float) -> None:
    """Live feature vectors and model signals; ``shift`` moves the top-importance feature."""
    rng = np.random.default_rng(9)
    for i in range(n):
        t = (1 + i // 26) * 86_400 * NS + i * 60 * NS
        fv = features_pb2.FeatureVector(
            feature_set_id="core_intraday",
            instrument_id="I1",
            as_of=t,
            values={"f_mom": rng.normal(shift, 1), "f_vol": rng.normal(0, 1), "f_low": rng.normal(0, 1)},
        )
        mon.on_message("feat.core_intraday.I1", fv.SerializeToString())
        sig = signals_pb2.Signal(
            signal_id=f"{sid}-{i}",
            strategy_id=sid,
            instrument_id="I1",
            score=float(rng.normal(0, 1)),
            as_of=t,
            direction=signals_pb2.DIRECTION_LONG if i % 5 == 0 else signals_pb2.DIRECTION_FLAT,
        )
        mon.on_message(f"sig.{sid}.I1", sig.SerializeToString())


def execution(coid: str, side: Any, qty: str, px: str, t: int, xid: str) -> bytes:
    report = orders_pb2.ExecutionReport(
        broker_account_id=ACCOUNT,
        broker_exec_id=xid,
        client_order_id=coid,
        instrument_id="I1",
        side=side,
        qty=common_pb2.Decimal(value=qty),
        price=common_pb2.Decimal(value=px),
        fees=common_pb2.Decimal(value="0"),
        event_time=t,
    )
    return bytes(report.SerializeToString())


def losing_sessions(mon: Monitor, short: str, sessions: int, loss: str) -> None:
    """One 100-share round trip per session losing ``loss`` per share."""
    for d in range(sessions):
        t = (d + 1) * 86_400 * NS
        coid = f"tb1-{short}-01JGATE5{d:018d}"
        exit_px = str(Decimal(100) - Decimal(loss))
        mon.on_message("exec." + ACCOUNT, execution(coid + "-1", common_pb2.SIDE_BUY, "100", "100", t, f"b{d}"))
        mon.on_message("exec." + ACCOUNT, execution(coid + "-2", common_pb2.SIDE_SELL, "100", exit_px, t + 1, f"s{d}"))
        mon.close_session(f"2026-09-{d + 1:02d}")


def split_recording(fixture: Path, directory: Path, drop_bars_of: list[str]) -> SessionRecorder:
    """Split the recorded paper sessions per XNYS session, without the bars of some instruments: the
    replay never sees them, so every live intent in them is missed (injected divergence)."""
    rec = SessionRecorder(directory, xnys_session)
    with open(fixture, "rb") as f:
        msgs = sorted(iter_recording(f), key=lambda m: m.recorded_at)
    for seq, m in enumerate(msgs, start=1):
        if m.subject.startswith("md.bar.") and any(i in m.subject for i in drop_bars_of):
            continue
        rec.write(m.subject, m.data, m.msg_id, seq, m.recorded_at)
    rec.rollover(msgs[-1].recorded_at + 3600 * NS)
    return rec


def orb_strategy(config: Path, mode: str) -> Monitored:
    s = json.loads(config.read_text())["strategies"][0]
    entry = {
        "strategy_id": s["strategy_id"],
        "short_id": s["short_id"],
        "horizon": s["horizon"],
        "broker_account_id": s["broker_account_id"],
        "allocation": s["allocation"],
        "segregation_allowlist": s["segregation_allowlist"],
        "version": s["version"],
        "mode": mode,
        "runtime": {
            "class": s["runtime_class"],
            "timeframe": s["timeframe"],
            "instruments": s["instruments"],
            "params": s["params"],
        },
        "limits": s["limits"],
    }
    return Monitored(
        s["strategy_id"],
        s["short_id"],
        s["horizon"],
        s["timeframe"],
        mode,
        Decimal(s["allocation"]),
        "orb",
        s["version"],
        entry,
        expectation=Expectation(np.random.default_rng(1).normal(10, 60, 250), np.zeros(0), 0.0, 0.0),
    )


def component(h: mp.StrategyHealth, name: str) -> dict[str, Any]:
    c = next(c for c in h.components if c.name == name)
    return {"status": mp.HealthStatus.Name(c.status), "value": c.value, "detail": c.detail}


def run(args: argparse.Namespace) -> dict[str, Any]:
    store, audit, bus = MemoryDocStore(), MemoryAudit(), Bus()
    contain = ct.Containment(store, audit, executor(args.exec_addr, args.secrets_dir), bus)
    out: dict[str, Any] = {"phase": args.phase, "strategy": args.strategy, "mode": args.mode, "steps": []}
    if args.phase == "drift":
        m = model_strategy(args.strategy, args.short_id, args.mode, drift_reference())
        mon = Monitor({m.strategy_id: m}, store, contain, bus)
        for label, n, shift in (("baseline", 400, 0.0), ("moderate", 5000, 0.6), ("severe", 5000, 2.0)):
            feed_features(mon, m.strategy_id, n, shift)
            h = mon.evaluate(m.strategy_id)
            out["steps"].append({"step": label, "feature_drift": component(h, "feature_drift"), "mode": h.mode})
        top = mon.drift(m.strategy_id).features[0]
        out["top_feature"] = {"feature": top.feature, "psi": top.psi, "importance_rank": top.importance_rank}
    elif args.phase == "divergence":
        rec = split_recording(Path(args.recording), Path(args.workdir), [i for i in args.drop_bars_of.split(",") if i])
        m = orb_strategy(Path(args.config), args.mode)
        pol = mp.ContainmentPolicy()
        pol.CopyFrom(ct.DEFAULT)
        pol.strategy_id, pol.min_matched_intents = m.strategy_id, 3
        contain.set_policy(pol, OWNER, "five recorded fixture sessions: small sample")
        mon = Monitor({m.strategy_id: m}, store, contain, bus, recorder=rec)
        reports = [mon.run_divergence(m.strategy_id, d) for d in rec.sessions()]
        out["sessions"] = [
            {
                "date": r.session_date,
                "matched": r.matched_intents,
                "missed": r.missed_intents,
                "trade_match_rate": r.parity.trade_match_rate,
                "recording_complete": r.recording_complete,
            }
            for r in reports
            if r is not None
        ]
        h = mon.evaluate(m.strategy_id)
        out["steps"].append({"step": "evaluate", "divergence": component(h, "divergence"), "mode": h.mode})
    elif args.phase == "drawdown":
        m = model_strategy(args.strategy, args.short_id, args.mode)
        mon = Monitor({m.strategy_id: m}, store, contain, bus)
        losing_sessions(mon, m.short_id, 3, "6")
        h = mon.evaluate(m.strategy_id)
        out["steps"].append({"step": "evaluate", "drawdown": component(h, "drawdown"), "mode": h.mode})
        perf = mon.performance(m.strategy_id)
        out["drawdown"] = {"pct": perf.drawdown_pct, "p95_pct": perf.drawdown_p95_pct, "p99_pct": perf.drawdown_p99_pct}
    else:
        raise SystemExit(f"unknown phase {args.phase}")
    out["actions"] = [MessageToDict(a, preserving_proto_field_name=True) for a in contain.actions(args.strategy)]
    out["monitor_audit"] = [e.action for e in audit.events]
    out["published"] = [s for s, _, _ in bus.msgs]
    asyncio.run(bus.flush(args.nats_url))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--phase", required=True, choices=["drift", "divergence", "drawdown"])
    p.add_argument("--strategy", required=True)
    p.add_argument("--short-id", default="")
    p.add_argument("--mode", required=True, choices=["paper", "live"])
    p.add_argument("--exec-addr", required=True)
    p.add_argument("--secrets-dir", required=True)
    p.add_argument("--nats-url", required=True)
    p.add_argument("--recording", default="tests/fixtures/parity/orb-paper-gate.tbrec")
    p.add_argument("--config", default="tests/fixtures/parity/orb-paper-gate.config.json")
    p.add_argument("--drop-bars-of", default="01JAAPL,01JNVDA", help="comma-separated instrument ID prefixes")
    p.add_argument("--workdir", default="")
    print(json.dumps(run(p.parse_args())))


if __name__ == "__main__":
    main()
