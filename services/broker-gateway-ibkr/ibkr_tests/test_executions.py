"""Executions: IB-030 (commission join), IB-031 (dedupe across restarts), IB-032 (corrections),
IB-033 (execution gap), OE-006, VP-001 (manual orders placed in the broker's own app),
section 04 acceptance "manual order appears without attribution".
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tb_broker_ibkr.cursor_store import FileCursorStore
from tb_broker_ibkr.models import BrokerUnavailableError, OrderState, OwnerKind
from tb_broker_ibkr.port import CommissionFill
from tb_broker_ibkr.schedule import ET

from .conftest import AAPL, ACCT, PAPER_CODE, SPY, Harness, cid, make_harness, order, run


async def ready(**kwargs: Any) -> Harness:
    h = make_harness(**kwargs)
    await h.start()
    await h.until_ready()
    return h


async def submit(h: Harness, **changes: Any) -> int:
    ack = await h.drive(h.adapter.submit_order(order(**changes)))
    return int(ack.broker_order_id)


def test_ib030_commission_joined_into_execution_report() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(4), Decimal("180.20"), commission=Decimal("0.35"))
        await h.clock.settle()
        (report,) = h.sink.executions
        assert report.fees == Decimal("0.35") and report.fee_currency == "USD"
        assert (report.qty, report.price, report.cum_qty) == (Decimal(4), Decimal("180.20"), Decimal(4))
        assert report.client_order_id == cid() and report.owner is OwnerKind.STRATEGY
        assert report.broker_order_id == str(perm) and report.instrument_id == AAPL.instrument_id
        assert h.sink.orders[-1].state is OrderState.PARTIALLY_FILLED
        assert h.sink.orders[-1].filled_qty == Decimal(4)
        await h.stop()

    run(body)


def test_ib030_commission_before_execution() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(10), Decimal("180.25"), commission=Decimal("1.00"), commission_first=True)
        await h.clock.settle()
        assert [e.fees for e in h.sink.executions] == [Decimal("1.00")]
        await h.stop()

    run(body)


def test_ib030_missing_commission_publishes_after_wait_and_records_late_fee() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        fill = h.gateway.fill(perm, Decimal(10), Decimal("180.25"), commission=None)
        await h.clock.settle()
        assert h.sink.executions == [], "held for the commission"
        await h.clock.advance(2.5)
        (report,) = h.sink.executions
        assert report.fees is None
        # The late report is not republished (consumers dedupe) but is visible on query.
        h.gateway.ports[0].handlers.on_commission(CommissionFill(fill.exec_id, Decimal("1.00"), "USD"))
        await h.clock.settle()
        assert len(h.sink.executions) == 1
        h.gateway.executions[0] = (fill, None)
        queried = await h.adapter.list_executions(ACCT)
        assert queried[0].fees == Decimal("1.00")
        await h.stop()

    run(body)


def test_oe006_duplicate_execution_messages_published_once() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(10), Decimal("180.25"), duplicate=True)
        await h.clock.settle()
        assert len(h.sink.executions) == 1
        await h.stop()

    run(body)


def test_out_of_order_status_never_regresses() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(10), Decimal("180.25"))
        await h.clock.settle()
        assert h.sink.orders[-1].state is OrderState.FILLED
        # A stale "Submitted" with less filled arrives late.
        stale = h.gateway.orders[perm].snapshot()
        h.gateway.ports[0].handlers.on_order(
            replace(stale, status="Submitted", filled=Decimal(4), remaining=Decimal(6))
        )
        await h.clock.settle()
        last = h.sink.orders[-1]
        assert last.state is OrderState.FILLED and last.filled_qty == Decimal(10)
        await h.stop()

    run(body)


def test_vp001_manual_order_in_broker_app_is_unattributed() -> None:
    async def body() -> None:
        h = await ready()
        manual = h.gateway.place_external_order(PAPER_CODE, SPY.con_id, "SPY", "BUY", Decimal(3))
        await h.clock.settle()
        orders = await h.adapter.list_orders(ACCT)
        (seen,) = [o for o in orders if o.broker_order_id == str(manual.perm_id)]
        assert seen.owner is OwnerKind.EXTERNAL and seen.client_order_id == ""
        assert seen.instrument_id == SPY.instrument_id
        h.gateway.fill(manual.perm_id, Decimal(3), Decimal("500.10"))
        await h.clock.settle()
        (report,) = h.sink.executions
        assert report.owner is OwnerKind.EXTERNAL and report.client_order_id == ""
        assert report.strategy_short_id == ""
        await h.stop()

    run(body)


def test_vp001_malformed_tb1_ref_goes_to_manual_with_alert() -> None:
    async def body() -> None:
        h = await ready()
        h.gateway.place_external_order(PAPER_CODE, SPY.con_id, "SPY", "BUY", Decimal(1), order_ref="tb1-oops")
        await h.clock.settle()
        orders = await h.adapter.list_orders(ACCT)
        assert orders[0].owner is OwnerKind.EXTERNAL
        assert any(a.subject.endswith(".attribution") for a in h.sink.alerts)
        await h.stop()

    run(body)


def test_ib005_orders_of_unmapped_accounts_are_ignored() -> None:
    async def body() -> None:
        h = await ready(managed=[PAPER_CODE, "DU0000002"])
        h.gateway.place_external_order("DU0000002", SPY.con_id, "SPY", "BUY", Decimal(1))
        await h.clock.settle()
        assert await h.adapter.list_orders(ACCT) == []
        assert h.sink.orders == []
        await h.stop()

    run(body)


def test_ib032_execution_correction_links_prior_exec() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(5), Decimal("180.25"), exec_id="0000e0d5.6551d5cf.01.01")
        h.gateway.fill(perm, Decimal(0), Decimal("180.24"), exec_id="0000e0d5.6551d5cf.01.02")
        await h.clock.settle()
        assert [e.corrects_exec_id for e in h.sink.executions] == ["", "0000e0d5.6551d5cf.01.01"]
        await h.stop()

    run(body)


def test_ib031_dedupe_survives_restart(tmp_path: Path) -> None:
    async def body() -> None:
        path = tmp_path / "cursor.json"
        h = await ready(cursor=FileCursorStore(path))
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(10), Decimal("180.25"))
        await h.clock.settle()
        assert len(h.sink.executions) == 1
        await h.stop()

        # New process, same gateway and state file: resync sees the execution but does not republish.
        h2 = make_harness(cursor=FileCursorStore(path), gateway=h.gateway, clock=h.clock)
        await h2.start()
        await h2.until_ready()
        assert h2.sink.executions == []
        # A crash before the cursor write would republish instead (at-least-once, consumers dedupe).
        path.unlink()
        h3 = make_harness(cursor=FileCursorStore(path), gateway=h.gateway, clock=h.clock)
        await h3.start()
        await h3.until_ready()
        assert len(h3.sink.executions) == 1
        await h2.stop()
        await h3.stop()

    run(body)


def test_ib033_outage_across_day_boundary_flags_execution_gap() -> None:
    async def body() -> None:
        start = datetime(2026, 10, 13, 15, 0, tzinfo=ET)
        h = await ready(start=start)
        h.gateway.go_down()
        await h.clock.advance(19 * 3600)  # back next day 10:00 ET
        h.gateway.come_up()
        await h.until_ready(120)
        assert h.sink.reconciliations[-1].reason == "execution_gap"
        assert any(a.subject.endswith(".execution_gap") for a in h.sink.alerts)
        await h.stop()

    run(body)


def test_br021_margin_deficit_degrades_account_until_clean_snapshot() -> None:
    async def body() -> None:
        h = await ready()
        h.gateway.account_values[PAPER_CODE] = {"NetLiquidation": "90000", "ExcessLiquidity": "-1500"}
        await h.clock.advance(31)  # next snapshot poll
        snap = h.sink.snapshots[-1]
        assert snap.margin_deficit and snap.excess_liquidity == Decimal(-1500)
        status = h.adapter.session_status(ACCT)
        assert status.state.value == "degraded" and status.degraded_reasons == ("margin_deficit",)
        assert sum(a.subject.endswith(".margin_deficit") for a in h.sink.alerts) == 1
        with pytest.raises(BrokerUnavailableError):
            await h.adapter.submit_order(order())
        await h.clock.advance(31)
        assert sum(a.subject.endswith(".margin_deficit") for a in h.sink.alerts) == 1, "alert once per episode"
        h.gateway.account_values[PAPER_CODE] = {"NetLiquidation": "90000", "ExcessLiquidity": "2500"}
        await h.clock.advance(31)
        assert h.adapter.session_status(ACCT).state.value == "ready"
        assert h.sink.reconciliations[-1].reason == "margin_restored"
        await h.stop()

    run(body)


def test_br011_resync_fetches_since_last_exec_minus_overlap() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(10), Decimal("180.25"))
        await h.clock.settle()
        last = h.sink.executions[-1].event_time
        calls: list[datetime | None] = []
        original = h.gateway.ports[0].executions

        async def spy(since: datetime | None) -> list:  # type: ignore[type-arg]
            calls.append(since)
            return await original(since)

        h.gateway.ports[0].executions = spy  # type: ignore[method-assign]
        h.gateway.go_down()
        await h.clock.settle()
        h.gateway.come_up()
        await h.until_ready(60)
        assert calls and calls[0] is not None
        assert (last / 1e9) - calls[0].timestamp() == pytest.approx(300, abs=1)
        await h.stop()

    run(body)


def test_snapshot_positions_keyed_by_con_id() -> None:
    async def body() -> None:
        h = await ready()
        perm = await submit(h)
        h.gateway.fill(perm, Decimal(10), Decimal("180.25"))
        await h.clock.settle()
        snap = await h.adapter.get_account_snapshot(ACCT)
        (pos,) = snap.positions
        assert (pos.con_id, pos.instrument_id, pos.qty) == (AAPL.con_id, AAPL.instrument_id, Decimal(10))
        await h.stop()

    run(body)
