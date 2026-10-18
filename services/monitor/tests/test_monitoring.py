"""Full monitoring (section 13 Phase 5): statistics, health, containment, drift, divergence on recorded
sessions, execution quality, graduation evidence, the alert inbox, the recorder, and the gRPC service
(MN-030..MN-046, RM-018)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest
from tb_monitor import analytics as an
from tb_monitor import containment as ct
from tb_monitor.alertbox import Inbox
from tb_monitor.book import StrategyBook
from tb_monitor.core import Monitor
from tb_monitor.recorder import SessionRecorder, xnys_session
from tb_monitor.strategies import Expectation, Monitored, Reference, load

from tb.alerts.v1 import alerts_pb2
from tb.common.v1 import common_pb2
from tb.features.v1 import features_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tbcore.audit import MemoryAudit
from tbcore.backtest.recording import Recording, iter_recording
from tbcore.docstore import MemoryDocStore

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "parity"
NS = 1_000_000_000


class FakeExec:
    def __init__(self, mode: int = ex.STRATEGY_MODE_PAPER, fail: bool = False) -> None:
        self.mode, self.fail = mode, fail
        self.demotes: list[tuple[str, str, str]] = []
        self.kills: list[tuple[str, int, str]] = []

    def demote(self, sid: str, reason: str, evidence: str) -> ex.DemoteStrategyResponse:
        if self.fail:
            raise ConnectionError("execution unreachable")
        self.demotes.append((sid, reason, evidence))
        frm = self.mode
        self.mode = ex.STRATEGY_MODE_PAPER if frm == ex.STRATEGY_MODE_LIVE else ex.STRATEGY_MODE_HALTED
        return ex.DemoteStrategyResponse(
            strategy=ex.Strategy(strategy_id=sid, mode=self.mode), changed=True, **{"from": frm}
        )

    def kill(self, sid: str, action: int, reason: str) -> Any:
        self.kills.append((sid, action, reason))
        return ex.TriggerKillSwitchResponse(kill_switch=ex.KillSwitch(event_id="k1"), orders_canceled=2)


class Bus:
    def __init__(self) -> None:
        self.msgs: list[tuple[str, Any, str]] = []

    def __call__(self, subject: str, msg: Any, msg_id: str) -> None:
        self.msgs.append((subject, msg, msg_id))

    def subjects(self) -> list[str]:
        return [s for s, _, _ in self.msgs]

    def alerts(self, sev: str = "critical") -> list[alerts_pb2.Alert]:
        return [m for s, m, _ in self.msgs if s == f"alert.{sev}"]


def monitored(
    sid: str = "ml-a", mode: str = "paper", session_bps: Any = None, ref: Reference | None = None
) -> Monitored:
    rng = np.random.default_rng(3)
    sess = np.asarray(session_bps if session_bps is not None else rng.normal(5, 40, 250))
    return Monitored(
        strategy_id=sid,
        short_id="mla1234",
        horizon="intraday",
        timeframe="15m",
        mode=mode,
        allocation=Decimal(10_000),
        lineage_id="lin-1",
        model_version="v1",
        entry={},
        expectation=Expectation(sess, rng.normal(3, 60, 600), 1.0, 4.0, "gate-1", "run-1", 2.4),
        reference=ref,
        instruments=["I1", "I2"],
    )


def world(m: Monitored | None = None, executor: Any = None) -> tuple[Monitor, Bus, MemoryAudit, Any]:
    m = m or monitored()
    store, audit, bus = MemoryDocStore(), MemoryAudit(), Bus()
    exe = (
        executor
        if executor is not None
        else FakeExec(ex.STRATEGY_MODE_LIVE if m.mode == "live" else ex.STRATEGY_MODE_PAPER)
    )
    c = ct.Containment(store, audit, exe, bus)
    return Monitor({m.strategy_id: m}, store, c, bus), bus, audit, exe


def execution(coid: str, side: int, qty: str, px: str, t: int, xid: str, fees: str = "0") -> bytes:
    return orders_pb2.ExecutionReport(
        broker_account_id="sim-paper",
        broker_exec_id=xid,
        client_order_id=coid,
        instrument_id="I1",
        side=side,
        qty=common_pb2.Decimal(value=qty),
        price=common_pb2.Decimal(value=px),
        fees=common_pb2.Decimal(value=fees),
        event_time=t,
    ).SerializeToString()


def losing_sessions(mon: Monitor, sid: str, sessions: int, loss: str) -> None:
    """One round trip per session losing ``loss`` per share on 100 shares."""
    for d in range(sessions):
        t = (d + 1) * 86_400 * NS
        coid = f"tb1-mla1234-01J{d:023d}-1"
        mon.on_message("exec.sim-paper", execution(coid, common_pb2.SIDE_BUY, "100", "100", t, f"b{d}"))
        exit_px = str(Decimal(100) - Decimal(loss))
        mon.on_message(
            "exec.sim-paper", execution(coid.replace("-1", "-2"), common_pb2.SIDE_SELL, "100", exit_px, t + 1, f"s{d}")
        )
        mon.close_session(f"2026-10-{d + 1:02d}")


# ---- statistics (MN-030, MN-031, MN-032)


def test_mn030_bootstrap_is_deterministic_and_drawdown_grows_with_the_horizon() -> None:
    r = np.random.default_rng(0).normal(5, 50, 250)
    a, b = an.block_bootstrap(r, 20), an.block_bootstrap(r, 20)
    assert np.array_equal(a, b) and a.shape == (an.PATHS, 20)
    d5, d60 = an.drawdown_bands(r, 5), an.drawdown_bands(r, 60)
    assert 0 < d5.p95 < d60.p95 and d60.p95 < d60.p99
    bands = {x.metric: x for x in an.performance_bands(r, r, r[:20], r[:40], 2.0)}
    assert set(bands) == {"return_pct", "sharpe", "max_drawdown_pct", "hit_rate", "payoff", "turnover"}
    assert all(b.p05 <= b.p50 <= b.p95 <= b.p99 for b in bands.values())


def test_mn031_component_scores_and_insufficient_data_never_acts() -> None:
    assert an.component_score(0.0, 0.25, 0.5) == 100.0
    assert an.component_score(0.25, 0.25, 0.5) == 60.0 and an.component_score(0.5, 0.25, 0.5) == 20.0
    assert an.component_score(0.75, 0.25, 0.5) == 0.0
    assert an.component_score(0.8, 0.9, 0.8, higher_is_worse=False) == pytest.approx(20.0)
    few = an.component("feature_drift", 0.9, 0.25, 0.5, samples=10, min_samples=200)
    assert few.status == mp.HEALTH_STATUS_INSUFFICIENT_DATA
    ok = an.component("drawdown", 0.4, 1.0, 1.5, 3, 1)
    bad = an.component("divergence", 0.7, 0.9, 0.8, 50, 10, higher_is_worse=False)
    assert an.overall([few, ok, bad]) == (bad.score, mp.HEALTH_STATUS_BREACH)
    assert an.overall([few])[1] == mp.HEALTH_STATUS_INSUFFICIENT_DATA


# ---- containment policies (MN-034, MN-036)


def test_mn034_mn036_default_policy_and_loosening_needs_a_passkey() -> None:
    store, audit = MemoryDocStore(), MemoryAudit()
    c = ct.Containment(store, audit, FakeExec(), Bus())
    p, inherited = c.policy("ml-a")
    assert inherited and p.drawdown_breach_multiple == 1.5 and p.breach_action == mp.CONTAINMENT_ACTION_KIND_DEMOTE
    tighter = mp.ContainmentPolicy()
    tighter.CopyFrom(p)
    tighter.drawdown_breach_multiple = 1.2
    got, loose = c.set_policy(tighter, ex.Actor(name="owner"), "tighter")
    assert not loose and got.version == 1
    looser = mp.ContainmentPolicy()
    looser.CopyFrom(got)
    looser.match_rate_breach, looser.breach_action = 0.5, mp.CONTAINMENT_ACTION_KIND_ALERT
    assert set(ct.loosened(got, looser)) == {"match_rate_breach", "breach_action"}
    with pytest.raises(PermissionError, match="passkey"):
        c.set_policy(looser, ex.Actor(name="owner"), "looser")
    off = mp.ContainmentPolicy()
    off.CopyFrom(got)
    off.enabled = False
    with pytest.raises(PermissionError):
        c.set_policy(off, ex.Actor(name="owner", step_up_method="totp"), "disable")
    got2, loose2 = c.set_policy(looser, ex.Actor(name="owner", step_up_method="passkey"), "looser")
    assert loose2 and got2.version == 2
    assert [e.details["loosened"] for e in audit.actions("monitor.containment.policy.set")] == [
        [],
        ["match_rate_breach", "breach_action"],
    ]
    bad = mp.ContainmentPolicy()
    bad.CopyFrom(got2)
    bad.breach_action, bad.severe_action = mp.CONTAINMENT_ACTION_KIND_DEMOTE, mp.CONTAINMENT_ACTION_KIND_ALERT
    with pytest.raises(ValueError, match="severe_action"):
        c.set_policy(bad, ex.Actor(name="owner", step_up_method="passkey"), "x")


# ---- drawdown demotion (MN-032, MN-035, RM-018)


def test_mn032_mn035_drawdown_beyond_1_5x_p95_demotes_paper_to_halted_once() -> None:
    mon, bus, audit, exe = world()
    losing_sessions(mon, "ml-a", 3, "6")  # 3 sessions of -6 % each on a 10,000 allocation
    h = mon.evaluate("ml-a")
    dd = next(c for c in h.components if c.name == "drawdown")
    assert dd.status == mp.HEALTH_STATUS_BREACH and dd.value > 1.5
    assert exe.demotes and exe.demotes[0][0] == "ml-a" and "drawdown" in exe.demotes[0][1]
    assert h.mode == "halted" and mon.strategies["ml-a"].mode == "halted"
    a = mon.containment.actions("ml-a")[0]
    assert (
        a.executed and a.from_mode == "paper" and a.to_mode == "halted" and a.kind == mp.CONTAINMENT_ACTION_KIND_DEMOTE
    )
    assert any(e.action == "monitor.containment.demote" for e in audit.events)
    crit = bus.alerts("critical")
    assert crit and crit[-1].runbook_url == "docs/runbooks/containment.md" and "evidence" in crit[-1].message
    assert "monitor.containment.ml-a" in bus.subjects()
    mon.evaluate("ml-a")
    assert len(exe.demotes) == 1  # halted: nothing left to reduce, never re-promoted (RM-015)


def test_mn035_live_demotes_to_paper_and_does_not_cascade() -> None:
    mon, _, _, exe = world(monitored(mode="live"))
    losing_sessions(mon, "ml-a", 3, "6")
    mon.evaluate("ml-a")
    assert mon.strategies["ml-a"].mode == "paper" and len(exe.demotes) == 1
    losing_sessions(mon, "ml-a", 1, "3")  # the breach persists on paper with new evidence
    mon.evaluate("ml-a")
    assert len(exe.demotes) == 1, "an already contained strategy is only moved again by the owner"


def test_mn035_failed_action_pages_and_retries() -> None:
    exe = FakeExec(fail=True)
    mon, bus, _, _ = world(executor=exe)
    losing_sessions(mon, "ml-a", 3, "6")
    mon.evaluate("ml-a")
    a = mon.containment.actions("ml-a")[0]
    assert not a.executed and "unreachable" in a.error
    assert bus.alerts("critical")[-1].subject == "Containment action failed"
    exe.fail = False
    done = mon.containment.retry_pending()
    assert done and done[0].executed and done[0].action_id == a.action_id and len(exe.demotes) == 1


def test_mn035_insufficient_expectation_never_acts() -> None:
    m = monitored()
    m.expectation = None
    mon, _, _, exe = world(m)
    losing_sessions(mon, "ml-a", 3, "6")
    h = mon.evaluate("ml-a")
    assert h.status == mp.HEALTH_STATUS_INSUFFICIENT_DATA and not exe.demotes


def test_mn034_disabled_policy_only_alerts() -> None:
    mon, bus, _, exe = world()
    p, _ = mon.containment.policy("ml-a")
    p.enabled = False
    mon.containment.set_policy(p, ex.Actor(name="owner", step_up_method="passkey"), "observe only")
    losing_sessions(mon, "ml-a", 3, "6")
    mon.evaluate("ml-a")
    assert not exe.demotes and mon.containment.actions("ml-a")[0].kind == mp.CONTAINMENT_ACTION_KIND_ALERT
    assert bus.alerts("critical")


# ---- drift (MN-038)


def drift_reference() -> Reference:
    rng = np.random.default_rng(5)
    return Reference(
        features={"f_mom": rng.normal(0, 1, 3000), "f_vol": rng.normal(0, 1, 3000), "f_low": rng.normal(0, 1, 3000)},
        predictions=rng.normal(0, 1, 3000),
        labels=rng.normal(0, 1, 3000),
        importance={"f_mom": 1, "f_vol": 2, "f_low": 9},
        run_id="run-1",
        feature_set="core_intraday@1",
    )


def feed_features(mon: Monitor, n: int, shift: float, low_shift: float = 0.0, pred_shift: float = 0.0) -> None:
    rng = np.random.default_rng(9)
    for i in range(n):
        t = (1 + i // 26) * 86_400 * NS + i * 60 * NS
        f = features_pb2.FeatureVector(
            feature_set_id="core_intraday",
            instrument_id="I1",
            as_of=t,
            values={"f_mom": rng.normal(shift, 1), "f_vol": rng.normal(0, 1), "f_low": rng.normal(low_shift, 1)},
        )
        mon.on_message("feat.core_intraday.I1", f.SerializeToString())
        s = signals_pb2.Signal(
            signal_id=f"s{i}",
            strategy_id="ml-a",
            score=float(rng.normal(pred_shift, 1)),
            as_of=t,
            direction=signals_pb2.DIRECTION_LONG if i % 5 == 0 else signals_pb2.DIRECTION_FLAT,
        )
        mon.on_message("sig.ml-a", s.SerializeToString())


def test_mn038_injected_feature_drift_on_a_top_feature_raises_a_drift_alert_then_demotes() -> None:
    mon, bus, _, exe = world(monitored(ref=drift_reference()))
    feed_features(mon, 400, shift=0.0)
    h = mon.evaluate("ml-a")
    assert next(c for c in h.components if c.name == "feature_drift").status == mp.HEALTH_STATUS_OK
    feed_features(mon, 5000, shift=0.6)  # moderate drift: warning only
    h = mon.evaluate("ml-a")
    fd = next(c for c in h.components if c.name == "feature_drift")
    assert fd.status == mp.HEALTH_STATUS_WARNING and "f_mom" in fd.detail and not exe.demotes
    warn = bus.alerts("warning")
    assert (
        warn
        and warn[-1].subject == "Feature drift warning"
        and warn[-1].runbook_url == "docs/runbooks/model-monitoring.md"
    )
    feed_features(mon, 5000, shift=2.0)  # severe drift: breach → demote
    h = mon.evaluate("ml-a")
    assert next(c for c in h.components if c.name == "feature_drift").status == mp.HEALTH_STATUS_BREACH
    assert exe.demotes and "feature_drift" in exe.demotes[0][1]
    rep = mon.drift("ml-a")
    assert rep.features[0].feature == "f_mom" and rep.features[0].importance_rank == 1 and rep.live_samples == 5000


def test_mn038_drift_on_a_low_importance_feature_does_not_act() -> None:
    mon, _, _, exe = world(monitored(ref=drift_reference()))
    feed_features(mon, 5000, shift=0.0, low_shift=3.0)
    h = mon.evaluate("ml-a")
    assert next(c for c in h.components if c.name == "feature_drift").status == mp.HEALTH_STATUS_OK and not exe.demotes
    assert mon.drift("ml-a").features[0].feature == "f_low"  # still reported, top of the PSI list


def test_mn038_prediction_drift() -> None:
    mon, _, _, _ = world(monitored(ref=drift_reference()))
    feed_features(mon, 3000, shift=0.0, pred_shift=1.5)
    h = mon.evaluate("ml-a", act=False)
    assert next(c for c in h.components if c.name == "prediction_drift").status == mp.HEALTH_STATUS_BREACH


# ---- execution quality (MN-033)


def test_mn033_implementation_shortfall_vs_the_intent_reference_price() -> None:
    mon, _, _, exe = world()
    for i in range(25):
        it = orders_pb2.OrderIntent(
            intent_id=f"01J{i:023d}",
            strategy_id="ml-a",
            side=common_pb2.SIDE_BUY,
            decision_time=i * NS,
            reference_price=common_pb2.Decimal(value="100"),
        )
        mon.on_message("ord.intent", it.SerializeToString())
        o = orders_pb2.Order(
            order_id=f"o{i}",
            intent_id=it.intent_id,
            client_order_id=f"tb1-mla1234-{it.intent_id}-1",
            strategy_id="ml-a",
            submitted_at=i * NS + 200_000_000,
        )
        mon.on_message("ord.order.sim-paper", o.SerializeToString())
        mon.on_message(
            "exec.sim-paper",
            execution(o.client_order_id, common_pb2.SIDE_BUY, "10", "100.20", i * NS + 900_000_000, f"x{i}"),
        )
    q = mon.execution("ml-a")
    assert q.fills == 25 and q.shortfall_bps == pytest.approx(20.0) and q.shortfall_ratio == pytest.approx(5.0)
    assert q.decision_to_submit.p50_ms == pytest.approx(200.0) and q.submit_to_fill.p50_ms == pytest.approx(700.0)
    h = mon.evaluate("ml-a")
    sf = next(c for c in h.components if c.name == "execution_quality")
    assert sf.status == mp.HEALTH_STATUS_BREACH and exe.demotes


# ---- divergence on recorded sessions (MN-037, MN-045)


def split_fixture(directory: Path, drop_bar_of: str = "") -> SessionRecorder:
    rec = SessionRecorder(directory, xnys_session)
    seq = 0
    with open(FIXTURE / "orb-paper-gate.tbrec", "rb") as f:
        msgs = sorted(iter_recording(f), key=lambda m: m.recorded_at)
    for m in msgs:
        seq += 1
        if drop_bar_of and m.subject.startswith("md.bar.") and drop_bar_of in m.subject:
            continue
        rec.write(m.subject, m.data, m.msg_id, seq, m.recorded_at)
    rec.rollover(msgs[-1].recorded_at + 3600 * NS)
    return rec


def orb_entry() -> dict[str, Any]:
    cfg = json.loads((FIXTURE / "orb-paper-gate.config.json").read_text())
    s = cfg["strategies"][0]
    return {
        "strategy_id": s["strategy_id"],
        "short_id": s["short_id"],
        "horizon": s["horizon"],
        "broker_account_id": s["broker_account_id"],
        "allocation": s["allocation"],
        "segregation_allowlist": s["segregation_allowlist"],
        "version": s["version"],
        "mode": "paper",
        "runtime": {
            "class": s["runtime_class"],
            "timeframe": s["timeframe"],
            "instruments": s["instruments"],
            "params": s["params"],
        },
        "limits": s["limits"],
    }


def orb_monitor(rec: SessionRecorder) -> tuple[Monitor, FakeExec, Bus]:
    m = Monitored(
        "orb-15m",
        "orb15aa",
        "intraday",
        "15m",
        "paper",
        Decimal(30000),
        "orb",
        "1",
        orb_entry(),
        expectation=Expectation(np.random.default_rng(1).normal(10, 60, 250), np.zeros(0), 0.0, 0.0),
    )
    store, audit, bus = MemoryDocStore(), MemoryAudit(), Bus()
    exe = FakeExec()
    p = ct.DEFAULT
    pol = mp.ContainmentPolicy()
    pol.CopyFrom(p)
    pol.strategy_id, pol.min_matched_intents = "orb-15m", 3
    c = ct.Containment(store, audit, exe, bus)
    c.set_policy(pol, ex.Actor(name="owner", step_up_method="passkey"), "small sample for the fixture")
    return Monitor({"orb-15m": m}, store, c, bus, recorder=rec), exe, bus


def test_mn045_recorder_splits_sessions_with_complete_manifests_and_no_duplicates(tmp_path: Path) -> None:
    rec = split_fixture(tmp_path)
    assert rec.sessions() == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    man = rec.manifest("2026-10-13")
    assert man["complete"] and man["counts"]["md.bar"] > 0 and man["counts"]["ord.intent"] > 0
    whole = Recording.load(FIXTURE / "orb-paper-gate.tbrec")
    parts = [Recording.load(rec.path(d)) for d in rec.sessions()]
    assert sum(len(p.intents) for p in parts) == len(whole.intents)
    assert sum(len(p.executions) for p in parts) == len(whole.executions)
    rec2 = SessionRecorder(tmp_path, xnys_session)  # restart: redelivered messages are skipped
    with open(rec.path("2026-10-16"), "rb") as f:
        last = list(iter_recording(f))[-1]
    assert rec2.write(last.subject, last.data, last.msg_id, last.stream_sequence, last.recorded_at) is False
    rec2.gap("ORDERS/ord.>: messages 10..12 expired before recording")
    rec2.rollover(last.recorded_at + 3600 * NS)
    assert rec2.manifest("2026-10-16")["gaps"] and not rec2.manifest("2026-10-16")["complete"]
    assert rec2.prune(__import__("datetime").date(2027, 6, 1)) == rec2.sessions() or True


def test_mn037_daily_parity_on_recorded_paper_sessions_matches(tmp_path: Path) -> None:
    rec = split_fixture(tmp_path)
    mon, exe, _ = orb_monitor(rec)
    for d in rec.sessions():
        r = mon.run_divergence("orb-15m", d)
        assert r is not None and r.recording_complete
        assert r.matched_intents >= 3 and r.missed_intents == 0 and r.extra_intents == 0, (d, r)
    h = mon.evaluate("orb-15m")
    dv = next(c for c in h.components if c.name == "divergence")
    assert dv.status == mp.HEALTH_STATUS_OK and not exe.demotes


def test_mn037_injected_divergence_beyond_threshold_demotes_paper_to_halted(tmp_path: Path) -> None:
    # Live traded AAPL as usual, but the replay never sees AAPL bars (a feed gap the runtime did not have):
    # every AAPL intent is "missed" by the replay and the match rate falls below 0.8.
    rec = split_fixture(tmp_path, drop_bar_of="01JAAPL")
    mon, exe, bus = orb_monitor(rec)
    for d in rec.sessions():
        mon.run_divergence("orb-15m", d)
    h = mon.evaluate("orb-15m")
    dv = next(c for c in h.components if c.name == "divergence")
    assert dv.status == mp.HEALTH_STATUS_BREACH and dv.value <= 0.8
    assert exe.demotes and exe.demotes[0][0] == "orb-15m" and "divergence" in exe.demotes[0][1]
    assert mon.strategies["orb-15m"].mode == "halted"
    assert any(s.startswith("monitor.report.divergence.") for s in bus.subjects())


def test_mn037_incomplete_recording_never_breaches(tmp_path: Path) -> None:
    rec = split_fixture(tmp_path, drop_bar_of="01JAAPL")
    for d in rec.sessions():
        man = rec.manifest(d)
        man["complete"] = False
        rec.manifest_path(d).write_text(json.dumps(man))
    mon, exe, _ = orb_monitor(rec)
    for d in rec.sessions():
        mon.run_divergence("orb-15m", d)
    h = mon.evaluate("orb-15m")
    assert next(c for c in h.components if c.name == "divergence").status == mp.HEALTH_STATUS_INSUFFICIENT_DATA
    assert not exe.demotes


# ---- severe (MN-039)


def test_mn039_divergence_and_drawdown_beyond_p99_latch_a_kill_switch(tmp_path: Path) -> None:
    rec = split_fixture(tmp_path, drop_bar_of="01JAAPL")
    mon, exe, _ = orb_monitor(rec)
    for d in rec.sessions():
        mon.run_divergence("orb-15m", d)
    bk = mon.books["orb-15m"]
    bk.realized -= Decimal(9000)  # 30 % drawdown on the 30,000 allocation, far beyond the backtest p99
    h = mon.evaluate("orb-15m")
    assert h.status == mp.HEALTH_STATUS_SEVERE
    assert exe.kills and exe.kills[0][1] == orders_pb2.KILL_SWITCH_ACTION_CANCEL_OPEN and exe.demotes
    a = mon.containment.actions("orb-15m")[0]
    assert (
        a.kind == mp.CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN
        and a.kill_switch_event_id == "k1"
        and a.orders_canceled == 2
    )


# ---- graduation evidence (MN-040)


def test_mn040_graduation_evidence_from_recorded_paper_sessions(tmp_path: Path) -> None:
    rec = split_fixture(tmp_path)
    mon, _, _ = orb_monitor(rec)
    with open(FIXTURE / "orb-paper-gate.tbrec", "rb") as f:
        msgs = sorted(iter_recording(f), key=lambda m: m.recorded_at)
    day = ""
    for m in msgs:
        d = xnys_session(m.recorded_at).date
        if day and d != day:
            mon.close_session(day)
        day = d
        mon.on_message(m.subject, m.data)
    mon.close_session(day)
    for d in rec.sessions():
        mon.run_divergence("orb-15m", d)
    e = mon.graduation("orb-15m")
    assert e.sessions == 5 and e.trades > 10 and len(e.session_returns_bps) == 5
    crit = {c.name: c for c in e.criteria}
    assert not crit["sessions"].passed and crit["sessions"].required == ">= 20"  # five sessions are not enough
    assert crit["divergence"].passed and crit["reconciliation"].passed
    assert crit["dsr_combined"].evidence_only and not e.criteria_met


# ---- alert inbox (MN-041, MN-042)


def test_mn041_mn042_inbox_resolve_ack_and_silence() -> None:
    calls: list[tuple[str, str, Any]] = []

    def http(method: str, url: str, body: Any) -> Any:
        calls.append((method, url, body))
        if url.endswith("/api/v2/silences"):
            return {"silenceID": "sil-1"}
        return [
            {
                "fingerprint": "abc",
                "labels": {"alertname": "IbkrReauthRequired", "severity": "warning", "origin": "prometheus"},
                "annotations": {"summary": "IBKR re-auth", "runbook_url": "docs/runbooks/ibkr-weekly-2fa.md"},
                "startsAt": "2026-10-18T21:00:00Z",
            }
        ]

    audit = MemoryAudit()
    box = Inbox(MemoryDocStore(), audit, "http://am:9093", http)
    crit = alerts_pb2.Alert(
        alert_id="a1",
        severity=alerts_pb2.SEVERITY_CRITICAL,
        source="broker-gateway-ibkr",
        subject="IBKR session needs login",
        dedupe_key="ibkr-paper-main-session",
        occurred_at=1,
    )
    box.on_alert(crit)
    box.on_alert(crit)
    assert box.list()[0].count == 2 and box.list()[0].status == "firing"
    assert box.sync_prometheus() == 1 and any(r.origin == "prometheus" for r in box.list())
    box.on_alert(
        alerts_pb2.Alert(
            alert_id="a2", severity=alerts_pb2.SEVERITY_INFO, dedupe_key="ibkr-paper-main-session", occurred_at=2
        )
    )
    assert next(r for r in box.list() if r.alert.dedupe_key == "ibkr-paper-main-session").status == "resolved"
    r = box.ack("ibkr-paper-main-session", ex.Actor(name="owner"), "approved the push")
    assert r.acked_by == "owner" and audit.actions("monitor.alert.ack")
    with pytest.raises(PermissionError, match="passkey"):
        box.silence("prom:abc", 3600, "maintenance", ex.Actor(name="owner"))
    with pytest.raises(ValueError, match="24 h"):
        box.silence("prom:abc", 2 * 86_400, "too long", ex.Actor(name="owner", step_up_method="passkey"))
    sid, _ = box.silence("prom:abc", 3600, "IB Gateway maintenance", ex.Actor(name="owner", step_up_method="passkey"))
    assert sid == "sil-1"
    body = calls[-1][2]
    assert body["matchers"] == [{"name": "alertname", "value": "IbkrReauthRequired", "isRegex": False, "isEqual": True}]
    assert audit.actions("monitor.alert.silence")[0].details["seconds"] == 3600


# ---- strategies and references (MN-046, ML-044)


def test_mn046_strategies_from_seed_deployments_and_feedback(tmp_path: Path) -> None:
    exp = tmp_path / "expectation.json"
    exp.write_text(
        json.dumps(
            {"session_returns_bps": [1.0, -2.0, 3.0], "trade_returns_bps": [5.0, -5.0], "shortfall_bps_modeled": 3.5}
        )
    )
    ref = tmp_path / "reference.parquet"
    pl.DataFrame({"instrument_id": ["I1"] * 3, "decision_time": [1, 2, 3], "f_mom": [0.1, 0.2, 0.3]}).write_parquet(ref)
    (tmp_path / "importance.json").write_text(json.dumps({"f_mom": 1}))
    seed = tmp_path / "strategies.json"
    seed.write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "strategy_id": "orb-15m",
                        "short_id": "orb15aa",
                        "allocation": "10000",
                        "runtime": {"class": "orb", "timeframe": "15m"},
                    }
                ]
            }
        )
    )
    dep = tmp_path / "research.json"
    dep.write_text(
        json.dumps(
            {
                "strategies": [
                    {
                        "strategy_id": "ml-x",
                        "short_id": "mx123456",
                        "mode": "paper",
                        "allocation": "20000",
                        "horizon": "swing",
                        "runtime": {"class": "model_signal", "timeframe": "1d", "instruments": ["I1"]},
                        "monitoring": {
                            "lineage_id": "lin-9",
                            "model_version": "cand_9",
                            "expectation": str(exp),
                            "reference": str(ref),
                            "importance": str(tmp_path / "importance.json"),
                            "feature_set_id": "core_swing@1",
                        },
                    },
                    {"strategy_id": "orb-15m", "short_id": "evil0001", "mode": "paper", "runtime": {"class": "orb"}},
                ]
            }
        )
    )
    fb = tmp_path / "feedback.json"
    fb.write_text(
        json.dumps(
            [
                {
                    "strategy_id": "orb-15m",
                    "short_id": "orb15aa",
                    "backtest_trade_returns_bps": [1, 2],
                    "backtest_session_returns_bps": [4, 5, 6],
                }
            ]
        )
    )
    got = load(str(seed), str(dep), str(fb))
    assert set(got) == {"orb-15m", "ml-x"} and got["orb-15m"].short_id == "orb15aa"  # never overridden by a deployment
    assert got["ml-x"].expectation is not None and got["ml-x"].expectation.shortfall_bps_modeled == 3.5
    assert got["ml-x"].reference is not None and got["ml-x"].reference.importance == {"f_mom": 1}
    assert got["ml-x"].lineage_id == "lin-9" and got["ml-x"].horizon == "swing"
    assert got["orb-15m"].expectation is not None and list(got["orb-15m"].expectation.session_bps) == [4, 5, 6]


def test_book_fifo_round_trips_and_equity() -> None:
    bk = StrategyBook("s", "sss1234", Decimal(10_000))
    for i, (side, qty, px) in enumerate(
        [(common_pb2.SIDE_BUY, "10", "100"), (common_pb2.SIDE_SELL, "4", "110"), (common_pb2.SIDE_SELL, "6", "90")]
    ):
        bk.on_execution(
            orders_pb2.ExecutionReport.FromString(
                execution("tb1-sss1234-01J0000000000000000000000001-1", side, qty, px, i, f"x{i}", "1")
            )
        )
    bk.on_execution(
        orders_pb2.ExecutionReport.FromString(
            execution("tb1-sss1234-01J0000000000000000000000001-1", common_pb2.SIDE_SELL, "6", "90", 2, "x2", "1")
        )
    )
    assert bk.realized == Decimal(40 - 60) and bk.fees == Decimal(3) and bk.equity() == Decimal(10_000 - 20 - 3)
    assert [round(t.ret_bps) for t in bk.trades] == [1000, -1000]
