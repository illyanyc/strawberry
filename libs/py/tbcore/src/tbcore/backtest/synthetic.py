"""Synthetic market data for leakage tests and benchmarks (BT-061): martingale prices, no edge."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import polars as pl

from tbcore.data.calendar import xnys

NS = 1_000_000_000


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
