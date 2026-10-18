"""Per-strategy live state built from bus events (section 13 Phase 5, ADR-0045).

The monitor keeps its own view of each monitored strategy from the order path (it never queries the
ledger): FIFO lots and realized P&L from the strategy's executions (attribution prefix), open lots marked
at the latest final bar, session-close equity, every fill joined to its intent (decision time and
reference price, MN-033) and OMS order (submit time, order type), and the model's signals and scored
feature vectors (MN-038). Money stays in ``Decimal``; statistics are computed in floats downstream.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal

from tb.common.v1 import common_pb2
from tb.features.v1 import features_pb2
from tb.marketdata.v1 import marketdata_pb2
from tb.orders.v1 import orders_pb2
from tb.signals.v1 import signals_pb2
from tb_monitor.analytics import FillObs

ZERO = Decimal(0)
MAX_SAMPLES = 50_000


def dec(d: common_pb2.Decimal) -> Decimal:
    return Decimal(d.value) if d.value else ZERO


def attribution(client_order_id: str) -> tuple[str, str]:
    """(strategy short ID, intent ULID) from ``tb1-<short>-<intentULID>-<attempt>``; ('', '') for manual."""
    p = client_order_id.split("-")
    return (p[1], p[2]) if len(p) >= 4 and p[0] == "tb1" else ("", "")


@dataclass
class Trade:
    exit_time: int
    ret_bps: float
    pnl: Decimal


@dataclass
class StrategyBook:
    strategy_id: str
    short_id: str
    allocation: Decimal
    lots: dict[str, deque[tuple[Decimal, Decimal]]] = field(default_factory=lambda: defaultdict(deque))
    realized: Decimal = ZERO
    fees: Decimal = ZERO
    marks: dict[str, Decimal] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    session_equity: dict[str, Decimal] = field(default_factory=dict)
    intents: dict[str, tuple[int, Decimal, int]] = field(default_factory=dict)
    orders: dict[str, orders_pb2.Order] = field(default_factory=dict)
    fills: list[FillObs] = field(default_factory=list)
    signals: deque[signals_pb2.Signal] = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    features: deque[features_pb2.FeatureVector] = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))
    seen_exec: set[str] = field(default_factory=set)
    first_seen: int = 0
    peak_equity: Decimal = ZERO
    max_drawdown_pct: float = 0.0

    # ---- events

    def on_intent(self, it: orders_pb2.OrderIntent) -> None:
        side = 1 if it.side == common_pb2.SIDE_BUY else -1
        self.intents[it.intent_id] = (it.decision_time or it.created_at, dec(it.reference_price), side)

    def on_order(self, o: orders_pb2.Order) -> None:
        self.orders[o.order_id] = o

    def on_execution(self, x: orders_pb2.ExecutionReport) -> None:
        key = f"{x.broker_account_id}|{x.broker_exec_id}"
        if key in self.seen_exec:  # at-least-once delivery (OE-006)
            return
        self.seen_exec.add(key)
        self.first_seen = self.first_seen or x.event_time
        qty, px, fee = dec(x.qty), dec(x.price), dec(x.fees)
        side = 1 if x.side == common_pb2.SIDE_BUY else -1
        self.fees += fee
        self.marks[x.instrument_id] = px
        lots = self.lots[x.instrument_id]
        left = qty
        while left > 0 and lots and (lots[0][0] > 0) != (side > 0):
            lq, lp = lots[0]
            take = min(left, abs(lq))
            sign = Decimal(1) if lq > 0 else Decimal(-1)
            pnl = (px - lp) * take * sign
            self.realized += pnl
            self.trades.append(Trade(x.event_time, float((px - lp) / lp * 10_000 * sign) if lp else 0.0, pnl))
            left -= take
            if take >= abs(lq):
                lots.popleft()
            else:
                lots[0] = (lq - take * sign, lp)
        if left > 0:
            lots.append((left * side, px))
        _, intent = attribution(x.client_order_id)
        dt, ref, _ = self.intents.get(intent, (0, ZERO, side))
        submitted = next((o.submitted_at for o in self.orders.values() if o.client_order_id == x.client_order_id), 0)
        self.fills.append(
            FillObs(x.event_time, side, float(qty), float(px), float(fee), float(ref), int(dt), int(submitted))
        )
        self._track_drawdown()

    def on_bar(self, b: marketdata_pb2.Bar) -> None:
        if self.lots.get(b.instrument_id):
            self.marks[b.instrument_id] = dec(b.close)
            self._track_drawdown()

    def on_signal(self, s: signals_pb2.Signal) -> None:
        self.signals.append(s)

    def on_feature(self, f: features_pb2.FeatureVector) -> None:
        self.features.append(f)

    # ---- valuation

    def unrealized(self) -> Decimal:
        total = ZERO
        for inst, lots in self.lots.items():
            m = self.marks.get(inst)
            for q, p in lots:
                total += (m - p) * q if m is not None else ZERO
        return total

    def equity(self) -> Decimal:
        return self.allocation + self.realized - self.fees + self.unrealized()

    def _track_drawdown(self) -> None:
        eq = self.equity()
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity > 0:
            self.max_drawdown_pct = max(self.max_drawdown_pct, float((1 - eq / self.peak_equity) * 100))

    def close_session(self, date: str) -> None:
        self.peak_equity = max(self.peak_equity, self.allocation)
        self._track_drawdown()
        self.session_equity[date] = self.equity()

    def current_drawdown_pct(self) -> float:
        peak = max(self.peak_equity, self.allocation)
        return float((1 - self.equity() / peak) * 100) if peak > 0 else 0.0

    def session_returns_bps(self) -> list[float]:
        out, prev = [], self.allocation
        for _, eq in sorted(self.session_equity.items()):
            out.append(float((eq / prev - 1) * 10_000) if prev else 0.0)
            prev = eq
        return out

    def trade_returns_bps(self) -> list[float]:
        return [t.ret_bps for t in self.trades if math.isfinite(t.ret_bps)]

    def limit_orders(self) -> tuple[int, int]:
        lim = [
            o
            for o in self.orders.values()
            if o.order_type == orders_pb2.ORDER_TYPE_LIMIT and o.role == orders_pb2.ORDER_ROLE_ENTRY
        ]
        final = {orders_pb2.ORDER_STATE_FILLED, orders_pb2.ORDER_STATE_CANCELED, orders_pb2.ORDER_STATE_EXPIRED}
        done = [o for o in lim if o.state in final]
        return len(done), sum(1 for o in done if o.state == orders_pb2.ORDER_STATE_FILLED)
