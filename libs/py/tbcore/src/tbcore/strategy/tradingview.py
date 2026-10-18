"""TradingView webhook strategy (runtime class ``tradingview``, section 14, ADR-0052, SM-020).

A ``tv-<name>`` strategy turns verified TradingView alerts (``Signal`` messages the gateway
publishes on ``sig.tv-<name>.<instrument>``) into intents for its own virtual portfolio. It is an
ordinary strategy: the intents carry its attribution ID and pass the same pre-trade gate (RM-001),
with its own allocation, limits, and ledger. TradingView never places orders.

Rules (SM-020):

* Only instruments in the registry's ``runtime.instruments`` are traded; others are dropped.
* A signal is acted on only if it is at most ``max_signal_age_seconds`` old at the decision time
  and the newest final bar of the instrument is at most ``max_bar_age_seconds`` old (fail closed:
  no fresh platform price, no order). Sizing and stops use the platform's own bar, never a price
  from the alert.
* ``long`` opens a long when flat (no pyramiding) and closes a short; ``short`` opens a short when
  flat and shorts are allowed, and closes a long; ``flat`` closes the strategy's own position. A
  signal never reverses a position in one step.
* Opens carry a protective stop ``stop_bps`` away from the reference price (N5) and are sized by
  fixed-fractional risk on the strategy's own equity basis (RM-010). Intraday strategies close
  ``exit_minutes_before_close`` before the session close.
* Intent IDs derive from the signal ID, so a redelivered signal yields the same intent and
  execution deduplicates it (OE-006).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from tbcore.strategy.api import Bar, Fill, Intent, StrategyContext, new_ulid
from tbcore.strategy.sizing import fixed_fractional_qty

CENT = Decimal("0.01")
NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class ExternalSignal:
    """A webhook signal as the strategy sees it (from ``tb.signals.v1.Signal``)."""

    signal_id: str
    instrument_id: str
    direction: str  # long | short | flat
    as_of: int
    correlation_id: str = ""


@dataclass(frozen=True)
class TradingViewParams:
    risk_per_trade: Decimal = Decimal("0.005")
    stop_bps: Decimal = Decimal(150)
    take_profit_r: Decimal | None = None
    allow_short: bool = False
    max_signal_age_seconds: int = 60
    max_bar_age_seconds: int = 1800
    swing: bool = False
    exit_minutes_before_close: int = 15

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TradingViewParams:
        tp = d.get("take_profit_r")
        p = cls(
            risk_per_trade=Decimal(str(d.get("risk_per_trade", "0.005"))),
            stop_bps=Decimal(str(d.get("stop_bps", "150"))),
            take_profit_r=Decimal(str(tp)) if tp not in (None, "") else None,
            allow_short=bool(d.get("allow_short", False)),
            max_signal_age_seconds=int(d.get("max_signal_age_seconds", 60)),
            max_bar_age_seconds=int(d.get("max_bar_age_seconds", 1800)),
            swing=bool(d.get("swing", False)),
            exit_minutes_before_close=int(d.get("exit_minutes_before_close", 15)),
        )
        if not (Decimal(0) < p.stop_bps < Decimal(5000)):
            raise ValueError("stop_bps must be in (0, 5000)")
        if not (Decimal(0) < p.risk_per_trade <= Decimal("0.05")):
            raise ValueError("risk_per_trade must be in (0, 0.05]")
        return p


class TradingViewStrategy:
    def __init__(self, strategy_id: str, params: TradingViewParams, instruments: list[str]) -> None:
        self.strategy_id = strategy_id
        self.p = params
        self.instruments = set(instruments)
        self._last: dict[str, Bar] = {}
        self._exit_sent: set[str] = set()
        self.dropped: list[tuple[str, str]] = []  # (signal_id, reason), for logs and tests

    def on_start(self, ctx: StrategyContext) -> None:
        self._exit_sent.clear()

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        if bar.available_at > ctx.now:  # DF-002
            return []
        self._last[bar.instrument_id] = bar
        held = ctx.position(bar.instrument_id)
        if held == 0:
            self._exit_sent.discard(bar.instrument_id)
            return []
        if self.p.swing or bar.instrument_id in self._exit_sent or not self._late(ctx, bar):
            return []
        self._exit_sent.add(bar.instrument_id)
        side = "sell" if held > 0 else "buy"
        return [
            self._intent(
                bar, f"close-{bar.ts_open}", side, abs(held), "close", "tradingview flat before close", None, None
            )
        ]

    def _late(self, ctx: StrategyContext, bar: Bar) -> bool:
        s = ctx.session()
        if s is None:
            return True
        close_at = int(s.close.timestamp()) * NS - self.p.exit_minutes_before_close * 60 * NS
        return bar.ts_close >= close_at

    def on_signal(self, ctx: StrategyContext, sig: ExternalSignal) -> list[Intent]:
        def drop(reason: str) -> list[Intent]:
            self.dropped.append((sig.signal_id, reason))
            return []

        if sig.instrument_id not in self.instruments:
            return drop("instrument not in runtime.instruments")
        if ctx.now - sig.as_of > self.p.max_signal_age_seconds * NS or sig.as_of > ctx.now:
            return drop("stale signal")
        bar = self._last.get(sig.instrument_id)
        if bar is None or bar.available_at > ctx.now or ctx.now - bar.available_at > self.p.max_bar_age_seconds * NS:
            return drop("no fresh platform bar")
        held = ctx.position(sig.instrument_id)
        if sig.direction == "flat":
            if held == 0:
                return drop("already flat")
            return [self._close(bar, sig, held, "tradingview exit")]
        if sig.direction == "long":
            if held < 0:
                return [self._close(bar, sig, held, "tradingview buy closes short")]
            if held > 0:
                return drop("already long")
            return self._open(ctx, bar, sig, "buy")
        if sig.direction == "short":
            if held > 0:
                return [self._close(bar, sig, held, "tradingview sell closes long")]
            if held < 0:
                return drop("already short")
            if not self.p.allow_short:
                return drop("shorts not allowed")
            return self._open(ctx, bar, sig, "sell_short")
        return drop(f"unknown direction {sig.direction!r}")

    def _open(self, ctx: StrategyContext, bar: Bar, sig: ExternalSignal, side: str) -> list[Intent]:
        if not self.p.swing and self._late(ctx, bar):
            self.dropped.append((sig.signal_id, "too close to the session close"))
            return []
        entry = bar.close
        dist = entry * self.p.stop_bps / Decimal(10_000)
        if side == "buy":
            stop = (entry - dist).quantize(CENT, rounding=ROUND_FLOOR)
            target = (
                (entry + dist * self.p.take_profit_r).quantize(CENT, rounding=ROUND_CEILING)
                if self.p.take_profit_r
                else None
            )
        else:
            stop = (entry + dist).quantize(CENT, rounding=ROUND_CEILING)
            target = (
                (entry - dist * self.p.take_profit_r).quantize(CENT, rounding=ROUND_FLOOR)
                if self.p.take_profit_r
                else None
            )
        if stop <= 0 or stop == entry:
            self.dropped.append((sig.signal_id, "invalid stop"))
            return []
        lo, hi = (stop, entry) if side == "buy" else (entry, stop)
        qty = fixed_fractional_qty(ctx.equity_basis(), self.p.risk_per_trade, hi, lo, ctx.cash())
        if qty <= 0:
            self.dropped.append((sig.signal_id, "size rounds to zero"))
            return []
        self._exit_sent.discard(sig.instrument_id)
        return [
            self._intent(
                bar,
                sig.signal_id,
                side,
                qty,
                "open",
                f"tradingview {side} (signal {sig.signal_id})",
                stop,
                target,
                sig.correlation_id,
            )
        ]

    def _close(self, bar: Bar, sig: ExternalSignal, held: Decimal, reason: str) -> Intent:
        side = "sell" if held > 0 else "buy"
        return self._intent(
            bar,
            sig.signal_id,
            side,
            abs(held),
            "close",
            f"{reason} (signal {sig.signal_id})",
            None,
            None,
            sig.correlation_id,
        )

    def _intent(
        self,
        bar: Bar,
        key: str,
        side: str,
        qty: Decimal,
        kind: str,
        reason: str,
        stop: Decimal | None,
        target: Decimal | None,
        correlation_id: str = "",
    ) -> Intent:
        ts_ms = bar.available_at // 1_000_000
        seed = f"{self.strategy_id}|{bar.instrument_id}|{key}|{kind}"
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
            protective_tif="gtc" if self.p.swing else "day",
            correlation_id=correlation_id or new_ulid(ts_ms, seed + "|corr"),
        )

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return []

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return []
