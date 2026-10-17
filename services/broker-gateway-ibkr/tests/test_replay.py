"""Recorded-message replay (section 04 "recorded-message replay tests"): fixtures in ``fixtures/`` are
rebuilt into real ``ib_async`` objects and run through the production conversion layer and adapter.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from ib_async import CommissionReport, Contract, Execution, Fill, Order, OrderStatus, Trade
from tb_broker_ibkr.fakes.replay import Recorder, deliver, iter_fixtures, load_fixture, replay
from tb_broker_ibkr.models import OrderState, OwnerKind, SessionState

from .conftest import Harness, make_harness, run

FIXTURES = Path(__file__).parent / "fixtures"


async def replayed(name: str) -> Harness:
    h = make_harness()
    await h.start()
    await h.until_ready()
    await replay(h.gateway.ports[0].handlers, load_fixture(FIXTURES / name), h.clock)
    await h.clock.advance(3)  # past the commission wait
    return h


def states(h: Harness, perm_id: int) -> list[OrderState]:
    out: list[OrderState] = []
    for o in h.sink.orders:
        if o.broker_order_id == str(perm_id) and (not out or out[-1] is not o.state):
            out.append(o.state)
    return out


def test_replay_partial_fill_then_cancel() -> None:
    async def body() -> None:
        h = await replayed("partial_fill_then_cancel.jsonl")
        assert states(h, 1849274651) == [
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.PENDING_CANCEL,
            OrderState.CANCELED,
        ]
        final = h.sink.orders[-1]
        assert final.filled_qty == Decimal(40) and final.owner is OwnerKind.STRATEGY
        assert final.strategy_short_id == "swing01"
        (execution,) = h.sink.executions
        assert (execution.qty, execution.price, execution.fees) == (Decimal(40), Decimal("180.25"), Decimal("0.2"))
        assert execution.event_time == int(datetime(2026, 10, 13, 14, 0, 1, tzinfo=UTC).timestamp()) * 10**9
        await h.stop()

    run(body)


def test_replay_manual_order_from_mobile_is_external() -> None:
    async def body() -> None:
        h = await replayed("manual_order_mobile.jsonl")
        assert states(h, 1849275000)[-1] is OrderState.FILLED
        assert {o.owner for o in h.sink.orders} == {OwnerKind.EXTERNAL}
        assert all(o.client_order_id == "" for o in h.sink.orders)
        (execution,) = h.sink.executions
        assert execution.owner is OwnerKind.EXTERNAL and execution.side.value == "sell"
        assert execution.fees == Decimal("1.0")
        await h.stop()

    run(body)


def test_replay_daily_reset_degrades_then_recovers_quietly() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        before = len(h.sink.reconciliations)
        events = load_fixture(FIXTURES / "daily_reset.jsonl")
        handlers = h.gateway.ports[0].handlers
        for event in events[:3]:
            deliver(handlers, event)
        await h.clock.settle()
        assert h.state is SessionState.DEGRADED
        await replay(handlers, events[3:], h.clock)
        await h.until_ready(10)
        assert len(h.sink.reconciliations) == before + 1
        assert h.sink.alerts == []
        await h.stop()

    run(body)


def test_replay_out_of_order_and_duplicates() -> None:
    async def body() -> None:
        h = await replayed("out_of_order_duplicates.jsonl")
        # The second fill publishes first: its commission had already arrived (IB-030).
        assert [(e.broker_exec_id, e.qty, e.fees) for e in h.sink.executions] == [
            ("0000e0d5.67a1b301.01.01", Decimal(4), Decimal("0.35")),
            ("0000e0d5.67a1b300.01.01", Decimal(6), Decimal("0.65")),
        ]
        final = h.sink.orders[-1]
        assert final.state is OrderState.FILLED and final.filled_qty == Decimal(10)
        await h.stop()

    run(body)


def test_replay_reject_inactive() -> None:
    async def body() -> None:
        h = await replayed("reject_inactive.jsonl")
        assert states(h, 1849277000)[-1] is OrderState.REJECTED
        assert h.sink.orders[-1].strategy_short_id == "meanrv01"
        await h.stop()

    run(body)


def test_every_fixture_loads_and_uses_fake_accounts_only() -> None:
    files = list(iter_fixtures(FIXTURES))
    assert len(files) >= 5
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "DU0000001" in text or "error" in text
        for event in load_fixture(path):
            account = event.data.get("order", {}).get("account") or event.data.get("execution", {}).get("acctNumber")
            assert account in (None, "", "DU0000001"), f"{path.name}: real-looking account {account}"


def test_recorder_round_trips_and_redacts_accounts(tmp_path: Path) -> None:
    start = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    buf = io.StringIO()
    rec = Recorder(buf, start)
    contract = Contract(conId=265598, symbol="AAPL", secType="STK", exchange="SMART", currency="USD")
    order = Order(orderId=3, clientId=1, permId=42, account="U1234567", orderRef="x", action="BUY", totalQuantity=1)
    rec.trade(start, "openOrder", Trade(contract, order, OrderStatus(orderId=3, status="Submitted", remaining=1)))
    execution = Execution(execId="e.01", time=start, acctNumber="U1234567", side="BOT", shares=1, price=1.5, permId=42)
    rec.fill(start, Fill(contract, execution, CommissionReport(), start))
    rec.commission(start, CommissionReport(execId="e.01", commission=0.5, currency="USD"))
    rec.error(start, -1, 1100, "lost")
    text = buf.getvalue()
    assert "U1234567" not in text and "DU0000001" in text
    path = tmp_path / "recording.jsonl"
    path.write_text(text, encoding="utf-8")
    assert [e.event for e in load_fixture(path)] == ["openOrder", "execDetails", "commissionReport", "error"]
