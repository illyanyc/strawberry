"""Runs the analyst feedback slice inside the monitor service (MN-020..MN-026).

Keeps each configured strategy's recent executions (from ``exec.>``), builds a daily (and on-demand)
``LiveFeedbackReport`` over a rolling window of sessions, and publishes:

* ``monitor.feedback.<strategy>``: the report (the analyst stores the latest per strategy);
* ``monitor.retrain_suggested.<lineage>`` (+ ``monitor.decay/drift/divergence.<lineage>``) when a
  condition fires with enough samples (MN-023, MN-024);
* on a critical condition, or a critical bus alert naming the strategy: ``analyst.autonomy.lower``
  (level 0, paused) and a critical alert (MN-025). Containment (demotion, kill switches) stays with
  section 07 and the full section 13; feedback never replaces it.

Strategy configuration (``TB_MONITOR_FEEDBACK_CONFIG``, JSON list): ``strategy_id``, ``short_id``
(attribution prefix), ``lineage_id``, ``mode``, ``model_version``, ``window_sessions`` (default 20),
and ``expectation`` (``trade_mean_bps``, ``trade_sd_bps``, ``distributions``, ``backtest_report_id``,
``regime_shares``, ``slippage_bps_modeled``) or ``backtest_trade_returns_bps`` to bootstrap one.
"""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.protobuf.message import Message

from tb.alerts.v1 import alerts_pb2
from tb.analyst.v1 import analyst_pb2
from tb.monitor.v1 import feedback_pb2
from tb.orders.v1 import orders_pb2
from tb_monitor import feedback as fb

Publish = Callable[[str, Message, str], Awaitable[None]]
MAX_EXECUTIONS = 20_000


@dataclass
class StrategyFeedback:
    strategy_id: str
    short_id: str
    lineage_id: str
    mode: str
    model_version: str
    expectation: fb.Expectation
    window_sessions: int = 20
    executions: deque[orders_pb2.ExecutionReport] = field(default_factory=lambda: deque(maxlen=MAX_EXECUTIONS))
    latest: feedback_pb2.LiveFeedbackReport | None = None


def load_config(path: str) -> list[StrategyFeedback]:
    if not path or not Path(path).exists():
        return []
    out = []
    for e in json.loads(Path(path).read_text(encoding="utf-8")):
        if "expectation" in e:
            x = e["expectation"]
            exp = fb.Expectation(
                trade_mean_bps=float(x["trade_mean_bps"]),
                trade_sd_bps=float(x["trade_sd_bps"]),
                distributions={k: [float(v) for v in vs] for k, vs in x.get("distributions", {}).items()},
                backtest_report_id=str(x.get("backtest_report_id", "")),
                training_run_id=str(x.get("training_run_id", "")),
                slippage_bps_modeled=float(x.get("slippage_bps_modeled", 0.0)),
                regime_shares={k: float(v) for k, v in x.get("regime_shares", {}).items()},
            )
        else:
            exp = fb.expectation_from(
                [float(v) for v in e.get("backtest_trade_returns_bps", [])],
                report_id=str(e.get("backtest_report_id", "")),
            )
        out.append(
            StrategyFeedback(
                strategy_id=e["strategy_id"],
                short_id=e["short_id"],
                lineage_id=e.get("lineage_id") or e["strategy_id"],
                mode=e.get("mode", "paper"),
                model_version=e.get("model_version", ""),
                expectation=exp,
                window_sessions=int(e.get("window_sessions", 20)),
            )
        )
    return out


class FeedbackRunner:
    def __init__(self, strategies: list[StrategyFeedback], cfg: fb.FeedbackConfig, publish: Publish) -> None:
        self.strategies = {s.strategy_id: s for s in strategies}
        self.by_short = {s.short_id: s for s in strategies}
        self.cfg = cfg
        self.publish = publish
        self._lowered: set[str] = set()

    def on_execution(self, x: orders_pb2.ExecutionReport) -> None:
        s = self.by_short.get(fb.strategy_short_id(x.client_order_id))
        if s is not None:
            s.executions.append(x)

    def window(self, s: StrategyFeedback) -> fb.LiveWindow:
        trips = fb.round_trips(list(s.executions), s.short_id)
        days = sorted({datetime.fromtimestamp(t / 1e9, tz=UTC).date() for t, _ in trips})[-s.window_sessions :]
        keep = set(days)
        trips = [(t, r) for t, r in trips if datetime.fromtimestamp(t / 1e9, tz=UTC).date() in keep]
        return fb.LiveWindow(
            strategy_id=s.strategy_id,
            lineage_id=s.lineage_id,
            model_version=s.model_version,
            mode=s.mode,
            window_start=trips[0][0] if trips else 0,
            window_end=trips[-1][0] if trips else 0,
            trade_returns_bps=[r for _, r in trips],
            session_returns_bps=fb.session_returns(trips),
            sessions=len(days),
        )

    async def run(self, now: datetime | None = None) -> list[feedback_pb2.LiveFeedbackReport]:
        now = now or datetime.now(tz=UTC)
        reports = []
        for s in self.strategies.values():
            rep = fb.build_report(self.window(s), s.expectation, self.cfg, now)
            s.latest = rep
            reports.append(rep)
            await self.publish(f"monitor.feedback.{s.strategy_id}", rep, rep.report_id)
            ev = fb.retrain_suggested(rep, now)
            if ev is None:
                continue
            for subject in fb.subjects_for(ev):
                await self.publish(subject, ev, f"{ev.trigger_id}:{subject.split('.')[1]}")
            if ev.severity == "critical":
                await self.lower(
                    s, f"critical live feedback: {', '.join(c.condition for c in ev.conditions)}", rep.report_id, now
                )
        return reports

    async def on_alert(self, a: alerts_pb2.Alert, now: datetime | None = None) -> None:
        """MN-025: a critical alarm that names a configured strategy lowers its lineage's autonomy."""
        if a.severity != alerts_pb2.SEVERITY_CRITICAL:
            return
        text = f"{a.subject} {a.dedupe_key} {a.message}"
        for s in self.strategies.values():
            if s.strategy_id in text:
                await self.lower(s, f"critical alert: {a.subject}"[:200], a.alert_id, now or datetime.now(tz=UTC))

    async def lower(self, s: StrategyFeedback, reason: str, evidence: str, now: datetime) -> None:
        key = f"{s.lineage_id}:{evidence}"
        if key in self._lowered:
            return
        self._lowered.add(key)
        ts = int(now.timestamp() * 1e9)
        await self.publish(
            "analyst.autonomy.lower",
            analyst_pb2.LowerAutonomy(
                lineage_id=s.lineage_id,
                level=0,
                pause=True,
                actor="monitor",
                reason=reason,
                evidence_report_id=evidence,
                occurred_at=ts,
                correlation_id=evidence,
            ),
            f"lower:{key}",
        )
        await self.publish(
            "alert.critical",
            alerts_pb2.Alert(
                alert_id=f"analyst-lower-{evidence}",
                severity=alerts_pb2.SEVERITY_CRITICAL,
                source="monitor",
                subject="Analyst autonomy lowered",
                message=f"lineage {s.lineage_id}: {reason}; analyst loop paused",
                dedupe_key=f"analyst-lower-{s.lineage_id}",
                runbook_url="docs/runbooks/analyst.md",
                occurred_at=ts,
                correlation_id=evidence,
            ),
            f"alert:{key}",
        )

    def snapshot(self) -> dict[str, Any]:
        return {sid: (str(s.latest) if s.latest else "") for sid, s in self.strategies.items()}


def now_ns() -> int:
    return time.time_ns()
