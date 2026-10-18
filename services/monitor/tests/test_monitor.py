"""Monitor ops slice (section 13): bus alerts → Alertmanager, pre-market checklist, EOD summary."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
import tb_monitor.__main__ as entry
from tb_monitor.alerts import post, report_alert, to_alertmanager
from tb_monitor.reports import Reports

from tb.alerts.v1 import alerts_pb2
from tb.common.v1 import common_pb2
from tb.ledger.v1 import ledger_pb2
from tb.orders.v1 import execution_pb2, orders_pb2


def test_bus_alert_maps_to_alertmanager_with_runbook_and_dedupe() -> None:
    a = alerts_pb2.Alert(
        alert_id="A1",
        severity=alerts_pb2.SEVERITY_CRITICAL,
        source="ledger",
        subject="reconciliation break: encroachment",
        message="sim-paper AAPL: manual sell",
        dedupe_key="ledger-break-b1",
        runbook_url="docs/runbooks/reconciliation-break.md",
        occurred_at=1_700_000_000_000_000_000,
        correlation_id="c1",
    )
    am = to_alertmanager(a)
    assert am["labels"]["severity"] == "critical" and am["labels"]["dedupe_key"] == "ledger-break-b1"
    assert am["labels"]["alertname"] == "BusReconciliationBreakEncroachment"
    assert am["annotations"]["runbook_url"] == "docs/runbooks/reconciliation-break.md"
    assert am["startsAt"] < am["endsAt"]


def test_post_delivers_to_alertmanager_api() -> None:
    got: list[Any] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    post(
        f"http://127.0.0.1:{srv.server_port}",
        [report_alert("EndOfDaySummary", "done", datetime(2026, 10, 14, 20, 10, tzinfo=UTC))],
    )
    t.join(5)
    srv.server_close()
    assert got and got[0][0] == "/api/v2/alerts" and got[0][1][0]["labels"]["kind"] == "report"


def status(ready: bool = True) -> execution_pb2.TradingStatus:
    acct = execution_pb2.AccountTradingStatus(
        broker_account_id="sim-paper",
        session_state="SESSION_STATE_READY" if ready else "SESSION_STATE_DEGRADED",
        reconciliation_status="RECONCILIATION_STATUS_OK",
        pdt_guard="off",
        environment="paper",
    )
    return execution_pb2.TradingStatus(
        global_state="running",
        accounts=[acct],
        clock_ok=True,
        strategies=[execution_pb2.StrategyTradingStatus(strategy_id="orb", mode=execution_pb2.STRATEGY_MODE_PAPER)],
    )


def test_premarket_checklist_passes_and_fails_closed() -> None:
    r = Reports()
    ok, text = r.premarket("2026-10-14")
    assert not ok and "heartbeat" in text
    r.on_status(status())
    ok, text = r.premarket("2026-10-14")
    assert ok and "OK   sim-paper" in text
    r.on_status(status(ready=False))
    assert not r.premarket("2026-10-14")[0]
    st = status()
    st.active_kill_switches.add(event_id="k1")
    r.on_status(st)
    ok, text = r.premarket("2026-10-14")
    assert not ok and "kill switch" in text


def test_eod_summary_per_account_and_strategy() -> None:
    r = Reports()
    d = common_pb2.Decimal
    date = "2026-10-14"
    r.on_fill(date, orders_pb2.ExecutionReport(broker_account_id="sim-paper", fees=d(value="0.5")))
    r.on_fill(date, orders_pb2.ExecutionReport(broker_account_id="sim-paper", fees=d(value="0.25")))
    r.on_decision(date, orders_pb2.RiskDecision(strategy_id="orb", approved=True))
    r.on_decision(date, orders_pb2.RiskDecision(strategy_id="orb", approved=False, first_failure="symbol_segregation"))
    r.on_recon(
        date, ledger_pb2.ReconciliationResult(broker_account_id="sim-paper", status=ledger_pb2.RECONCILIATION_STATUS_OK)
    )
    r.on_ledger_update(
        date,
        ledger_pb2.LedgerUpdate(
            positions=[
                ledger_pb2.StrategyPosition(
                    broker_account_id="sim-paper", owner="orb", instrument_id="I1", realized_pnl=d(value="42.5")
                )
            ]
        ),
    )
    r.on_alert(date, "critical")
    text = r.eod(date)
    assert "sim-paper: 2 fills, fees 0.75, recon OK, open breaks 0" in text
    assert "sim-paper/orb: realized P&L today 42.5" in text
    assert "orb: 1 approved intents, rejects: symbol_segregation 1" in text
    assert "alerts: critical 1" in text


class _FakeJS:
    def __init__(self) -> None:
        self.subs: list[tuple[str, Any]] = []

    async def subscribe(self, subject: str, **kw: Any) -> None:
        self.subs.append((subject, kw.get("stream")))


class _FakeNC:
    def __init__(self) -> None:
        self.js = _FakeJS()

    def jetstream(self) -> _FakeJS:
        return self.js

    async def subscribe(self, *_: Any, **__: Any) -> None:
        return None

    async def drain(self) -> None:
        return None


def test_every_jetstream_subscription_names_its_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """The monitor's NATS user may not look streams up ($JS.API.STREAM.NAMES); unnamed subscribes time out."""
    nc = _FakeNC()

    async def connect(**_: Any) -> _FakeNC:
        return nc

    monkeypatch.setattr(entry.nats, "connect", connect)
    stop = asyncio.Event()
    stop.set()
    asyncio.run(entry.run(entry.Settings(service="monitor"), "pw", Reports(), {}, stop))
    assert dict(nc.js.subs) == {
        "alert.>": "ALERTS",
        "recon.result.>": "LEDGER",
        "ord.decision": "ORDERS",
        "exec.>": "ORDERS",
        "ledger.update.>": "LEDGER",
    }
