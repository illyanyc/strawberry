"""Synthetic market data for backtest tests (no lake needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import polars as pl

from tbcore.backtest.config import AccountSimConfig, BacktestConfig, ManualPosition, StrategySpec, UniverseRef
from tbcore.data.calendar import xnys

NS = 1_000_000_000
ORB_PARAMS = {
    "opening_bars": 1,
    "risk_per_trade": "0.005",
    "target_r": "2",
    "stop_buffer_bps": "5",
    "exit_minutes_before_close": 15,
    "max_trades_per_day": 1,
}


def iid(sym: str) -> str:
    """The paper harness's synthetic instrument ID (tests/paper InstrumentID)."""
    return ("01J" + sym).ljust(26, "0")


def gate_path(pattern: int, i: int, scale: float) -> tuple[float, float, float, float]:
    """OHLC of bar i of a session (tests/paper ``path``): 0 target, 1 stop, 2 hold, 3 no breakout."""

    def f(x: float) -> float:
        return round(x * scale, 2)

    if i == 0:
        return f(100), f(101), f(99), f(100.2)
    if i == 1:
        op, cl = 100.2, 100.6
    elif pattern == 3:
        op, cl = 100.4, 100.4 + 0.3 * (i % 3) - 0.3
    elif i == 2:
        op, cl = 100.6, 101.3
    elif pattern == 0:
        op, cl = 101.3 + 0.6 * (i - 3), 101.3 + 0.6 * (i - 2)
    elif pattern == 1:
        op, cl = 101.3 - 0.4 * (i - 3), 101.3 - 0.4 * (i - 2)
    else:
        op, cl = 101.4, 101.5
    hi, lo = (cl, op) if cl > op else (op, cl)
    return f(op), f(hi + 0.1), f(lo - 0.1), f(cl)


def gate_bars(
    symbols: list[str], scales: dict[str, float], n_sessions: int = 5, start: datetime | None = None
) -> pl.DataFrame:
    cal = xnys()
    sessions = cal.sessions(start or datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))[
        :n_sessions
    ]
    rows = []
    for n, s in enumerate(sessions):
        opn = int(s.open.timestamp()) * NS
        for i in range(26):
            close = opn + (i + 1) * 15 * 60 * NS
            for k, sym in enumerate(symbols):
                o, h, lo, c = gate_path((n + k) % 4, i, scales[sym])
                rows.append((iid(sym), close - 15 * 60 * NS, close, close + 60 * NS, o, h, lo, c, 100000.0))
    return pl.DataFrame(
        rows,
        schema=["instrument_id", "ts_open", "ts_close", "available_at", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def orb_config(
    instruments: list[str],
    start: datetime,
    end: datetime,
    *,
    allocation: str = "30000",
    cash: str = "60000",
    manual: list[ManualPosition] | None = None,
    allowlist: list[str] | None = None,
    **kw: object,
) -> BacktestConfig:
    return BacktestConfig(
        strategies=(
            StrategySpec(
                strategy_id="orb-15m",
                short_id="orb15aa",
                name="ORB 15m",
                allocation=Decimal(allocation),
                runtime_class="orb",
                timeframe="15m",
                instruments=tuple(instruments),
                params=ORB_PARAMS,
                segregation_allowlist=tuple(allowlist or ()),
            ),
        ),
        universe=UniverseRef(name="test", instruments=tuple(instruments)),
        timeframe="15m",
        start=start,
        end=end,
        account=AccountSimConfig(cash=Decimal(cash), manual_positions=tuple(manual or ())),
        **kw,  # type: ignore[arg-type]
    )
