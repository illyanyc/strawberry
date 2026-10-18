"""Strategy API (section 07, ADR-0026): the same code runs in backtest, shadow, paper, and live.

A strategy receives final bars in timestamp order and returns intents. Its context exposes only
the decision time (the bar's ``available_at``), its own virtual portfolio, its parameters, and
the session calendar; never data with ``available_at`` after the decision time, other owners'
positions, or manual holdings (SM-014, DF-002). Money and quantities are ``Decimal``.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from tbcore.data.calendar import Calendar, Session

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ts_ms: int | None = None, seed: str = "") -> str:
    """Return a ULID (26 Crockford base32 characters); intent IDs are ULIDs (VP-002).

    With ``seed`` the random part is derived from it, so a replayed decision (runtime restart,
    backtest replay) yields the same intent ID and execution deduplicates it (OE-006).
    """
    ms = int(time.time() * 1000) if ts_ms is None else ts_ms
    rnd = hashlib.sha256(seed.encode()).digest()[:10] if seed else os.urandom(10)
    value = (ms << 80) | int.from_bytes(rnd, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


@dataclass(frozen=True, slots=True)
class Bar:
    """A final bar. Times are UTC nanoseconds; ``available_at`` is the decision time (MD-002)."""

    instrument_id: str
    timeframe: str
    ts_open: int
    ts_close: int
    available_at: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(slots=True)
class OwnPosition:
    """The strategy's own position in one instrument (from its virtual ledger)."""

    instrument_id: str
    qty: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Fill:
    """An execution of one of the strategy's own orders."""

    instrument_id: str
    side: str
    qty: Decimal
    price: Decimal
    event_time: int


@dataclass(frozen=True, slots=True)
class Intent:
    """What the strategy wants. ``side`` is buy | sell | sell_short; ``kind`` is open | close.

    Opens MUST carry ``stop_loss`` (N5); ``take_profit`` is optional. Prices are guidance for
    the OMS, which sends a collared marketable limit (OE-013) and caps the size (RM-010).
    """

    intent_id: str
    instrument_id: str
    side: str
    qty: Decimal
    kind: str
    reason: str
    reference_price: Decimal
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    protective_tif: str = "day"
    correlation_id: str = ""


@dataclass(slots=True)
class StrategyContext:
    """What a strategy may see (SM-014)."""

    strategy_id: str
    now: int
    calendar: Calendar
    allocation: Decimal
    positions: dict[str, OwnPosition] = field(default_factory=dict)
    params: dict[str, object] = field(default_factory=dict)

    def position(self, instrument_id: str) -> Decimal:
        p = self.positions.get(instrument_id)
        return p.qty if p else Decimal(0)

    def realized(self) -> Decimal:
        return sum((p.realized_pnl for p in self.positions.values()), Decimal(0))

    def invested(self) -> Decimal:
        return sum((p.cost_basis for p in self.positions.values()), Decimal(0))

    def equity_basis(self) -> Decimal:
        """Allocation plus realized P&L: the sizing basis (unrealized excluded on purpose)."""
        return self.allocation + self.realized()

    def cash(self) -> Decimal:
        return self.equity_basis() - self.invested()

    def session(self) -> Session | None:
        s, _ = self.calendar.session_at(datetime.fromtimestamp(self.now / 1e9, tz=UTC))
        return s


class Strategy(Protocol):
    """The strategy interface (section 07). Implementations are pure and deterministic."""

    strategy_id: str

    def on_start(self, ctx: StrategyContext) -> None: ...

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]: ...

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]: ...

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]: ...
