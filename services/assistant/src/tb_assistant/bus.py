"""NATS wiring for the analyst (AN-001, AN-063; MN-023, MN-025).

Consumes, each with a durable consumer on its named stream (the assistant's NATS user may not look
streams up):

* ``ml.run.completed``, ``backtest.completed``, ``optimizer.study.completed`` (stream ``RESEARCH``)
  → round triggers (continuations when they carry an analyst round ID);
* ``monitor.feedback.>`` → the latest ``LiveFeedbackReport`` per strategy (``get_live_feedback``);
* ``monitor.retrain_suggested.>`` → live-feedback rounds (stream ``MONITOR``);
* ``analyst.autonomy.lower`` → lower a lineage's level and pause (stream ``ANALYST``).

Publishes ``analyst.*`` (stream ``ANALYST``) and ``alert.<severity>`` through JetStream with a
``Nats-Msg-Id`` so redeliveries deduplicate.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from google.protobuf.message import Message

from tb.alerts.v1 import alerts_pb2
from tb.analyst.v1 import analyst_pb2
from tb.ml.v1 import training_pb2
from tb.monitor.v1 import feedback_pb2
from tb.optimizer.v1 import optimizer_pb2
from tb_assistant.loop import Trigger

log = logging.getLogger("tb_assistant.bus")
SEVERITY = {
    "info": alerts_pb2.SEVERITY_INFO,
    "warning": alerts_pb2.SEVERITY_WARNING,
    "critical": alerts_pb2.SEVERITY_CRITICAL,
}


class FeedbackCache:
    """Latest LiveFeedbackReport per strategy (the analyst's ``get_live_feedback`` source)."""

    def __init__(self) -> None:
        self.reports: dict[str, feedback_pb2.LiveFeedbackReport] = {}

    def put(self, rep: feedback_pb2.LiveFeedbackReport) -> None:
        self.reports[rep.strategy_id] = rep

    def latest_feedback(self, strategy_id: str) -> feedback_pb2.LiveFeedbackReport | None:
        return self.reports.get(strategy_id)


class JetStreamPublisher:
    def __init__(self, js: Any, source: str = "assistant") -> None:
        self._js = js
        self._source = source

    async def publish(self, subject: str, msg: Message) -> None:
        data = msg.SerializeToString(deterministic=True)
        msg_id = subject + ":" + hashlib.sha256(data).hexdigest()[:32]
        await self._js.publish(subject, data, headers={"Nats-Msg-Id": msg_id})

    async def alert(self, severity: str, subject: str, message: str, dedupe_key: str) -> None:
        a = alerts_pb2.Alert(
            alert_id=dedupe_key + ":" + str(time.time_ns()),
            severity=SEVERITY.get(severity, alerts_pb2.SEVERITY_WARNING),
            source=self._source,
            subject=subject[:200],
            message=message[:1000],
            dedupe_key=dedupe_key,
            runbook_url="docs/runbooks/analyst.md",
            occurred_at=time.time_ns(),
        )
        await self._js.publish(f"alert.{severity}", a.SerializeToString(), headers={"Nats-Msg-Id": a.alert_id})


def trigger_from_run(m: training_pb2.MLRunCompleted) -> Trigger:
    return Trigger(
        analyst_pb2.ROUND_TRIGGER_RUN_COMPLETED,
        m.lineage_id,
        f"ml.run.completed:{m.run_id}",
        run_id=m.run_id,
        job_id=m.job_id,
        report_id=m.report_id,
        analyst_round_id=m.analyst_round_id,
        failed=m.status == training_pb2.JOB_STATUS_FAILED,
        correlation_id=m.correlation_id,
    )


def trigger_from_study(m: optimizer_pb2.StudyCompleted) -> Trigger:
    return Trigger(
        analyst_pb2.ROUND_TRIGGER_STUDY_COMPLETED,
        m.lineage_id,
        f"optimizer.study.completed:{m.study_id}:{m.status}",
        study_id=m.study_id,
        candidate_id=m.candidate_id,
        analyst_round_id=m.analyst_round_id,
        failed=m.status == optimizer_pb2.STUDY_STATUS_FAILED,
        correlation_id=m.correlation_id,
    )


def trigger_from_backtest(m: training_pb2.BacktestCompleted, lineage_id: str) -> Trigger:
    return Trigger(
        analyst_pb2.ROUND_TRIGGER_BACKTEST_COMPLETED,
        lineage_id,
        f"backtest.completed:{m.backtest_id}",
        run_id=m.run_id,
        candidate_id=m.candidate_id,
        correlation_id=m.correlation_id,
    )


def trigger_from_feedback(m: feedback_pb2.RetrainSuggested) -> Trigger:
    return Trigger(
        analyst_pb2.ROUND_TRIGGER_LIVE_FEEDBACK,
        m.lineage_id or m.strategy_id,
        m.trigger_id,
        strategy_id=m.strategy_id,
        report_id=m.report_id,
        correlation_id=m.correlation_id,
    )


async def subscribe_all(
    js: Any,
    *,
    on_trigger: Callable[[Trigger], Awaitable[Any]],
    on_backtest: Callable[[training_pb2.BacktestCompleted], Awaitable[Any]],
    feedback: FeedbackCache,
    on_lower: Callable[[analyst_pb2.LowerAutonomy], Awaitable[Any]],
) -> None:
    """Durable, explicitly acknowledged consumers; a handler error naks for redelivery."""

    def handler(parse: Callable[[bytes], Any], fn: Callable[[Any], Awaitable[Any]]) -> Callable[[Any], Awaitable[None]]:
        async def cb(msg: Any) -> None:
            try:
                await fn(parse(msg.data))
            except Exception as exc:
                log.warning(
                    "analyst event handling failed; will retry", extra={"subject": msg.subject, "error": str(exc)[:300]}
                )
                await msg.nak(delay=30)
                return
            await msg.ack()

        return cb

    async def fb_put(rep: feedback_pb2.LiveFeedbackReport) -> None:
        feedback.put(rep)

    subs: list[tuple[str, str, str, Callable[[bytes], Any], Callable[[Any], Awaitable[Any]]]] = [
        (
            "ml.run.completed",
            "RESEARCH",
            "analyst-ml-runs",
            training_pb2.MLRunCompleted.FromString,
            lambda m: on_trigger(trigger_from_run(m)),
        ),
        (
            "optimizer.study.completed",
            "RESEARCH",
            "analyst-studies",
            optimizer_pb2.StudyCompleted.FromString,
            lambda m: on_trigger(trigger_from_study(m)),
        ),
        ("backtest.completed", "RESEARCH", "analyst-backtests", training_pb2.BacktestCompleted.FromString, on_backtest),
        ("monitor.feedback.>", "MONITOR", "analyst-feedback", feedback_pb2.LiveFeedbackReport.FromString, fb_put),
        (
            "monitor.retrain_suggested.>",
            "MONITOR",
            "analyst-retrain",
            feedback_pb2.RetrainSuggested.FromString,
            lambda m: on_trigger(trigger_from_feedback(m)),
        ),
        ("analyst.autonomy.lower", "ANALYST", "analyst-lower", analyst_pb2.LowerAutonomy.FromString, on_lower),
    ]
    for subject, stream, durable, parse, fn in subs:
        await js.subscribe(subject, stream=stream, durable=durable, cb=handler(parse, fn), manual_ack=True)
