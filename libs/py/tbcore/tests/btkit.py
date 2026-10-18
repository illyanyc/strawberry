"""Synthetic market data for backtest tests (no lake needed)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
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


def random_walk_bars(
    n_instruments: int,
    n_sessions: int,
    timeframe_min: int = 15,
    seed: int = 0,
    vol: float = 0.002,
    start: datetime | None = None,
) -> pl.DataFrame:
    """Martingale prices (no edge exists): log-normal steps, OHLC from 4 sub-steps per bar."""
    cal = xnys()
    sessions = cal.sessions(start or datetime(2025, 1, 2, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))[:n_sessions]
    rng = np.random.default_rng(seed)
    step = timeframe_min * 60 * NS
    frames = []
    for j in range(n_instruments):
        px = 50.0 + 100.0 * rng.random()
        parts: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for s in sessions:
            opn, cls = int(s.open.timestamp()) * NS, int(s.close.timestamp()) * NS
            nb = (cls - opn) // step
            sub = px * np.exp(np.cumsum(rng.normal(0, vol / 2, nb * 4)).reshape(nb, 4))
            px = float(sub[-1, -1])
            parts.append(
                (opn + np.arange(nb, dtype=np.int64) * step, sub, rng.integers(20_000, 200_000, nb).astype(float))
            )
        ts_open = np.concatenate([p[0] for p in parts])
        sub_all = np.concatenate([p[1] for p in parts])
        o_, h_, l_, c_ = sub_all[:, 0], sub_all.max(1), sub_all.min(1), sub_all[:, -1]
        v_ = np.concatenate([p[2] for p in parts])
        to = np.asarray(ts_open, dtype=np.int64)
        frames.append(
            pl.DataFrame(
                {
                    "instrument_id": f"01KRW{j:021d}",
                    "ts_open": to,
                    "ts_close": to + step,
                    "available_at": to + step + 60 * NS,
                    "open": np.round(o_, 2),
                    "high": np.round(h_, 2),
                    "low": np.round(l_, 2),
                    "close": np.round(c_, 2),
                    "volume": v_,
                }
            )
        )
    df = pl.concat(frames)
    return df.with_columns(
        pl.max_horizontal("open", "close", "high").alias("high"), pl.min_horizontal("open", "close", "low").alias("low")
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
