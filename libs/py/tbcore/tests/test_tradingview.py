"""TradingView webhook strategy (SM-020, ADR-0051)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tbcore.data.calendar import xnys
from tbcore.strategy.api import Bar, OwnPosition, StrategyContext
from tbcore.strategy.tradingview import ExternalSignal, TradingViewParams, TradingViewStrategy

NS = 1_000_000_000


def bar(i: int, close: str) -> Bar:
    s = xnys().session(date(2026, 10, 14))
    assert s is not None
    start = s.open + timedelta(minutes=15 * i)
    end = start + timedelta(minutes=15)
    c = Decimal(close)
    return Bar(
        "I1",
        "15m",
        int(start.timestamp()) * NS,
        int(end.timestamp()) * NS,
        int(end.timestamp() + 60) * NS,
        c,
        c,
        c,
        c,
        Decimal(1000),
    )


def ctx(now: int) -> StrategyContext:
    return StrategyContext(strategy_id="tv-x", now=now, calendar=xnys(), allocation=Decimal(10000))


def test_sm020_short_open_carries_a_stop_above_and_a_target_below() -> None:
    s = TradingViewStrategy(
        "tv-x", TradingViewParams.from_dict({"allow_short": True, "stop_bps": "200", "take_profit_r": "2"}), ["I1"]
    )
    b = bar(3, "50")
    c = ctx(b.available_at)
    s.on_bar(c, b)
    out = s.on_signal(c, ExternalSignal("S1", "I1", "short", b.available_at))
    assert len(out) == 1
    it = out[0]
    assert it.side == "sell_short" and it.kind == "open"
    assert it.stop_loss == Decimal("51.00") and it.take_profit == Decimal("48.00")
    # 0.5 % of 10,000 risked over 1.00 per share.
    assert it.qty == Decimal(50)


def test_sm020_intraday_strategy_flattens_before_the_close_and_refuses_late_opens() -> None:
    s = TradingViewStrategy("tv-x", TradingViewParams(), ["I1"])
    late = bar(25, "100")  # 15:45-16:00 bar: inside the 15-minute exit window
    c = ctx(late.available_at)
    c.positions["I1"] = OwnPosition("I1", qty=Decimal(5))
    out = s.on_bar(c, late)
    assert len(out) == 1 and out[0].side == "sell" and out[0].qty == Decimal(5) and out[0].kind == "close"
    assert s.on_bar(c, late) == []  # once
    c.positions["I1"] = OwnPosition("I1")
    assert s.on_signal(c, ExternalSignal("S2", "I1", "long", late.available_at)) == []
    assert s.dropped[-1] == ("S2", "too close to the session close")


def test_sm020_signal_from_the_future_or_without_fresh_bar_is_dropped() -> None:
    s = TradingViewStrategy("tv-x", TradingViewParams(max_bar_age_seconds=600), ["I1"])
    b = bar(3, "100")
    c = ctx(b.available_at)
    s.on_bar(c, b)
    assert s.on_signal(c, ExternalSignal("S1", "I1", "long", b.available_at + NS)) == []
    c.now = b.available_at + 700 * NS
    assert s.on_signal(c, ExternalSignal("S2", "I1", "long", c.now)) == []
    assert [r for _, r in s.dropped] == ["stale signal", "no fresh platform bar"]


@pytest.mark.parametrize("bad", [{"stop_bps": "0"}, {"stop_bps": "9000"}, {"risk_per_trade": "0.2"}])
def test_sm020_params_are_bounded(bad: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        TradingViewParams.from_dict(bad)
