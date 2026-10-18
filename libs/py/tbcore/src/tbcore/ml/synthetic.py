"""Synthetic universes for ML tests, studies, and the dev profile (ML-004; never live-eligible, EX-052).

Returns follow a one-factor model with an optional planted short-term reversal in the idiosyncratic
part: ``e_t = -edge * mean(e_{t-k..t-1}) + sigma * eps``. ``edge = 0`` gives pure noise (the
optimizer's honesty tests); ``edge > 0`` gives a weak, real, cross-sectional signal that residual
features (``ret_4`` vs ``mkt_ret_4``, ``xs_rank_ret_4``) can learn. Market volatility switches between
regimes in blocks of sessions so regime slices are meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import polars as pl

from tbcore.data.calendar import TIMEFRAMES, xnys

NS = 1_000_000_000
FINALITY_NS = 60 * NS
MARKET_ID = "01KSYNMKT00000000000000000"


def instrument_id(j: int) -> str:
    return f"01KSYN{j:020d}"


@dataclass(frozen=True)
class SyntheticSpec:
    n_instruments: int = 24
    n_sessions: int = 160
    timeframe: str = "15m"
    edge: float = 0.0
    seed: int = 11
    lookback: int = 4
    idio_vol: float = 0.003
    market_vol: float = 0.0015
    start: datetime = datetime(2025, 1, 2, tzinfo=UTC)


PRESETS: dict[str, SyntheticSpec] = {
    "intraday_edge": SyntheticSpec(edge=0.35, seed=11),
    "intraday_noise": SyntheticSpec(edge=0.0, seed=12),
    "daily_edge": SyntheticSpec(
        n_instruments=40, n_sessions=750, timeframe="1d", edge=0.3, seed=13, lookback=5, idio_vol=0.015, market_vol=0.01
    ),
    "daily_noise": SyntheticSpec(
        n_instruments=40, n_sessions=750, timeframe="1d", edge=0.0, seed=14, lookback=5, idio_vol=0.015, market_vol=0.01
    ),
    "intraday_small": SyntheticSpec(n_instruments=12, n_sessions=90, edge=0.35, seed=15),
    "daily_small": SyntheticSpec(
        n_instruments=20, n_sessions=400, timeframe="1d", edge=0.3, seed=16, lookback=5, idio_vol=0.015, market_vol=0.01
    ),
}


def preset(name: str) -> SyntheticSpec:
    """``synthetic:<name>`` data sources resolve here; unknown names raise."""
    if name not in PRESETS:
        raise ValueError(f"unknown synthetic preset {name!r} (have {sorted(PRESETS)})")
    return PRESETS[name]


def _bar_times(spec: SyntheticSpec) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ts_open, ts_close, session index) for every regular-session bar."""
    cal = xnys()
    sessions = cal.sessions(spec.start, datetime(2035, 1, 1, tzinfo=UTC))[: spec.n_sessions]
    if len(sessions) < spec.n_sessions:
        raise ValueError("calendar too short for the requested sessions")
    opens: list[int] = []
    closes: list[int] = []
    sess: list[int] = []
    for k, s in enumerate(sessions):
        for o in s.bar_opens(spec.timeframe):
            opens.append(int(o.timestamp()) * NS)
            closes.append(int(s.bar_close(o, spec.timeframe).timestamp()) * NS)
            sess.append(k)
    return np.array(opens, dtype=np.int64), np.array(closes, dtype=np.int64), np.array(sess, dtype=np.int64)


def generate(spec: SyntheticSpec) -> pl.DataFrame:
    """Bars in the lake schema (ns UTC ints) for ``n_instruments`` names plus the market proxy."""
    if spec.timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {spec.timeframe}")
    rng = np.random.default_rng(spec.seed)
    ts_open, ts_close, sess = _bar_times(spec)
    t_n = ts_open.size
    # Market volatility regime per block of 20 sessions: low / normal / high.
    blocks = sess // 20
    regime_scale = np.array([0.6, 1.0, 1.8])[rng.integers(0, 3, int(blocks.max()) + 1)][blocks]
    first_bar = np.r_[True, sess[1:] != sess[:-1]]
    gap_scale = np.where(first_bar, 1.5, 0.0) if spec.timeframe != "1d" else np.zeros(t_n)
    m = rng.normal(0.0, spec.market_vol, t_n) * regime_scale
    frames = []
    for j in range(spec.n_instruments + 1):
        is_market = j == spec.n_instruments
        beta = 1.0 if is_market else float(rng.uniform(0.6, 1.4))
        idio = np.zeros(t_n)
        if not is_market:
            eps = rng.normal(0.0, spec.idio_vol, t_n)
            k = spec.lookback
            for t in range(t_n):
                lo = max(0, t - k)
                mean_prev = idio[lo:t].mean() if t > lo else 0.0
                idio[t] = -spec.edge * mean_prev + eps[t]
        r = beta * m + idio + rng.normal(0.0, spec.idio_vol, t_n) * gap_scale * 0.5
        px0 = 40.0 + 120.0 * rng.random()
        close = px0 * np.exp(np.cumsum(r))
        prev = np.r_[px0, close[:-1]]
        open_ = prev * np.exp(rng.normal(0.0, spec.idio_vol * 0.2, t_n))
        wick = np.abs(rng.normal(0.0, spec.idio_vol * 0.5, t_n))
        high = np.maximum(open_, close) * np.exp(wick)
        low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0.0, spec.idio_vol * 0.5, t_n)))
        vol = np.exp(rng.normal(11.0 if spec.timeframe != "1d" else 14.0, 0.4, t_n))
        frames.append(
            pl.DataFrame(
                {
                    "instrument_id": MARKET_ID if is_market else instrument_id(j),
                    "ts_open": ts_open,
                    "ts_close": ts_close,
                    "available_at": ts_close + FINALITY_NS,
                    "open": np.round(open_, 4),
                    "high": np.round(high, 4),
                    "low": np.round(low, 4),
                    "close": np.round(close, 4),
                    "volume": np.round(vol),
                    "vwap": np.round((high + low + close) / 3.0, 4),
                    "trade_count": np.round(vol / 100.0),
                }
            )
        )
    df = pl.concat(frames)
    return df.with_columns(
        pl.max_horizontal("open", "close", "high").alias("high"),
        pl.min_horizontal("open", "close", "low").alias("low"),
    ).sort(["instrument_id", "ts_open"])


def universe(spec: SyntheticSpec) -> list[str]:
    return [instrument_id(j) for j in range(spec.n_instruments)]


def holdout_boundary(spec: SyntheticSpec, holdout_sessions: int | None = None) -> int:
    """UTC ns of the first session of the trailing holdout (default: the last 20 % of sessions)."""
    cal = xnys()
    sessions = cal.sessions(spec.start, datetime(2035, 1, 1, tzinfo=UTC))[: spec.n_sessions]
    k = holdout_sessions if holdout_sessions is not None else max(1, spec.n_sessions // 5)
    return int(sessions[spec.n_sessions - k].open.timestamp()) * NS


def data_start(spec: SyntheticSpec) -> int:
    cal = xnys()
    return int(cal.sessions(spec.start, datetime(2035, 1, 1, tzinfo=UTC))[0].open.timestamp()) * NS


def data_end(spec: SyntheticSpec) -> int:
    cal = xnys()
    s = cal.sessions(spec.start, datetime(2035, 1, 1, tzinfo=UTC))[spec.n_sessions - 1]
    return int(s.close.timestamp()) * NS + FINALITY_NS + 1
