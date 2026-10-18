"""Opt-in tests against a real IB Gateway / TWS **paper** session (BR-001 paper leg; spike V1).

Skipped unless ``TB_IBKR_PAPER_TEST=1``; run with ``make test-ibkr-paper``. Never point these at a
live gateway: every test asserts the account code is a paper account (``DU``/``DF``).

Environment:
  TB_IBKR_PAPER_HOST / TB_IBKR_PAPER_PORT   gateway API (default 127.0.0.1:4002)
  TB_IBKR_PAPER_ACCOUNT                     paper account code (DU...)
  TB_IBKR_PAPER_CON_ID                      contract to trade (default 756733 = SPY)
  TB_IBKR_SPIKE_OUT                         where spike V1 writes its JSON result (default ./v1-order-ref.json)
  TB_IBKR_SPIKE_PHASE                       "submit" (default) or "after_restart" (re-reads refs by permId)
  TB_IBKR_SPIKE_FILLS=1                     also execute 1-share marketable orders to read refs from executions
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tb_broker_ibkr.adapter import PAPER_ACCOUNT_PREFIXES, AdapterConfig, IbkrAdapter, round_to_tick
from tb_broker_ibkr.clock import SystemClock
from tb_broker_ibkr.cursor_store import CursorStore
from tb_broker_ibkr.events import RecordingSink
from tb_broker_ibkr.ib_async_port import IbAsyncPort
from tb_broker_ibkr.instruments import StaticInstrumentResolver
from tb_broker_ibkr.models import Environment, Instrument, OrderRequest, OrderState, OrderType, Side, TimeInForce
from tb_broker_ibkr.port import HistoricalRequest, PlaceOrder

pytestmark = [
    pytest.mark.ibkr_paper,
    pytest.mark.skipif(os.environ.get("TB_IBKR_PAPER_TEST") != "1", reason="needs a real IB Gateway paper session"),
]

HOST = os.environ.get("TB_IBKR_PAPER_HOST", "127.0.0.1")
PORT = int(os.environ.get("TB_IBKR_PAPER_PORT", "4002"))
ACCOUNT = os.environ.get("TB_IBKR_PAPER_ACCOUNT", "")
CON_ID = int(os.environ.get("TB_IBKR_PAPER_CON_ID", "756733"))
ACCT = "ibkr-paper-rehearsal"
ULID = "01J9ZK3M4N5P6Q7R8S9T0V1W2X"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def require_paper() -> None:
    assert ACCOUNT.startswith(PAPER_ACCOUNT_PREFIXES), "TB_IBKR_PAPER_ACCOUNT must be a paper account (DU...)"


async def wait(predicate: Any, timeout: float = 30) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.2)


async def reference_price(port: IbAsyncPort) -> Decimal:
    bars = await port.historical_bars(
        HistoricalRequest(con_id=CON_ID, exchange="SMART", end=None, duration="2 D", bar_size="1 day")
    )
    assert bars, "no historical data for the paper contract"
    return bars[-1].close


def test_br001_paper_session_resync_and_order_lifecycle() -> None:
    require_paper()

    async def body() -> None:
        sink = RecordingSink()
        port = IbAsyncPort(HOST, PORT, client_id=7, connect_timeout=10)
        instrument = Instrument(instrument_id="rehearsal", con_id=CON_ID, symbol="")
        adapter = IbkrAdapter(
            config=AdapterConfig(environment=Environment.PAPER, accounts={ACCT: ACCOUNT}),
            port=port,
            clock=SystemClock(),
            sink=sink,
            resolver=StaticInstrumentResolver([instrument]),
            cursor=CursorStore(),
        )
        task = asyncio.ensure_future(adapter.run())
        try:
            await wait(lambda: adapter.supervisor.is_ready, 60)
            assert sink.reconciliations, "resync requested reconciliation (OE-008)"
            snap = await adapter.get_account_snapshot(ACCT)
            assert snap.net_liquidation is not None
            price = round_to_tick(await reference_price(port) * Decimal("0.7"), Decimal("0.01"), up=False)
            cid = f"tb1-rehears-{ULID}-{datetime.now(UTC).second}"
            ack = await adapter.submit_order(
                OrderRequest(
                    client_order_id=cid,
                    broker_account_id=ACCT,
                    instrument_id="rehearsal",
                    side=Side.BUY,
                    qty=Decimal(1),
                    order_type=OrderType.LIMIT,
                    limit_price=price,
                    tif=TimeInForce.DAY,
                )
            )
            assert ack.broker_order_id.isdigit(), "permId is the durable broker id (IB-003)"
            await adapter.cancel_order(ACCT, client_order_id=cid)
            await wait(lambda: any(o.client_order_id == cid and o.state is OrderState.CANCELED for o in sink.orders))
            assert adapter.supervisor.is_ready, "orderRef echo matched (IB-002)"
        finally:
            adapter.stop()
            port.disconnect()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(body())


def test_v1_order_ref_length_and_persistence() -> None:
    """Spike V1: the longest orderRef IBKR keeps, and whether it survives on open/completed orders,
    executions, and a gateway restart. Writes a JSON result to record in brokers-ibkr.md."""
    require_paper()
    out = Path(os.environ.get("TB_IBKR_SPIKE_OUT", "v1-order-ref.json"))
    phase = os.environ.get("TB_IBKR_SPIKE_PHASE", "submit")

    async def body() -> None:
        port = IbAsyncPort(HOST, PORT, client_id=8, connect_timeout=10)
        await port.connect()
        try:
            if phase == "after_restart":
                result = json.loads(out.read_text())
                seen = {s.perm_id: s.order_ref for s in [*await port.open_orders(), *await port.completed_orders()]}
                for row in result["orders"]:
                    row["ref_after_restart"] = seen.get(row["perm_id"])
                    row["persisted_after_restart"] = row["ref_after_restart"] == row["sent"]
                out.write_text(json.dumps(result, indent=2))
                return
            price = round_to_tick(await reference_price(port) * Decimal("0.7"), Decimal("0.01"), up=False)
            rows = []
            for length in (20, 40, 44, 64, 128, 255):
                ref = (f"tb1-v1spike-{ULID}-" + "x" * 255)[:length]
                order_id = port.place_order(
                    PlaceOrder(
                        account=ACCOUNT,
                        con_id=CON_ID,
                        exchange="SMART",
                        currency="USD",
                        action="BUY",
                        total_qty=Decimal(1),
                        order_type="LMT",
                        tif="DAY",
                        order_ref=ref,
                        lmt_price=price,
                    )
                )
                await asyncio.sleep(2)
                echoed = {s.ib_order_id: s for s in await port.open_orders() if s.ib_client_id == 8}
                snap = echoed.get(order_id)
                rows.append(
                    {
                        "length": length,
                        "sent": ref,
                        "perm_id": snap.perm_id if snap else None,
                        "echoed_open_order": snap.order_ref if snap else None,
                        "exact": bool(snap and snap.order_ref == ref),
                    }
                )
                port.cancel_order(order_id)
            if os.environ.get("TB_IBKR_SPIKE_FILLS") == "1":
                ref = f"tb1-v1fills-{ULID}-1"
                port.place_order(
                    PlaceOrder(
                        account=ACCOUNT,
                        con_id=CON_ID,
                        exchange="SMART",
                        currency="USD",
                        action="BUY",
                        total_qty=Decimal(1),
                        order_type="MKT",
                        tif="DAY",
                        order_ref=ref,
                    )
                )
                await asyncio.sleep(5)
                fills = [f for f, _ in await port.executions(None) if f.order_ref.startswith("tb1-v1fills")]
                echoed_ref = fills[0].order_ref if fills else None
                rows.append({"length": len(ref), "sent": ref, "execution_order_ref": echoed_ref})
            result = {"spike": "V1", "ran_at": datetime.now(UTC).isoformat(), "orders": rows}
            out.write_text(json.dumps(result, indent=2))
            assert rows[0]["exact"], "even a 20-char orderRef did not round-trip"
        finally:
            port.disconnect()

    run(body())
