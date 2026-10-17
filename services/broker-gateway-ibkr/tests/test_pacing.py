"""Historical pacing and market-data lines: IB-050, IB-051."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from tb_broker_ibkr.clock import FakeClock
from tb_broker_ibkr.pacing import HistoricalPacer, MarketDataLineLimitError, MarketDataLines, PacingLimits
from tb_broker_ibkr.port import HistoricalRequest, PortBar

from .conftest import AAPL, ACCT, TUESDAY_10_ET, make_harness, run


def req(con_id: int = 1, duration: str = "1 D", end_offset: int = 0) -> HistoricalRequest:
    end = TUESDAY_10_ET - timedelta(days=end_offset) if end_offset else None
    return HistoricalRequest(con_id=con_id, exchange="SMART", end=end, duration=duration, bar_size="1 min")


async def send_all(pacer: HistoricalPacer, clock: FakeClock, requests: list[HistoricalRequest]) -> list[float]:
    """Send requests sequentially through the pacer; return each send time in seconds from start."""
    start = clock.now()
    times: list[float] = []

    async def worker() -> None:
        for r in requests:
            async with pacer.slot(r):
                times.append((clock.now() - start).total_seconds())

    task = asyncio.ensure_future(worker())
    while not task.done():
        await clock.advance(0.5)
    return times


def test_ib050_identical_request_waits_15s() -> None:
    async def body() -> None:
        clock = FakeClock(TUESDAY_10_ET)
        times = await send_all(HistoricalPacer(clock), clock, [req(), req()])
        assert times[0] == 0 and times[1] >= 15

    run(body)


def test_ib050_per_contract_six_per_two_seconds() -> None:
    async def body() -> None:
        clock = FakeClock(TUESDAY_10_ET)
        requests = [req(end_offset=i + 1) for i in range(7)]  # distinct requests, same contract
        times = await send_all(HistoricalPacer(clock), clock, requests)
        assert times[5] < 2 <= times[6]

    run(body)


def test_ib050_rolling_window_limit() -> None:
    async def body() -> None:
        clock = FakeClock(TUESDAY_10_ET)
        limits = PacingLimits(max_requests=5, window=timedelta(minutes=10))
        requests = [req(con_id=i) for i in range(6)]
        times = await send_all(HistoricalPacer(clock, limits), clock, requests)
        assert times[4] < 1 and times[5] >= 600

    run(body)


def test_ib050_pacing_violation_freezes_requests() -> None:
    async def body() -> None:
        clock = FakeClock(TUESDAY_10_ET)
        pacer = HistoricalPacer(clock)
        pacer.penalize()
        times = await send_all(pacer, clock, [req()])
        assert times[0] >= 60

    run(body)


def test_ib050_pacing_error_from_gateway_penalizes_adapter_requests() -> None:
    async def body() -> None:
        h = make_harness()
        await h.start()
        await h.until_ready()
        h.gateway.bars = [
            PortBar(TUESDAY_10_ET, Decimal(1), Decimal(2), Decimal("0.5"), Decimal("1.5"), Decimal(100), None, 3)
        ]
        bars = await h.drive(h.adapter.historical_bars(AAPL.instrument_id, req(con_id=AAPL.con_id)))
        assert bars[0].close == Decimal("1.5") and bars[0].trade_count == 3
        h.gateway.emit_error(
            162, "Historical Market Data Service error message:API historical data query pacing violation", req_id=7
        )
        sent_at = h.clock.now()
        await h.drive(h.adapter.historical_bars(AAPL.instrument_id, req(con_id=AAPL.con_id, duration="2 D")))
        assert (h.gateway.historical_requests[-1][0] - sent_at).total_seconds() >= 60
        assert h.adapter.session_status(ACCT).state.value == "ready", "pacing never degrades the order session"
        await h.stop()

    run(body)


def test_ib051_market_data_line_budget() -> None:
    lines = MarketDataLines(limit=2)
    lines.acquire()
    lines.acquire()
    with pytest.raises(MarketDataLineLimitError):
        lines.acquire()
    lines.release()
    lines.acquire()
