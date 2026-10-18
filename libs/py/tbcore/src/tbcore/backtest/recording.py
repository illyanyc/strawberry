"""Bus recordings: length-delimited ``tb.backtest.v1.RecordedMessage`` files (ADR-0031, BT-070).

A recording holds what the bus carried during a paper or live period: final bars (``md.bar.*``),
intents (``ord.intent``), risk decisions (``ord.decision``), OMS order states (``ord.order.<acct>``),
executions (``exec.<acct>``), and ledger updates (``ledger.update.<acct>``). The event-driven tier
replays its bars; the parity harness compares the rest with the replay.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import polars as pl

from tb.backtest.v1 import simcore_pb2
from tb.ledger.v1 import ledger_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.orders.v1 import orders_pb2
from tbcore.backtest.simcore import _read_varint, _varint
from tbcore.strategy.host import TIMEFRAMES


def write_recording(path: str | Path, messages: Iterable[simcore_pb2.RecordedMessage]) -> int:
    n = 0
    with open(path, "wb") as f:
        for m in messages:
            b = m.SerializeToString()
            f.write(_varint(len(b)) + b)
            n += 1
    return n


def iter_recording(f: BinaryIO) -> Iterator[simcore_pb2.RecordedMessage]:
    while True:
        n = _read_varint(f)
        if n is None:
            return
        m = simcore_pb2.RecordedMessage()
        m.ParseFromString(f.read(n))
        yield m


@dataclass
class Recording:
    """A parsed recording, deduplicated on message ID (JetStream redeliveries)."""

    bars: list[marketdata_pb2.Bar] = field(default_factory=list)
    intents: list[orders_pb2.OrderIntent] = field(default_factory=list)
    decisions: list[orders_pb2.RiskDecision] = field(default_factory=list)
    orders: list[orders_pb2.Order] = field(default_factory=list)
    executions: list[orders_pb2.ExecutionReport] = field(default_factory=list)
    ledger_updates: list[ledger_pb2.LedgerUpdate] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        with open(path, "rb") as f:
            return cls.from_messages(iter_recording(f))

    @classmethod
    def from_messages(cls, messages: Iterable[simcore_pb2.RecordedMessage]) -> Recording:
        r = cls()
        seen: set[str] = set()
        for m in messages:
            key = m.subject + "|" + (m.msg_id or str(m.stream_sequence))
            if m.msg_id and key in seen:
                continue
            seen.add(key)
            s = m.subject
            if s.startswith("md.bar.") and not s.startswith(("md.bar.partial.", "md.bar.correction.")):
                b = marketdata_pb2.Bar()
                b.ParseFromString(m.data)
                if b.is_final:
                    r.bars.append(b)
            elif s == "ord.intent":
                it = orders_pb2.OrderIntent()
                it.ParseFromString(m.data)
                r.intents.append(it)
            elif s == "ord.decision":
                d = orders_pb2.RiskDecision()
                d.ParseFromString(m.data)
                r.decisions.append(d)
            elif s.startswith("ord.order."):
                o = orders_pb2.Order()
                o.ParseFromString(m.data)
                r.orders.append(o)
            elif s.startswith("exec."):
                x = orders_pb2.ExecutionReport()
                x.ParseFromString(m.data)
                r.executions.append(x)
            elif s.startswith("ledger.update."):
                u = ledger_pb2.LedgerUpdate()
                u.ParseFromString(m.data)
                r.ledger_updates.append(u)
        r.executions = _dedupe(r.executions, lambda x: x.broker_account_id + "|" + x.broker_exec_id)
        r.executions.sort(key=lambda x: (x.event_time, x.sequence))
        return r

    def bars_frame(self, timeframe: str | None = None) -> pl.DataFrame:
        """Final bars as a frame in the lake's bar schema (int64 ns times, float prices)."""
        rows = [b for b in self.bars if timeframe is None or TIMEFRAMES.get(b.timeframe) == timeframe]
        return pl.DataFrame(
            {
                "instrument_id": [b.instrument_id for b in rows],
                "timeframe": [TIMEFRAMES.get(b.timeframe, "") for b in rows],
                "ts_open": [b.ts_open for b in rows],
                "ts_close": [b.ts_close for b in rows],
                "available_at": [b.available_at for b in rows],
                "open": [float(b.open.value) for b in rows],
                "high": [float(b.high.value) for b in rows],
                "low": [float(b.low.value) for b in rows],
                "close": [float(b.close.value) for b in rows],
                "volume": [float(b.volume.value or 0) for b in rows],
            },
            schema={
                "instrument_id": pl.Utf8,
                "timeframe": pl.Utf8,
                "ts_open": pl.Int64,
                "ts_close": pl.Int64,
                "available_at": pl.Int64,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            },
        ).unique(subset=["instrument_id", "timeframe", "ts_open"], keep="last", maintain_order=True)

    def order_roles(self) -> dict[str, tuple[str, str]]:
        """Broker order ID → (client order ID, OMS role name) from ``ord.order.*`` states."""
        out: dict[str, tuple[str, str]] = {}
        for o in self.orders:
            if o.broker_order_id:
                out[o.broker_order_id] = (o.client_order_id, orders_pb2.OrderRole.Name(o.role))
        return out


def _dedupe(xs: list[orders_pb2.ExecutionReport], key: object) -> list[orders_pb2.ExecutionReport]:
    seen: set[str] = set()
    out = []
    for x in xs:
        k = key(x)  # type: ignore[operator]
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out
