"""Recorded-message fixtures: format, recorder, and replay.

A fixture is JSON Lines; each line is one gateway callback as ``ib_async`` delivers it::

    {"t": 0.0, "event": "openOrder",  "order": {...Order fields...}, "contract": {...}, "status": {...}}
    {"t": 0.1, "event": "orderStatus", ...same shape...}
    {"t": 0.2, "event": "execDetails", "execution": {...Execution fields...}, "contract": {...}}
    {"t": 0.2, "event": "commissionReport", "report": {...CommissionReport fields...}}
    {"t": 0.3, "event": "error", "reqId": -1, "code": 1100, "message": "..."}
    {"t": 0.4, "event": "disconnected"}

Field names are ``ib_async``'s (``orderId``, ``permId``, ``orderRef``, ``totalQuantity``, ``execId``,
``acctNumber``, ...). Replay rebuilds real ``ib_async`` objects and sends them through the same
conversion functions as :class:`~tb_broker_ibkr.ib_async_port.IbAsyncPort`, so fixtures test the
production translation layer. :class:`Recorder` writes this format from a live paper session with
account codes replaced by placeholders (opt-in, ``TB_IBKR_RECORD_PATH``), so recordings never carry
real account identifiers (SE-012, fixture rule in ``AGENTS.md``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from ib_async import CommissionReport, Contract, Execution, Fill, Order, OrderStatus, Trade

from tb_broker_ibkr.clock import FakeClock
from tb_broker_ibkr.ib_async_port import commission_from_report, exec_from_fill, snapshot_from_trade
from tb_broker_ibkr.port import PortHandlers

_CONTRACT_FIELDS = ("conId", "symbol", "secType", "exchange", "primaryExchange", "currency")
_ORDER_FIELDS = (
    "orderId",
    "clientId",
    "permId",
    "account",
    "orderRef",
    "action",
    "totalQuantity",
    "orderType",
    "tif",
    "lmtPrice",
    "auxPrice",
    "outsideRth",
)
_STATUS_FIELDS = ("orderId", "status", "filled", "remaining", "avgFillPrice", "permId", "clientId", "whyHeld")


@dataclass(frozen=True, slots=True)
class FixtureEvent:
    t: float
    event: str
    data: dict[str, Any]


def load_fixture(path: str | Path) -> list[FixtureEvent]:
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        raw = json.loads(line)
        events.append(FixtureEvent(t=float(raw.pop("t", 0.0)), event=raw.pop("event"), data=raw))
    return sorted(events, key=lambda e: e.t)


def _trade(data: dict[str, Any]) -> Trade:
    contract = Contract(**{k: v for k, v in data.get("contract", {}).items() if k in _CONTRACT_FIELDS})
    order = Order(**{k: v for k, v in data.get("order", {}).items() if k in _ORDER_FIELDS})
    status = OrderStatus(**{k: v for k, v in data.get("status", {}).items() if k in _STATUS_FIELDS})
    return Trade(contract=contract, order=order, orderStatus=status, fills=[], log=[])


def _fill(data: dict[str, Any]) -> Fill:
    contract = Contract(**{k: v for k, v in data.get("contract", {}).items() if k in _CONTRACT_FIELDS})
    raw = dict(data["execution"])
    raw["time"] = datetime.fromisoformat(raw["time"]).astimezone(UTC)
    allowed = {f.name for f in fields(Execution)}
    execution = Execution(**{k: v for k, v in raw.items() if k in allowed})
    return Fill(contract=contract, execution=execution, commissionReport=CommissionReport(), time=execution.time)


def deliver(handlers: PortHandlers, event: FixtureEvent) -> None:
    """Send one fixture event to the adapter's port handlers."""
    data = event.data
    if event.event == "openOrder":
        handlers.on_order(snapshot_from_trade(_trade(data), ref_echoed=True))
    elif event.event == "orderStatus":
        handlers.on_order(snapshot_from_trade(_trade(data), ref_echoed=False))
    elif event.event == "execDetails":
        handlers.on_execution(exec_from_fill(_fill(data)))
    elif event.event == "commissionReport":
        commission = commission_from_report(CommissionReport(**data["report"]))
        if commission is not None:
            handlers.on_commission(commission)
    elif event.event == "error":
        handlers.on_error(int(data.get("reqId", -1)), int(data["code"]), str(data.get("message", "")))
    elif event.event == "disconnected":
        handlers.on_disconnected()
    else:
        raise ValueError(f"unknown fixture event {event.event!r}")


async def replay(handlers: PortHandlers, events: list[FixtureEvent], clock: FakeClock) -> None:
    """Deliver events at their recorded offsets on virtual time."""
    elapsed = 0.0
    for event in events:
        if event.t > elapsed:
            await clock.advance(event.t - elapsed)
            elapsed = event.t
        deliver(handlers, event)
        await clock.settle()


class Recorder:
    """Writes live gateway callbacks in fixture format, replacing account codes with placeholders."""

    def __init__(self, sink: IO[str], start: datetime) -> None:
        self._sink = sink
        self._start = start
        self._accounts: dict[str, str] = {}

    def _account(self, code: str) -> str:
        if not code:
            return ""
        return self._accounts.setdefault(code, f"DU{len(self._accounts) + 1:07d}")

    def _write(self, now: datetime, event: str, **payload: Any) -> None:
        line = {"t": round((now - self._start).total_seconds(), 3), "event": event, **payload}
        self._sink.write(json.dumps(line, default=str) + "\n")
        self._sink.flush()

    def trade(self, now: datetime, event: str, trade: Trade) -> None:
        order = {k: getattr(trade.order, k) for k in _ORDER_FIELDS}
        order["account"] = self._account(order["account"])
        self._write(
            now,
            event,
            order=order,
            contract={k: getattr(trade.contract, k) for k in _CONTRACT_FIELDS},
            status={k: getattr(trade.orderStatus, k) for k in _STATUS_FIELDS},
        )

    def fill(self, now: datetime, fill: Fill) -> None:
        execution = {f.name: getattr(fill.execution, f.name) for f in fields(Execution)}
        execution["acctNumber"] = self._account(execution["acctNumber"])
        execution["time"] = fill.execution.time.isoformat()
        self._write(
            now, "execDetails", execution=execution, contract={k: getattr(fill.contract, k) for k in _CONTRACT_FIELDS}
        )

    def commission(self, now: datetime, report: CommissionReport) -> None:
        self._write(
            now,
            "commissionReport",
            report={"execId": report.execId, "commission": report.commission, "currency": report.currency},
        )

    def error(self, now: datetime, req_id: int, code: int, message: str) -> None:
        self._write(now, "error", reqId=req_id, code=code, message=message)


def iter_fixtures(directory: str | Path) -> Iterator[Path]:
    yield from sorted(Path(directory).glob("*.jsonl"))
