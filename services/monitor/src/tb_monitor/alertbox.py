"""Alert inbox, acknowledgements, and Alertmanager silences (MN-041, MN-042; ADR-0045).

Alertmanager stays the only router and Pushover sender (D45). The inbox keeps one record per dedupe key
from bus alerts (an ``info`` alert carrying the dedupe key of a firing record resolves it, as the IBKR
adapter does when the session is ready again) and from Alertmanager's active alerts (Prometheus rules).
Acknowledging records who acked (audited, no step-up). Silencing creates an Alertmanager silence for at
most 24 hours and needs a passkey step-up (audited).
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from tb.alerts.v1 import alerts_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb.orders.v1 import execution_pb2 as ex
from tb_monitor.alerts import SEVERITIES
from tbcore.audit import AuditSink
from tbcore.docstore import DocStore

K_ALERT = "alert"
MAX_SILENCE = 24 * 3600
SEV = {
    "info": alerts_pb2.SEVERITY_INFO,
    "warning": alerts_pb2.SEVERITY_WARNING,
    "critical": alerts_pb2.SEVERITY_CRITICAL,
}
HttpJSON = Callable[[str, str, dict[str, Any] | None], Any]


def http_json(method: str, url: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(  # noqa: S310 - internal Alertmanager URL from config
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:  # noqa: S310 - internal Alertmanager URL from config
        raw = resp.read()
    return json.loads(raw) if raw else None


def _ns(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1e9)
    except ValueError:
        return 0


class Inbox:
    def __init__(self, store: DocStore, audit: AuditSink, am_url: str = "", http: HttpJSON = http_json) -> None:
        self.store, self.audit, self.am, self.http = store, audit, am_url.rstrip("/"), http

    def _get(self, key: str) -> mp.AlertRecord | None:
        return self.store.get(K_ALERT, key, mp.AlertRecord)

    def _put(self, r: mp.AlertRecord, key: str) -> None:
        self.store.put(K_ALERT, key, r, r.alert.source)

    def on_alert(self, a: alerts_pb2.Alert) -> mp.AlertRecord:
        key = a.dedupe_key or a.alert_id
        now = a.occurred_at or time.time_ns()
        r = self._get(key)
        if (
            a.severity == alerts_pb2.SEVERITY_INFO
            and r is not None
            and r.status == "firing"
            and r.alert.severity > a.severity
        ):
            r.status, r.last_seen = "resolved", now
            self._put(r, key)
            return r
        if r is None:
            r = mp.AlertRecord(first_seen=now, origin="bus")
        elif r.status == "resolved":
            r.first_seen, r.count, r.acked_by, r.acked_at = now, 0, "", 0
        r.alert.CopyFrom(a)
        r.status, r.last_seen, r.count = "firing", now, r.count + 1
        self._put(r, key)
        return r

    def sync_prometheus(self) -> int:
        """Merge Alertmanager's active alerts that did not come from the bus (Prometheus rules)."""
        if not self.am:
            return 0
        active = self.http("GET", f"{self.am}/api/v2/alerts?active=true&silenced=true&inhibited=true", None) or []
        seen: set[str] = set()
        for x in active:
            labels = x.get("labels", {})
            if labels.get("origin") in ("bus", "monitor"):
                continue
            key = "prom:" + x.get("fingerprint", labels.get("alertname", ""))
            seen.add(key)
            r = self._get(key) or mp.AlertRecord(first_seen=_ns(x.get("startsAt", "")), origin="prometheus")
            sev = labels.get("severity", "warning")
            r.alert.CopyFrom(
                alerts_pb2.Alert(
                    alert_id=key,
                    severity=SEV.get(sev, alerts_pb2.SEVERITY_WARNING),
                    source=labels.get("service", labels.get("job", "prometheus")),
                    subject=labels.get("alertname", ""),
                    message=x.get("annotations", {}).get("summary", ""),
                    dedupe_key=key,
                    runbook_url=x.get("annotations", {}).get("runbook_url", ""),
                    occurred_at=r.first_seen,
                )
            )
            if r.status != "firing":
                r.count += 1
            r.status, r.last_seen = "firing", time.time_ns()
            self._put(r, key)
        for r in self.store.list(K_ALERT, mp.AlertRecord):
            if r.origin == "prometheus" and r.status == "firing" and r.alert.dedupe_key not in seen:
                r.status, r.last_seen = "resolved", time.time_ns()
                self._put(r, r.alert.dedupe_key)
        return len(seen)

    def list(self, severity: str = "", status: str = "", since: int = 0, limit: int = 200) -> list[mp.AlertRecord]:
        rows = self.store.list(K_ALERT, mp.AlertRecord)
        out = [
            r
            for r in rows
            if (not severity or SEVERITIES.get(r.alert.severity) == severity)
            and (not status or r.status == status)
            and r.last_seen >= since
        ]
        out.sort(key=lambda r: (r.status != "firing", -r.alert.severity, -r.last_seen))
        return out[: limit or 200]

    def ack(self, key: str, actor: ex.Actor, note: str = "") -> mp.AlertRecord:
        r = self._get(key)
        if r is None:
            raise KeyError(key)
        r.acked_by, r.acked_at = actor.name or "owner", time.time_ns()
        self._put(r, key)
        self.audit.append(
            r.acked_by, "monitor.alert.ack", key, {"note": note[:300], "severity": SEVERITIES.get(r.alert.severity, "")}
        )
        return r

    def silence(self, key: str, seconds: int, reason: str, actor: ex.Actor) -> tuple[str, int]:
        """MN-042: an Alertmanager silence on the record's labels, at most 24 h, passkey only."""
        if actor.step_up_method != "passkey":
            raise PermissionError("silencing an alert needs a passkey step-up (MN-042)")
        if not 0 < seconds <= MAX_SILENCE:
            raise ValueError("silence duration must be between 1 s and 24 h")
        if not reason.strip():
            raise ValueError("a reason is required")
        r = self._get(key)
        if r is None:
            raise KeyError(key)
        if not self.am:
            raise RuntimeError("Alertmanager is not configured")
        matchers = (
            [{"name": "alertname", "value": r.alert.subject, "isRegex": False, "isEqual": True}]
            if r.origin == "prometheus"
            else [{"name": "dedupe_key", "value": key, "isRegex": False, "isEqual": True}]
        )
        start = datetime.now(tz=UTC)
        end = start + timedelta(seconds=seconds)
        resp = (
            self.http(
                "POST",
                f"{self.am}/api/v2/silences",
                {
                    "matchers": matchers,
                    "startsAt": start.isoformat(),
                    "endsAt": end.isoformat(),
                    "createdBy": actor.name or "owner",
                    "comment": reason[:300],
                },
            )
            or {}
        )
        sid = str(resp.get("silenceID", ""))
        ends = int(end.timestamp() * 1e9)
        r.silence_id, r.silenced_until = sid, ends
        self._put(r, key)
        self.audit.append(
            actor.name or "owner",
            "monitor.alert.silence",
            key,
            {"silence_id": sid, "seconds": seconds, "reason": reason[:300], "step_up": actor.step_up_method},
        )
        return sid, ends
