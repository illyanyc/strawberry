"""Strategy API and ORB baseline (section 07; SM-011, SM-014, DF-002, N5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tbcore.data.calendar import xnys
from tbcore.strategy import (
    Bar,
    Intent,
    OpeningRangeBreakout,
    ORBParams,
    OwnPosition,
    StrategyContext,
    fixed_fractional_qty,
    new_ulid,
)

D = Decimal
NS = 1_000_000_000


def bars(day: date, closes: list[tuple[str, str, str, str]]) -> list[Bar]:
    s = xnys().session(day)
    assert s is not None
    out = []
    for i, (o, h, lo, c) in enumerate(closes):
        start = s.open + timedelta(minutes=15 * i)
        end = start + timedelta(minutes=15)
        out.append(
            Bar(
                "I1",
                "15m",
                int(start.timestamp()) * NS,
                int(end.timestamp()) * NS,
                int((end + timedelta(minutes=1)).timestamp()) * NS,
                D(o),
                D(h),
                D(lo),
                D(c),
                D(1000),
            )
        )
    return out


def run(strategy: OpeningRangeBreakout, series: list[Bar], ctx: StrategyContext) -> list[tuple[int, Intent]]:
    out: list[tuple[int, Intent]] = []
    for i, b in enumerate(series):
        ctx.now = b.available_at
        for it in strategy.on_bar(ctx, b):
            out.append((i, it))
            if it.kind == "open":
                ctx.positions["I1"] = OwnPosition("I1", it.qty, it.qty * it.reference_price)
            else:
                ctx.positions["I1"] = OwnPosition("I1", D(0))
    return out


def ctx() -> StrategyContext:
    return StrategyContext(strategy_id="orb", now=0, calendar=xnys(), allocation=D(10000))


def session_series() -> list[Bar]:
    rows = [("100", "101", "99", "100.5"), ("100.5", "100.8", "100", "100.6"), ("100.6", "101.5", "100.5", "101.4")]
    rows += [("101.4", "101.6", "101.2", "101.5")] * 23
    return bars(date(2026, 10, 14), rows)


def test_orb_breakout_entry_stop_target_and_session_exit() -> None:
    s = OpeningRangeBreakout("orb", ORBParams())
    out = run(s, session_series(), ctx())
    assert [(i, it.kind, it.side) for i, it in out] == [(2, "open", "buy"), (25 - 1, "close", "sell")]
    entry = out[0][1]
    assert entry.stop_loss == D("98.95")  # opening range low 99 minus 5 bps, floored to the cent
    assert entry.take_profit == D("106.30")  # 101.4 + 2 x (101.4 - 98.95)
    assert entry.qty == fixed_fractional_qty(D(10000), D("0.005"), D("101.4"), D("98.95"), D(10000)) == D(20)
    assert entry.stop_loss < entry.reference_price < entry.take_profit  # N5: every open carries a stop


def test_orb_is_deterministic_including_intent_ids() -> None:
    a = run(OpeningRangeBreakout("orb", ORBParams()), session_series(), ctx())
    b = run(OpeningRangeBreakout("orb", ORBParams()), session_series(), ctx())
    assert [(i, x.intent_id, x.qty, x.stop_loss) for i, x in a] == [(i, x.intent_id, x.qty, x.stop_loss) for i, x in b]


def test_orb_ignores_bars_from_the_future_df002() -> None:
    s = OpeningRangeBreakout("orb", ORBParams())
    c = ctx()
    series = session_series()
    c.now = series[0].available_at - 1
    assert s.on_bar(c, series[0]) == []


def test_orb_one_trade_per_day_and_no_entry_when_flat_range() -> None:
    s = OpeningRangeBreakout("orb", ORBParams())
    c = ctx()
    out = run(s, session_series(), c)
    assert sum(1 for _, it in out if it.kind == "open") == 1
    flat = bars(date(2026, 10, 15), [("100", "101", "99", "100")] + [("100", "100.9", "99.5", "100.9")] * 25)
    assert run(OpeningRangeBreakout("orb", ORBParams()), flat, ctx()) == []


def test_new_ulid_shape_and_seed() -> None:
    a, b = new_ulid(1_700_000_000_000, "x"), new_ulid(1_700_000_000_000, "x")
    assert a == b and len(a) == 26 and new_ulid(1_700_000_000_000, "y") != a
    assert datetime.fromtimestamp(1_700_000_000, tz=UTC).year == 2023
