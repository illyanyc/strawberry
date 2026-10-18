"""Containment policies and automatic actions (MN-034..MN-036, MN-039, RM-018; ADR-0045).

A breach of a strategy's policy demotes it through ``ExecutionService.DemoteStrategy`` (live → paper,
shadow/paper → halted, enforced as reduce-only by execution); a severe breach (divergence breach together
with drawdown beyond the backtest's p99) also latches a strategy kill switch (``cancel_open`` or, if the
policy says so, ``flatten_strategy_owned``, which never touches manual holdings). Every action is recorded
before it is sent, audited, alerted as critical with the evidence, published on
``monitor.containment.<strategy>``, idempotent per (strategy, kind, evidence), and retried with a page when
execution cannot be reached. Nothing here promotes, unhalts, or releases.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from google.protobuf.message import Message

from tb.alerts.v1 import alerts_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tb.orders.v1 import orders_pb2
from tb_monitor import analytics as an
from tbcore.audit import AuditSink
from tbcore.docstore import DocStore

log = logging.getLogger("monitor.containment")
K_POLICY, K_ACTION = "policy", "action"
RUNBOOK = "docs/runbooks/containment.md"
Publish = Callable[[str, Message, str], Any]

# Safe defaults (MN-034); the documented table in monitoring.md must match.
DEFAULT = mp.ContainmentPolicy(
    strategy_id="",
    enabled=True,
    drawdown_warning_multiple=1.0,
    drawdown_breach_multiple=1.5,
    match_rate_warning=0.9,
    match_rate_breach=0.8,
    slippage_gap_warning_bps=5.0,
    slippage_gap_breach_bps=15.0,
    feature_psi_warning=0.25,
    feature_psi_breach=0.5,
    feature_psi_top_k=5,
    prediction_psi_warning=0.25,
    prediction_psi_breach=0.5,
    shortfall_warning_multiple=1.5,
    shortfall_breach_multiple=2.0,
    shortfall_min_excess_bps=5.0,
    breach_action=mp.CONTAINMENT_ACTION_KIND_DEMOTE,
    severe_action=mp.CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN,
    min_trades=30,
    min_fills=20,
    min_matched_intents=10,
    min_drift_samples=200,
)
STRENGTH = {
    mp.CONTAINMENT_ACTION_KIND_UNSPECIFIED: 0,
    mp.CONTAINMENT_ACTION_KIND_ALERT: 1,
    mp.CONTAINMENT_ACTION_KIND_DEMOTE: 2,
    mp.CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN: 3,
    mp.CONTAINMENT_ACTION_KIND_KILL_FLATTEN: 4,
}
# Threshold fields where a larger value is more permissive, and where a smaller one is.
LOOSER_IF_HIGHER = (
    "drawdown_warning_multiple",
    "drawdown_breach_multiple",
    "slippage_gap_warning_bps",
    "slippage_gap_breach_bps",
    "feature_psi_warning",
    "feature_psi_breach",
    "prediction_psi_warning",
    "prediction_psi_breach",
    "shortfall_warning_multiple",
    "shortfall_breach_multiple",
    "shortfall_min_excess_bps",
    "min_trades",
    "min_fills",
    "min_matched_intents",
    "min_drift_samples",
)
LOOSER_IF_LOWER = ("match_rate_warning", "match_rate_breach", "feature_psi_top_k")


def loosened(prev: mp.ContainmentPolicy, nxt: mp.ContainmentPolicy) -> list[str]:
    """Fields the change makes more permissive (MN-036): any of them needs a passkey step-up."""
    out = [f for f in LOOSER_IF_HIGHER if getattr(nxt, f) > getattr(prev, f)]
    out += [f for f in LOOSER_IF_LOWER if getattr(nxt, f) < getattr(prev, f)]
    if prev.enabled and not nxt.enabled:
        out.append("enabled")
    for f in ("breach_action", "severe_action"):
        if STRENGTH[getattr(nxt, f)] < STRENGTH[getattr(prev, f)]:
            out.append(f)
    return out


def validate(p: mp.ContainmentPolicy) -> None:
    if p.drawdown_breach_multiple < p.drawdown_warning_multiple or p.drawdown_warning_multiple <= 0:
        raise ValueError("drawdown multiples: 0 < warning <= breach")
    if not 0 < p.match_rate_breach <= p.match_rate_warning <= 1:
        raise ValueError("match rates: 0 < breach <= warning <= 1")
    for w, b in (
        ("slippage_gap_warning_bps", "slippage_gap_breach_bps"),
        ("feature_psi_warning", "feature_psi_breach"),
        ("prediction_psi_warning", "prediction_psi_breach"),
        ("shortfall_warning_multiple", "shortfall_breach_multiple"),
    ):
        if not 0 < getattr(p, w) <= getattr(p, b):
            raise ValueError(f"{w} must be positive and at most {b}")
    if (
        p.breach_action == mp.CONTAINMENT_ACTION_KIND_UNSPECIFIED
        or p.severe_action == mp.CONTAINMENT_ACTION_KIND_UNSPECIFIED
    ):
        raise ValueError("breach_action and severe_action are required")
    if STRENGTH[p.severe_action] < STRENGTH[p.breach_action]:
        raise ValueError("severe_action cannot be weaker than breach_action")


class Executor(Protocol):
    def demote(self, strategy_id: str, reason: str, evidence: str) -> ex.DemoteStrategyResponse: ...

    def kill(self, strategy_id: str, action: int, reason: str) -> Any: ...


class GrpcExecutor:
    """ExecutionService client with the monitor's service token (execution trusts ``monitor`` only for
    reads, kill-switch engage, and the reduce-only DemoteStrategy, RM-018)."""

    def __init__(self, client: Any) -> None:
        self.c = client

    def demote(self, strategy_id: str, reason: str, evidence: str) -> ex.DemoteStrategyResponse:
        r: ex.DemoteStrategyResponse = self.c.call(
            "DemoteStrategy",
            ex.DemoteStrategyRequest(
                strategy_id=strategy_id, reason=reason, evidence_id=evidence, correlation_id=evidence
            ),
            timeout=8.0,
        )
        return r

    def kill(self, strategy_id: str, action: int, reason: str) -> Any:
        return self.c.call(
            "TriggerKillSwitch",
            ex.TriggerKillSwitchRequest(
                scope=orders_pb2.KILL_SWITCH_SCOPE_STRATEGY,
                target=strategy_id,
                action=orders_pb2.KillSwitchAction.ValueType(action),
                reason=reason,
            ),
            timeout=8.0,
        )


@dataclass
class Decision:
    kind: int
    reason: str
    evidence: list[mp.HealthComponent]
    evidence_id: str


def decide(health: mp.StrategyHealth, policy: mp.ContainmentPolicy, dd_p99_exceeded: bool) -> Decision | None:
    """Breach → the policy's breach action; divergence breach + drawdown beyond the backtest p99 → severe
    (MN-039). Insufficient-data components never act; a disabled policy only alerts."""
    breached = [c for c in health.components if c.status in (mp.HEALTH_STATUS_BREACH, mp.HEALTH_STATUS_SEVERE)]
    if not breached:
        return None
    div = any(c.name == "divergence" for c in breached)
    severe = div and dd_p99_exceeded
    kind = policy.severe_action if severe else policy.breach_action
    if not policy.enabled:
        kind = mp.CONTAINMENT_ACTION_KIND_ALERT
    names = ", ".join(f"{c.name} {c.value:.4g} vs {c.breach_threshold:.4g}" for c in breached)
    reason = ("severe: " if severe else "breach: ") + names
    eid = "+".join(sorted({c.evidence_id for c in breached if c.evidence_id})) or health.strategy_id
    return Decision(int(kind), reason[:400], breached, eid)


def action_id(strategy_id: str, kind: int, evidence: str) -> str:
    return "ca-" + hashlib.sha256(f"{strategy_id}|{kind}|{evidence}".encode()).hexdigest()[:20]


class Containment:
    def __init__(self, store: DocStore, audit: AuditSink, executor: Executor | None, publish: Publish) -> None:
        self.store, self.audit, self.executor, self.publish = store, audit, executor, publish

    # ---- policies

    def policy(self, strategy_id: str) -> tuple[mp.ContainmentPolicy, bool]:
        own = self.store.get(K_POLICY, strategy_id, mp.ContainmentPolicy) if strategy_id else None
        if own is not None:
            return own, False
        default = self.store.get(K_POLICY, "", mp.ContainmentPolicy) or DEFAULT
        out = mp.ContainmentPolicy()
        out.CopyFrom(default)
        out.strategy_id = strategy_id
        return out, bool(strategy_id)

    def set_policy(self, p: mp.ContainmentPolicy, actor: ex.Actor, reason: str) -> tuple[mp.ContainmentPolicy, bool]:
        validate(p)
        prev, _ = self.policy(p.strategy_id)
        loose = loosened(prev, p)
        if loose and actor.step_up_method != "passkey":
            raise PermissionError(f"loosening {', '.join(loose)} needs a passkey step-up (MN-036)")
        n = mp.ContainmentPolicy()
        n.CopyFrom(p)
        n.version, n.updated_at, n.updated_by = prev.version + 1, time.time_ns(), actor.name or "owner"
        self.store.put(K_POLICY, p.strategy_id, n, p.strategy_id)
        self.audit.append(
            actor.name or "owner",
            "monitor.containment.policy.set",
            p.strategy_id or "default",
            {"loosened": loose, "step_up": actor.step_up_method, "reason": reason[:300], "version": n.version},
        )
        return n, bool(loose)

    def actions(self, strategy_id: str = "", limit: int = 100) -> list[mp.ContainmentAction]:
        rows = self.store.list(K_ACTION, mp.ContainmentAction, lineage_id=strategy_id or None)
        return sorted(rows, key=lambda a: -a.decided_at)[:limit]

    # ---- acting

    def act(self, health: mp.StrategyHealth, d: Decision, mode: str) -> mp.ContainmentAction | None:
        """Record, audit, and execute one decision (idempotent per strategy, kind, and evidence)."""
        aid = action_id(health.strategy_id, d.kind, d.evidence_id)
        prev = self.store.get(K_ACTION, aid, mp.ContainmentAction)
        if prev is not None and prev.executed:
            return None
        if d.kind != mp.CONTAINMENT_ACTION_KIND_ALERT and prev is None:
            if mode in ("halted", "disabled"):
                return None  # nothing left to reduce
            last = next(
                (
                    x
                    for x in self.actions(health.strategy_id)
                    if x.executed and x.kind != mp.CONTAINMENT_ACTION_KIND_ALERT
                ),
                None,
            )
            if last is not None and last.to_mode == mode:
                return None  # already contained; only the owner moves it again (RM-015)
        now = time.time_ns()
        a = prev or mp.ContainmentAction(
            action_id=aid,
            strategy_id=health.strategy_id,
            kind=d.kind,  # type: ignore[arg-type]
            from_mode=mode,
            reason=d.reason,
            evidence=d.evidence,
            evidence_report_id=d.evidence_id,
            decided_at=now,
            correlation_id=aid,
        )
        self.store.put(K_ACTION, aid, a, health.strategy_id)
        try:
            self._execute(a)
            a.executed, a.executed_at, a.error = True, time.time_ns(), ""
        except Exception as exc:  # execution down or refusing: retry next cycle and page now
            a.error = str(exc)[:300]
            log.error("containment action failed", extra={"strategy": a.strategy_id, "err": a.error})
        self.store.put(K_ACTION, aid, a, health.strategy_id)
        self.audit.append(
            "monitor",
            f"monitor.containment.{mp.ContainmentActionKind.Name(a.kind).removeprefix('CONTAINMENT_ACTION_KIND_').lower()}",
            a.strategy_id,
            {
                "reason": a.reason,
                "evidence_id": a.evidence_report_id,
                "from": a.from_mode,
                "to": a.to_mode,
                "executed": a.executed,
                "error": a.error,
                "kill_switch_event_id": a.kill_switch_event_id,
            },
            correlation_id=aid,
        )
        self.publish(f"monitor.containment.{a.strategy_id}", a, f"{aid}:{int(a.executed)}")
        self.publish("alert.critical", self._alert(a), f"alert:{aid}:{int(a.executed)}")
        return a

    def _execute(self, a: mp.ContainmentAction) -> None:
        if a.kind == mp.CONTAINMENT_ACTION_KIND_ALERT:
            a.to_mode = a.from_mode
            return
        if self.executor is None:
            raise RuntimeError("no execution client configured")
        if a.kind in (mp.CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN, mp.CONTAINMENT_ACTION_KIND_KILL_FLATTEN):
            act = (
                orders_pb2.KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED
                if a.kind == mp.CONTAINMENT_ACTION_KIND_KILL_FLATTEN
                else orders_pb2.KILL_SWITCH_ACTION_CANCEL_OPEN
            )
            k = self.executor.kill(a.strategy_id, act, f"containment {a.action_id}: {a.reason}"[:400])
            ks = getattr(k, "kill_switch", None)
            a.kill_switch_event_id = getattr(ks, "event_id", "") or a.kill_switch_event_id
            a.orders_canceled = int(getattr(k, "orders_canceled", 0) or 0)
        r = self.executor.demote(a.strategy_id, f"containment {a.action_id}: {a.reason}"[:400], a.evidence_report_id)
        a.to_mode = ex.StrategyMode.Name(r.strategy.mode).removeprefix("STRATEGY_MODE_").lower()
        a.orders_canceled += r.orders_canceled
        a.kill_switch_event_id = a.kill_switch_event_id or r.kill_switch_event_id

    def _alert(self, a: mp.ContainmentAction) -> alerts_pb2.Alert:
        kind = mp.ContainmentActionKind.Name(a.kind).removeprefix("CONTAINMENT_ACTION_KIND_").lower()
        if a.executed:
            subject, msg = (
                f"Containment: {a.strategy_id} {kind}",
                f"{a.strategy_id} {a.from_mode} → {a.to_mode or a.from_mode} ({kind}): {a.reason} "
                f"[evidence {a.evidence_report_id}]",
            )
        else:
            subject, msg = "Containment action failed", f"{a.strategy_id} {kind} not executed: {a.error}; retrying"
        return alerts_pb2.Alert(
            alert_id=f"alert-{a.action_id}-{int(a.executed)}",
            severity=alerts_pb2.SEVERITY_CRITICAL,
            source="monitor",
            subject=subject,
            message=msg[:500],
            dedupe_key=f"containment-{a.strategy_id}",
            runbook_url=RUNBOOK,
            occurred_at=time.time_ns(),
            correlation_id=a.action_id,
        )

    def retry_pending(self) -> list[mp.ContainmentAction]:
        out = []
        for a in self.store.list(K_ACTION, mp.ContainmentAction):
            if not a.executed and a.kind != mp.CONTAINMENT_ACTION_KIND_ALERT:
                h = mp.StrategyHealth(strategy_id=a.strategy_id)
                d = Decision(a.kind, a.reason, list(a.evidence), a.evidence_report_id)
                r = self.act(h, d, a.from_mode)
                if r is not None:
                    out.append(r)
        return out


def drawdown_component(
    dd_pct: float, p95: float, policy: mp.ContainmentPolicy, sessions: int, evidence: str
) -> mp.HealthComponent:
    """MN-032: drawdown since deployment as a multiple of the backtest's horizon-matched p95."""
    mult = dd_pct / p95 if p95 > 0 else float("nan")
    return an.component(
        "drawdown",
        mult,
        policy.drawdown_warning_multiple,
        policy.drawdown_breach_multiple,
        sessions if p95 > 0 else 0,
        1,
        f"drawdown {dd_pct:.2f}% vs backtest p95 {p95:.2f}% over {sessions} sessions",
        evidence,
    )
