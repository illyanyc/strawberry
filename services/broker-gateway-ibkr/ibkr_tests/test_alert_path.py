"""IBKR re-auth alerts end to end (MN-043, IB-014, D13): the real session supervisor's weekly 2FA
alerts → the adapter's bus encoding (``NatsSink.alert``) → the monitor's Alertmanager conversion →
Alertmanager's route tree (``infra/observability/alertmanager/alertmanager.yml``) → the Pushover
receiver, plus the monitor's alert inbox (resolve on the adapter's ``info`` alert)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from tb_broker_ibkr.bus import NatsSink
from tb_broker_ibkr.models import Environment
from tb_broker_ibkr.schedule import ET
from tb_monitor.alertbox import Inbox
from tb_monitor.alerts import to_alertmanager

from tb.alerts.v1 import alerts_pb2
from tbcore.audit import MemoryAudit
from tbcore.docstore import MemoryDocStore

from .conftest import make_harness, run

AM = Path(__file__).resolve().parents[3] / "infra" / "observability" / "alertmanager" / "alertmanager.yml"


def _matches(matchers: list[str], labels: dict[str, str]) -> bool:
    for m in matchers:
        k, op, v = re.match(r'(\w+)\s*(=~|!=|=)\s*"?([^"]*)"?', m).groups()  # type: ignore[union-attr]
        got = labels.get(k, "")
        if (op == "=" and got != v) or (op == "!=" and got == v) or (op == "=~" and not re.fullmatch(v, got)):
            return False
    return True


def route(cfg: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """First matching child route of the top-level tree (Alertmanager's default ``continue: false``),
    with inherited timing; the root receiver when nothing matches."""
    root: dict[str, Any] = cfg["route"]
    for child in root.get("routes", []):
        if _matches(child.get("matchers", []), labels):
            return {**{k: v for k, v in root.items() if k != "routes"}, **child}
    return root


def receiver(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    out: dict[str, Any] = next(r for r in cfg["receivers"] if r["name"] == name)
    return out


class _Capture(NatsSink):
    def __init__(self) -> None:
        super().__init__("nats://unused", "u", "p", Environment.LIVE)
        self.sent: list[tuple[str, bytes]] = []

    async def _publish(self, subject: str, payload: bytes, msg_id: str, *, durable: bool = True) -> None:
        self.sent.append((subject, payload))


def test_mn043_ib014_weekly_reauth_pages_through_pushover_emergency_and_resolves_in_the_inbox() -> None:
    cfg = yaml.safe_load(AM.read_text())
    sink, box = _Capture(), Inbox(MemoryDocStore(), MemoryAudit())

    async def body() -> None:
        h = make_harness(
            start=datetime(2026, 10, 18, 16, 50, tzinfo=ET),
            environment=Environment.LIVE,
            managed=["U0000001"],
            accounts={"ibkr-live-bot": "U0000001"},
        )
        await h.start()
        await h.until_ready()
        await h.clock.advance(10 * 60)  # Sunday 17:00 ET: IBC cold restart, IBKR Mobile push sent
        h.gateway.require_login()
        await h.clock.advance(5 * 60)  # push not approved within the grace
        for ev in h.sink.alerts:
            await sink.alert(ev)
        h.gateway.approve_2fa()
        await h.until_ready(120)
        for key in h.sink.resolved:
            await sink.resolve_alert(key, "IBKR session ready")
        await h.stop()

    run(body)
    alerts = []
    for subject, payload in sink.sent:
        a = alerts_pb2.Alert()
        a.ParseFromString(payload)
        alerts.append((subject, a))
        box.on_alert(a)
    crit = [a for s, a in alerts if s == "alert.critical"]
    warn = [a for s, a in alerts if s == "alert.warning"]
    assert len(crit) == 1 and len(warn) == 1
    page = to_alertmanager(crit[0])
    assert (
        page["labels"]["severity"] == "critical"
        and page["annotations"]["runbook_url"] == "docs/runbooks/ibkr-weekly-2fa.md"
    )
    r = route(cfg, page["labels"])
    assert (
        r["receiver"] == "pushover-critical" and "mute_time_intervals" not in r
    )  # quiet hours never mute critical (MN-003)
    po = receiver(cfg, "pushover-critical")["pushover_configs"][0]
    assert po["priority"] == "2" and po["retry"] == "2m" and po["expire"] == "1h"  # repeats until acknowledged (D13)
    wait = int(str(r["group_wait"]).rstrip("s"))
    assert wait <= 60 - 30, "bus → monitor → Alertmanager → Pushover within 60 s (section 13 timeliness)"
    reminder = route(cfg, to_alertmanager(warn[0])["labels"])
    assert reminder["receiver"] == "pushover-warning" and reminder["mute_time_intervals"] == ["quiet-hours"]
    rec = next(x for x in box.list() if x.alert.dedupe_key == crit[0].dedupe_key)
    assert rec.status == "resolved", (
        "the adapter's info alert on the same dedupe key resolves the inbox record (MN-041)"
    )


def test_mn043_prometheus_reauth_during_session_escalates_to_critical() -> None:
    rules = yaml.safe_load((AM.parents[1] / "prometheus" / "rules" / "ibkr.yml").read_text())
    by = {r["alert"]: r for g in rules["groups"] for r in g["rules"]}
    assert by["IbkrReauthRequired"]["labels"]["severity"] == "warning"
    assert by["IbkrReauthRequiredDuringSession"]["labels"]["severity"] == "critical"
    cfg = yaml.safe_load(AM.read_text())
    assert (
        route(cfg, {"severity": "critical", "alertname": "IbkrReauthRequiredDuringSession"})["receiver"]
        == "pushover-critical"
    )
