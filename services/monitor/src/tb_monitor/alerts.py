"""Bus alerts → Alertmanager (section 13 ops slice, D22): every ``alert.<severity>`` message from
any service becomes an Alertmanager alert, so routing, grouping, deduplication, quiet hours, and
Pushover escalation (critical repeats until acknowledged) live in one place. Payloads never carry
secrets (SE-012; producers redact)."""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from tb.alerts.v1 import alerts_pb2

SEVERITIES = {
    alerts_pb2.SEVERITY_INFO: "info",
    alerts_pb2.SEVERITY_WARNING: "warning",
    alerts_pb2.SEVERITY_CRITICAL: "critical",
}


def _name(subject: str) -> str:
    words = [w for w in "".join(c if c.isalnum() else " " for c in subject).split() if w]
    return "Bus" + "".join(w[:1].upper() + w[1:] for w in words)[:80] if words else "BusAlert"


def to_alertmanager(
    a: alerts_pb2.Alert, now: datetime | None = None, ttl: timedelta = timedelta(hours=1)
) -> dict[str, Any]:
    """Convert one bus alert. The dedupe key becomes a label so repeats group into one notification;
    the alert resolves itself after ``ttl`` unless re-sent."""
    start = datetime.fromtimestamp(a.occurred_at / 1e9, tz=UTC) if a.occurred_at else (now or datetime.now(tz=UTC))
    labels = {
        "alertname": _name(a.subject),
        "severity": SEVERITIES.get(a.severity, "warning"),
        "source": a.source or "unknown",
        "dedupe_key": a.dedupe_key or a.alert_id,
        "origin": "bus",
    }
    annotations = {
        "summary": f"{a.subject}: {a.message}"[:1000],
        "runbook_url": a.runbook_url or "docs/runbooks/service-down.md",
    }
    if a.correlation_id:
        annotations["correlation_id"] = a.correlation_id
    return {
        "labels": labels,
        "annotations": annotations,
        "startsAt": start.isoformat(),
        "endsAt": (start + ttl).isoformat(),
    }


def report_alert(name: str, summary: str, now: datetime) -> dict[str, Any]:
    """A daily report as an Alertmanager alert routed to the report receiver."""
    return {
        "labels": {"alertname": name, "severity": "info", "kind": "report", "source": "monitor", "origin": "monitor"},
        "annotations": {"summary": summary[:3000], "runbook_url": "docs/runbooks/kill-switch.md"},
        "startsAt": now.isoformat(),
        "endsAt": (now + timedelta(hours=12)).isoformat(),
    }


def post(url: str, alerts: list[dict[str, Any]], timeout: float = 5.0) -> None:
    """POST alerts to Alertmanager's v2 API; raises on failure (the caller retries via the bus)."""
    req = urllib.request.Request(  # noqa: S310 - internal Alertmanager URL from config
        url.rstrip("/") + "/api/v2/alerts",
        data=json.dumps(alerts).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - internal Alertmanager URL from config
        if resp.status >= 300:
            raise RuntimeError(f"alertmanager returned {resp.status}")
