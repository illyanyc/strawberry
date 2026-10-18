"""Vectorized evaluation of model and rule decisions (screening tier for ML signals; BT-006, ML-012, EX-003).

A decision ``(instrument, decision bar t, side)`` enters at the **open of bar t+1** (never the signal
bar, N2) and exits at the open of bar ``t+1+horizon``; intraday trades (``cap_at_session_close``) exit
at the session's last bar close instead when the horizon would cross it, swing trades hold across
sessions. One open trade per instrument; trades decided at the same bar share the allocation equally,
with at most ``1/min_names`` per trade. Slippage and fees come from the shared cost model
(``tbcore.backtest.costs``, BT-010..BT-016); P&L is booked to the session of the exit. Protective stops
are not simulated here (known difference); the event-driven tier runs them before any gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import polars as pl
from numpy.typing import NDArray

from tbcore.backtest.costs import CostModelConfig, apply_slippage, fees, slippage_bps
from tbcore.backtest.vectorized import VectorData

NS = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC).date()


@dataclass(frozen=True)
class SignalEvalConfig:
    horizon: int
    cap_at_session_close: bool = True
    allocation: float = 100_000.0
    min_names: int = 5


@dataclass
class SignalEvalResult:
    trades: pl.DataFrame
    session_returns: NDArray[np.float64]  # one value per session of the data, 0 where nothing exited
    session_days: NDArray[np.int64]  # New York session date as days since 1970-01-01
    metrics: dict[str, float]


def session_days(d: VectorData) -> NDArray[np.int64]:
    """Session index → New York date ordinal (days since epoch), for aligning return series."""
    n = int(d.session.max()) + 1 if d.session.size else 0
    out = np.zeros(n, dtype=np.int64)
    for s in range(n):
        k = int(np.flatnonzero(d.session == s)[0])
        # session_close is 16:00 New York (or an early close): its UTC date equals the session date.
        out[s] = (datetime.fromtimestamp(int(d.session_close[k]) / NS, tz=UTC).date() - EPOCH).days
    return out


def _session_last(session: NDArray[np.int64]) -> NDArray[np.int64]:
    t_n = session.shape[0]
    out = np.zeros(t_n, dtype=np.int64)
    last = t_n - 1
    for k in range(t_n - 1, -1, -1):
        if k < t_n - 1 and session[k] != session[k + 1]:
            last = k
        out[k] = last
    return out


def evaluate_decisions(
    d: VectorData,
    instrument_ids: NDArray[np.str_] | list[str],
    decision_ts_open: NDArray[np.int64],
    side: NDArray[np.int64],
    cfg: SignalEvalConfig,
    cost: CostModelConfig,
) -> SignalEvalResult:
    """Simulate the decisions on ``d`` (decision-timeframe bars of the universe)."""
    days = session_days(d)
    n_sess = days.shape[0]
    empty = SignalEvalResult(
        pl.DataFrame(
            schema={
                "instrument_id": pl.Utf8,
                "decision_time": pl.Int64,
                "entry_time": pl.Int64,
                "exit_time": pl.Int64,
                "side": pl.Int64,
                "qty": pl.Float64,
                "entry_price": pl.Float64,
                "exit_price": pl.Float64,
                "pnl": pl.Float64,
                "fees": pl.Float64,
                "session": pl.Int64,
            }
        ),
        np.zeros(n_sess),
        days,
        {"trades": 0.0, "net_return": 0.0, "sharpe": math.nan, "turnover": 0.0, "cost_drag": 0.0, "hit_rate": math.nan},
    )
    if len(decision_ts_open) == 0:
        return empty
    ti = {int(t): k for k, t in enumerate(d.ts_open)}
    ni = {s: k for k, s in enumerate(d.instruments)}
    t_idx = np.array([ti.get(int(t), -1) for t in decision_ts_open], dtype=np.int64)
    n_idx = np.array([ni.get(str(s), -1) for s in instrument_ids], dtype=np.int64)
    sd = np.asarray(side, dtype=np.int64)
    t_n = d.ts_open.shape[0]
    ok = (t_idx >= 0) & (n_idx >= 0) & (t_idx + 1 < t_n) & (sd != 0)
    t_idx, n_idx, sd = t_idx[ok], n_idx[ok], sd[ok]
    e0 = t_idx + 1
    last = _session_last(d.session)
    if cfg.cap_at_session_close:
        ok = (d.session[e0] == d.session[t_idx]) & (d.session[t_idx] >= 0)
    else:
        ok = (d.session[e0] >= 0) & (d.session[t_idx] >= 0)
    t_idx, n_idx, sd, e0 = t_idx[ok], n_idx[ok], sd[ok], e0[ok]
    x = e0 + cfg.horizon
    if cfg.cap_at_session_close:
        at_close = x > last[e0]
        x = np.where(at_close, last[e0], x)
    else:
        at_close = np.zeros_like(x, dtype=bool)
        keep = x < t_n
        t_idx, n_idx, sd, e0, x, at_close = t_idx[keep], n_idx[keep], sd[keep], e0[keep], x[keep], at_close[keep]
    entry_raw = d.open[e0, n_idx]
    exit_raw = np.where(at_close, d.close[x, n_idx], d.open[x, n_idx])
    fin = np.isfinite(entry_raw) & np.isfinite(exit_raw) & (entry_raw > 0)
    t_idx, n_idx, sd, e0, x, entry_raw, exit_raw = (
        t_idx[fin],
        n_idx[fin],
        sd[fin],
        e0[fin],
        x[fin],
        entry_raw[fin],
        exit_raw[fin],
    )
    # One open trade per instrument, in decision order.
    order = np.lexsort((n_idx, t_idx))
    t_idx, n_idx, sd, e0, x, entry_raw, exit_raw = (a[order] for a in (t_idx, n_idx, sd, e0, x, entry_raw, exit_raw))
    keep = np.ones(t_idx.shape[0], dtype=bool)
    busy: dict[int, int] = {}
    for i in range(t_idx.shape[0]):
        n = int(n_idx[i])
        if n in busy and e0[i] <= busy[n]:
            keep[i] = False
            continue
        busy[n] = int(x[i])
    t_idx, n_idx, sd, e0, x, entry_raw, exit_raw = (a[keep] for a in (t_idx, n_idx, sd, e0, x, entry_raw, exit_raw))
    if t_idx.size == 0:
        return empty
    _, inv, counts = np.unique(t_idx, return_inverse=True, return_counts=True)
    weight = 1.0 / np.maximum(counts[inv], cfg.min_names)
    qty = np.floor(cfg.allocation * weight / entry_raw)
    keep = qty > 0
    t_idx, n_idx, sd, e0, x, entry_raw, exit_raw, qty = (
        a[keep] for a in (t_idx, n_idx, sd, e0, x, entry_raw, exit_raw, qty)
    )
    if t_idx.size == 0:
        return empty
    slip_in = slippage_bps(cost, qty, d.minute_of_session[e0], d.adv[e0, n_idx], d.daily_vol[e0, n_idx])
    slip_out = slippage_bps(cost, qty, d.minute_of_session[x], d.adv[x, n_idx], d.daily_vol[x, n_idx])
    entry_px = apply_slippage(entry_raw, sd, slip_in)
    exit_px = apply_slippage(exit_raw, -sd, slip_out)
    fee = fees(cost, sd, qty, entry_px, d.ts_open[e0]) + fees(cost, -sd, qty, exit_px, d.ts_open[x])
    gross = sd * qty * (exit_raw - entry_raw)
    pnl = sd * qty * (exit_px - entry_px) - fee
    sess = d.session[x]
    daily = np.zeros(n_sess)
    np.add.at(daily, sess, pnl)
    rets = daily / cfg.allocation
    sdev = float(np.std(rets, ddof=1)) if rets.size > 1 else 0.0
    trades = pl.DataFrame(
        {
            "instrument_id": [d.instruments[int(n)] for n in n_idx],
            "decision_time": d.ts_open[t_idx],
            "entry_time": d.ts_open[e0],
            "exit_time": d.ts_open[x],
            "side": sd,
            "qty": qty,
            "entry_price": entry_px,
            "exit_price": exit_px,
            "pnl": pnl,
            "fees": fee,
            "session": sess,
        }
    )
    notional = float(np.sum(qty * entry_raw))
    metrics = {
        "trades": float(pnl.size),
        "net_return": float(rets.sum()),
        "sharpe": float(np.mean(rets) / sdev) if sdev > 0 else math.nan,
        "turnover": 2.0 * notional / cfg.allocation / max(n_sess, 1),
        "cost_drag": float((gross.sum() - pnl.sum()) / cfg.allocation),
        "hit_rate": float(np.mean(pnl > 0)),
    }
    return SignalEvalResult(trades, rets, days, metrics)
