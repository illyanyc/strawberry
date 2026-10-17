"""Session supervision with the fake gateway on virtual time.

OE-004 (orders only when ready), OE-008 (resync before ready), IB-005/IB-006 (accounts and
environment), IB-010..IB-016 (state machine, backoff, connectivity codes, maintenance windows,
weekly re-auth and 2FA alerts).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from tb_broker_ibkr.events import RUNBOOK_2FA
from tb_broker_ibkr.fakes.gateway import GatewayState
from tb_broker_ibkr.models import BrokerUnavailableError, Environment, SessionState
from tb_broker_ibkr.schedule import ET, Severity

from .conftest import ACCT, PAPER_CODE, make_harness, order, run


def test_oe004_ready_only_after_resync_and_status_published() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        states = [s.state for s in h.sink.statuses if s.broker_account_id == ACCT]
        assert states[:4] == [
            SessionState.CONNECTING,
            SessionState.AUTHENTICATED,
            SessionState.SYNCING,
            SessionState.READY,
        ]
        assert h.sink.reconciliations and h.sink.reconciliations[-1].reason == "reconnect"
        assert h.sink.snapshots and h.sink.snapshots[-1].broker_account_id == ACCT
        status = h.adapter.session_status(ACCT)
        assert status.connected and status.authenticated and not status.reauth_required
        await h.stop()

    run(body)


def test_oe004_orders_refused_until_ready() -> None:
    async def body() -> None:
        h = make_harness()
        h.gateway.state = GatewayState.DOWN
        await h.start()
        with pytest.raises(BrokerUnavailableError) as err:
            await h.adapter.submit_order(order())
        assert err.value.state in {SessionState.CONNECTING, SessionState.DISCONNECTED}
        assert h.gateway.placements == []
        h.gateway.come_up()
        await h.until_ready()
        ack = await h.adapter.submit_order(order())
        assert ack.broker_order_id
        await h.stop()

    run(body)


def test_ib012_1100_degrades_and_1102_resyncs() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        recon_before = len(h.sink.reconciliations)
        h.gateway.emit_error(1100, "Connectivity between IB and Trader Workstation has been lost.")
        await h.clock.settle()
        assert h.state is SessionState.DEGRADED
        assert "connectivity_1100" in h.adapter.session_status(ACCT).degraded_reasons
        with pytest.raises(BrokerUnavailableError):
            await h.adapter.submit_order(order())
        h.gateway.emit_error(1102, "Connectivity restored - data maintained.")
        await h.until_ready(10)
        assert len(h.sink.reconciliations) == recon_before + 1
        assert h.sink.alerts == []  # restored within the grace: no page
        await h.stop()

    run(body)


def test_oe008_disconnect_reconnects_and_publishes_missed_fills_once() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        ack = await h.adapter.submit_order(order())
        h.gateway.go_down()
        await h.clock.settle()
        assert h.state is SessionState.DISCONNECTED
        # Fill happens at IBKR while we are disconnected (no port receives it).
        h.gateway.fill(int(ack.broker_order_id), Decimal(10), Decimal("180.25"))
        assert h.sink.executions == []
        h.gateway.come_up()
        await h.until_ready(60)
        assert [e.broker_exec_id for e in h.sink.executions] == [h.gateway.executions[0][0].exec_id]
        assert h.sink.executions[0].fees == Decimal("1.00")
        assert h.sink.orders[-1].state.value == "filled"
        assert h.sink.reconciliations[-1].reason == "reconnect"
        # A second reconnect does not republish (dedupe on broker_exec_id).
        h.gateway.go_down()
        await h.clock.settle()
        h.gateway.come_up()
        await h.until_ready(60)
        assert len(h.sink.executions) == 1
        await h.stop()

    run(body)


def test_ib011_no_tight_reconnect_loop() -> None:
    async def body() -> None:
        h = make_harness()
        h.gateway.state = GatewayState.DOWN
        await h.start()
        await h.clock.advance(600)
        # Backoff grows to 30-60 s: ten minutes of outage is ~20 attempts, never hundreds.
        assert 8 <= h.gateway.connect_attempts <= 30
        await h.stop()

    run(body)


def test_ib015_midweek_outage_pages_after_grace_then_resolves() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        h.gateway.go_down()
        await h.clock.advance(120)
        assert h.sink.alerts == [], "inside the 3-minute grace"
        await h.clock.advance(120)
        assert len(h.sink.alerts) == 1
        alert = h.sink.alerts[0]
        assert alert.severity is Severity.CRITICAL
        assert "2FA" in alert.message
        assert h.adapter.session_status(ACCT).reauth_required
        await h.clock.advance(600)
        assert len(h.sink.alerts) == 1, "one alert per outage"
        h.gateway.come_up()
        await h.until_ready(120)
        assert h.sink.resolved == [h.adapter.supervisor.dedupe_key]
        assert not h.adapter.session_status(ACCT).reauth_required
        await h.stop()

    run(body)


def test_ib013_daily_restart_is_quiet() -> None:
    async def body() -> None:
        h = make_harness(start=datetime(2026, 10, 13, 23, 40, tzinfo=ET))
        await h.start()
        await h.until_ready()
        await h.clock.advance(6 * 60)  # 23:46: IBC daily restart
        h.gateway.go_down()
        await h.clock.advance(4 * 60)
        assert "daily_restart" in h.adapter.session_status(ACCT).degraded_reasons
        h.gateway.come_up()
        await h.until_ready(120)
        assert h.sink.alerts == []
        await h.stop()

    run(body)


def test_ib013_daily_restart_overrun_pages() -> None:
    async def body() -> None:
        h = make_harness(start=datetime(2026, 10, 13, 23, 45, tzinfo=ET))
        await h.start()
        await h.until_ready()
        h.gateway.require_login()  # token reuse failed: needs a fresh login
        await h.clock.advance(15 * 60)
        assert [a.severity for a in h.sink.alerts] == [Severity.CRITICAL]
        assert h.adapter.session_status(ACCT).reauth_required
        await h.stop()

    run(body)


def test_ib014_weekly_login_reminder_and_2fa_alert() -> None:
    async def body() -> None:
        sunday = datetime(2026, 10, 18, 16, 50, tzinfo=ET)
        h = make_harness(
            start=sunday, environment=Environment.LIVE, managed=["U0000001"], accounts={"ibkr-live-bot": "U0000001"}
        )
        await h.start()
        await h.until_ready()
        await h.clock.advance(10 * 60)  # 17:00 ET: IBC cold restart, IBKR Mobile push sent
        h.gateway.require_login()
        await h.clock.advance(60)
        reminders = [a for a in h.sink.alerts if a.subject.endswith("weekly_login")]
        assert len(reminders) == 1 and reminders[0].severity is Severity.WARNING
        assert h.adapter.session_status("ibkr-live-bot").reauth_required
        await h.clock.advance(4 * 60)  # push not approved within the grace
        pages = [a for a in h.sink.alerts if a.subject.endswith(".session")]
        assert len(pages) == 1 and pages[0].severity is Severity.CRITICAL
        assert pages[0].runbook == RUNBOOK_2FA
        assert "approve the IBKR Mobile push" in pages[0].message
        h.gateway.approve_2fa()
        await h.until_ready(120)
        assert h.adapter.supervisor.dedupe_key in h.sink.resolved
        await h.stop()

    run(body)


def test_ib014_weekend_maintenance_is_quiet_until_cold_restart() -> None:
    async def body() -> None:
        saturday = datetime(2026, 10, 17, 12, 0, tzinfo=ET)
        h = make_harness(start=saturday)
        await h.start()
        await h.until_ready()
        h.gateway.go_down()
        await h.clock.advance(3 * 3600)
        assert h.sink.alerts == []
        assert "weekend_maintenance" in h.adapter.session_status(ACCT).degraded_reasons
        await h.stop()

    run(body)


def test_ib010_heartbeat_timeout_reconnects() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        attempts = h.gateway.connect_attempts
        h.gateway.hang_heartbeat = True
        await h.clock.advance(26)  # 15 s interval + 10 s timeout
        assert h.gateway.connect_attempts > attempts, "a hung gateway is dropped and redialled"
        assert any("heartbeat_timeout" in s.degraded_reasons for s in h.sink.statuses)
        h.gateway.hang_heartbeat = False
        await h.until_ready(60)
        await h.stop()

    run(body)


def test_ib006_environment_mismatch_never_ready() -> None:
    async def body() -> None:
        # A paper adapter whose account maps to a live IBKR account code.
        h = make_harness(managed=["U7654321"], accounts={ACCT: "U7654321"})
        await h.start()
        await h.clock.advance(60)
        assert h.state is SessionState.DEGRADED
        assert h.adapter.session_status(ACCT).degraded_reasons == ("environment_mismatch",)
        assert h.sink.alerts and h.sink.alerts[0].severity is Severity.CRITICAL
        with pytest.raises(BrokerUnavailableError):
            await h.adapter.submit_order(order())
        await h.stop()

    run(body)


def test_ib005_mapped_account_must_be_managed() -> None:
    async def body() -> None:
        h = make_harness(managed=["DU9999999"])
        await h.start()
        await h.clock.advance(30)
        assert h.adapter.session_status(ACCT).degraded_reasons == ("account_not_managed",)
        await h.stop()

    run(body)


def test_ib006_account_ids_must_name_the_environment() -> None:
    with pytest.raises(ValueError, match="does not belong"):
        make_harness(accounts={"ibkr-live-bot": PAPER_CODE})


def test_ib016_resync_failure_never_reaches_ready() -> None:
    async def body() -> None:
        h = make_harness()
        original = h.gateway.ports[0].executions
        calls = {"n": 0}

        async def flaky(since: datetime | None) -> list:  # type: ignore[type-arg]
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("dropped mid-resync")
            return await original(since)

        h.gateway.ports[0].executions = flaky  # type: ignore[method-assign]
        await h.start()
        await h.until_ready(60)
        assert calls["n"] >= 2
        states = [(s.state, s.degraded_reasons) for s in h.sink.statuses]
        failed_at = states.index((SessionState.DEGRADED, ("resync_failed",)))
        assert SessionState.READY not in [st for st, _ in states[:failed_at]]
        await h.stop()

    run(body)


def test_stable_session_resets_backoff() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        backoff = h.adapter.supervisor._backoff
        backoff._attempt = 5
        await h.clock.advance(timedelta(minutes=6).total_seconds())
        assert backoff.attempt == 0
        await h.stop()

    run(body)
