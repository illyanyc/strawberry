"""Opening-range breakout on 15-minute bars: the Phase 2 rule-based baseline (section 07).

Rules (deterministic, long only):

* The opening range is the high and low of the first ``opening_bars`` bars of the regular session.
* After the range is complete, the first bar that closes above the range high (while flat, with
  fewer than ``max_trades_per_day`` entries today) opens a long: stop = range low minus
  ``stop_buffer_bps``; take-profit = entry + ``target_r`` x (entry - stop); size = fixed fractional
  ``risk_per_trade`` of the equity basis, capped by the strategy's cash.
* Exit: the broker-side bracket (stop or target) or, failing that, a close at the first bar ending
  ``exit_minutes_before_close`` or less before the regular close. Nothing is held overnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from tbcore.strategy.api import Bar, Fill, Intent, StrategyContext, new_ulid
from tbcore.strategy.sizing import fixed_fractional_qty

CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ORBParams:
    opening_bars: int = 1
    risk_per_trade: Decimal = Decimal("0.005")
    target_r: Decimal = Decimal(2)
    stop_buffer_bps: Decimal = Decimal(5)
    exit_minutes_before_close: int = 15
    max_trades_per_day: int = 1

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> ORBParams:
        return cls(
            opening_bars=int(str(d.get("opening_bars", 1))),
            risk_per_trade=Decimal(str(d.get("risk_per_trade", "0.005"))),
            target_r=Decimal(str(d.get("target_r", "2"))),
            stop_buffer_bps=Decimal(str(d.get("stop_buffer_bps", "5"))),
            exit_minutes_before_close=int(str(d.get("exit_minutes_before_close", 15))),
            max_trades_per_day=int(str(d.get("max_trades_per_day", 1))),
        )


@dataclass(slots=True)
class _Day:
    date: str
    bars: int = 0
    high: Decimal = Decimal(0)
    low: Decimal = Decimal(0)
    entries: int = 0
    exit_sent: bool = False


class OpeningRangeBreakout:
    """ORB baseline. State is per instrument and per session date."""

    def __init__(self, strategy_id: str, params: ORBParams) -> None:
        self.strategy_id = strategy_id
        self.params = params
        self._days: dict[str, _Day] = {}

    def on_start(self, ctx: StrategyContext) -> None:
        self._days.clear()

    def _day(self, ctx: StrategyContext, instrument_id: str) -> _Day | None:
        s = ctx.session()
        if s is None:
            return None
        key = s.date.isoformat()
        d = self._days.get(instrument_id)
        if d is None or d.date != key:
            d = _Day(date=key)
            self._days[instrument_id] = d
        return d

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        if bar.available_at > ctx.now:  # DF-002: never act on a bar from the future
            return []
        d = self._day(ctx, bar.instrument_id)
        s = ctx.session()
        if d is None or s is None:
            return []
        bar_open = datetime.fromtimestamp(bar.ts_open / 1e9, tz=UTC)
        if bar_open < s.open or bar_open >= s.close:
            return []
        held = ctx.position(bar.instrument_id)
        close_at = int(s.close.timestamp()) * 1_000_000_000 - self.params.exit_minutes_before_close * 60 * 1_000_000_000
        if bar.ts_close >= close_at:
            if held > 0 and not d.exit_sent:
                d.exit_sent = True
                return [self._intent(bar, "sell", held, "close", "session exit before the close")]
            return []
        if d.bars < self.params.opening_bars:
            d.high = bar.high if d.bars == 0 else max(d.high, bar.high)
            d.low = bar.low if d.bars == 0 else min(d.low, bar.low)
            d.bars += 1
            return []
        if held != 0 or d.entries >= self.params.max_trades_per_day or bar.close <= d.high:
            return []
        entry = bar.close
        stop = (d.low * (1 - self.params.stop_buffer_bps / Decimal(10000))).quantize(CENT, rounding=ROUND_FLOOR)
        if stop >= entry:
            return []
        target = (entry + self.params.target_r * (entry - stop)).quantize(CENT, rounding=ROUND_CEILING)
        qty = fixed_fractional_qty(ctx.equity_basis(), self.params.risk_per_trade, entry, stop, ctx.cash())
        if qty <= 0:
            return []
        d.entries += 1
        return [self._intent(bar, "buy", qty, "open", f"breakout above opening range high {d.high}", stop, target)]

    def _intent(
        self,
        bar: Bar,
        side: str,
        qty: Decimal,
        kind: str,
        reason: str,
        stop: Decimal | None = None,
        target: Decimal | None = None,
    ) -> Intent:
        ts_ms = bar.available_at // 1_000_000
        seed = f"{self.strategy_id}|{bar.instrument_id}|{bar.available_at}|{kind}"
        return Intent(
            intent_id=new_ulid(ts_ms, seed),
            instrument_id=bar.instrument_id,
            side=side,
            qty=qty,
            kind=kind,
            reason=reason,
            reference_price=bar.close,
            stop_loss=stop,
            take_profit=target,
            protective_tif="day",
            correlation_id=new_ulid(ts_ms, seed + "|corr"),
        )

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return []

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return []
