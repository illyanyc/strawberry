"""Vectorized screening tier (section 08, ADR-0031, BT-006, BT-025).

A signal function maps aligned bar matrices (time x instrument) to entry signals with stop and
target levels and optional exit signals. Entries execute at the next bar's open with the shared cost
model (ADR-0029); stops and targets use bar high/low with the event tier's conservative ordering;
positions can be forced flat at the session close. Results are screening only: anything promoted
from here is rerun event-driven (BT-006). Known differences are listed in every result.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from tbcore.backtest.costs import CostModelConfig, apply_slippage, fees, slippage_bps
from tbcore.data.calendar import xnys

F64 = NDArray[np.float64]
I64 = NDArray[np.int64]
B = NDArray[np.bool_]
NS = 1_000_000_000

KNOWN_DIFFERENCES = (
    "no pre-trade gate (limits, margin guard, capital) and no ledger: every signal trades",
    "sizing on the fixed allocation (no compounding of realized P&L)",
    "market entries at the next bar open; no marketable-limit collar, no participation cap, no partial fills",
    "a position open at the session close is closed at that bar's close when flat_at_session_close",
)


@dataclass(frozen=True)
class VectorData:
    """Bars pivoted to a common timeline: arrays are (T,) or (T, N); missing bars are NaN."""

    instruments: tuple[str, ...]
    ts_open: I64
    ts_close: I64
    session: I64  # session index per row (-1 = outside the regular session)
    bar_in_session: I64
    minute_of_session: I64
    session_close: I64  # ns
    open: F64
    high: F64
    low: F64
    close: F64
    volume: F64
    adv: F64
    daily_vol: F64

    @classmethod
    def from_bars(cls, bars: pl.DataFrame, lookback: int = 20) -> VectorData:
        """Pivot decision bars (lake schema; int64 or datetime times) to the vector layout."""
        df = bars
        for c in ("ts_open", "ts_close"):
            if df.schema[c] != pl.Int64:
                df = df.with_columns(pl.col(c).dt.cast_time_unit("ns").dt.replace_time_zone("UTC").cast(pl.Int64))
        inst = tuple(sorted(df["instrument_id"].unique().to_list()))
        times = np.sort(df["ts_open"].unique().to_numpy()).astype(np.int64)
        ti = {int(t): k for k, t in enumerate(times)}
        ni = {s: k for k, s in enumerate(inst)}
        t_n, n_n = len(times), len(inst)
        mats = {c: np.full((t_n, n_n), np.nan) for c in ("open", "high", "low", "close", "volume")}
        rows = np.array([ti[int(x)] for x in df["ts_open"].to_numpy()], dtype=np.int64)
        cols = np.array([ni[s] for s in df["instrument_id"].to_list()], dtype=np.int64)
        for c, m in mats.items():
            m[rows, cols] = df[c].to_numpy().astype(np.float64)
        ts_close = np.zeros(t_n, dtype=np.int64)
        ts_close[rows] = df["ts_close"].to_numpy()
        cal = xnys()
        session = np.full(t_n, -1, dtype=np.int64)
        minute = np.full(t_n, -1, dtype=np.int64)
        sclose = np.zeros(t_n, dtype=np.int64)
        sess_idx: dict[object, int] = {}
        for k, t in enumerate(times):
            s, part = cal.session_at(datetime.fromtimestamp(int(t) / NS, tz=UTC))
            if s is None or part != "regular":
                continue
            session[k] = sess_idx.setdefault(s.date, len(sess_idx))
            opn, cls_ = int(s.open.timestamp()) * NS, int(s.close.timestamp()) * NS
            since, to_close = (int(t) - opn) // (60 * NS), (cls_ - int(t)) // (60 * NS)
            minute[k] = 390 - to_close if to_close < since else since
            sclose[k] = cls_
        bis = np.zeros(t_n, dtype=np.int64)
        for k in range(1, t_n):
            bis[k] = bis[k - 1] + 1 if session[k] == session[k - 1] and session[k] >= 0 else 0
        adv, dvol = _daily_stats(session, mats["volume"], mats["close"], lookback)
        return cls(
            inst,
            times,
            ts_close,
            session,
            bis,
            minute,
            sclose,
            mats["open"],
            mats["high"],
            mats["low"],
            mats["close"],
            mats["volume"],
            adv,
            dvol,
        )


def _daily_stats(session: I64, volume: F64, close: F64, lookback: int) -> tuple[F64, F64]:
    """ADV and daily return volatility from prior sessions only (no look-ahead), per row."""
    n_s = int(session.max()) + 1 if session.size and session.max() >= 0 else 0
    n = volume.shape[1]
    dv = np.full((n_s, n), np.nan)
    dc = np.full((n_s, n), np.nan)
    for s in range(n_s):
        m = session == s
        if m.any():
            dv[s] = np.nansum(volume[m], axis=0)
            last = close[m]
            dc[s] = last[
                np.max(np.where(np.isfinite(last), np.arange(last.shape[0])[:, None], -1), axis=0), np.arange(n)
            ]
    ret = np.full_like(dc, np.nan)
    ret[1:] = np.log(dc[1:] / dc[:-1])
    adv_s = np.full_like(dv, np.nan)
    vol_s = np.full_like(dv, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows stay NaN (fallback impact)
        for s in range(n_s):
            lo = max(0, s - lookback)
            if s - lo >= 2:
                adv_s[s] = np.nanmean(dv[lo:s], axis=0)
                vol_s[s] = np.nanstd(ret[lo:s], axis=0, ddof=1)
    adv = np.full(volume.shape, np.nan)
    vol = np.full(volume.shape, np.nan)
    ok = session >= 0
    adv[ok] = adv_s[session[ok]]
    vol[ok] = vol_s[session[ok]]
    return adv, vol


@dataclass(frozen=True)
class EntrySignals:
    """Signals decided at the close of bar t (executed from bar t+1)."""

    entry: B
    stop: F64
    target: F64
    exit: B | None = None
    side: int = 1


@dataclass(frozen=True)
class VectorRules:
    allocation: float
    risk_per_trade: float = 0.005
    max_holding_bars: int | None = None
    flat_at_session_close: bool = True
    tick: float = 0.01


@dataclass
class VectorizedResult:
    params: dict[str, Any]
    trades: pl.DataFrame
    daily_pnl: F64
    metrics: dict[str, float]
    known_differences: tuple[str, ...] = field(default=KNOWN_DIFFERENCES)


SignalFn = Callable[[VectorData, Mapping[str, Any]], EntrySignals]


def run_vectorized(
    signal_fn: SignalFn,
    data: VectorData,
    cost_model: CostModelConfig,
    rules: VectorRules,
    params: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> VectorizedResult | list[VectorizedResult]:
    """Screen one parameter set (a mapping, default none) or many (a sequence) on the same data."""
    if params is None or isinstance(params, Mapping):
        return _run_one(signal_fn, data, cost_model, rules, params or {})
    return [_run_one(signal_fn, data, cost_model, rules, p) for p in params]


def _run_one(
    signal_fn: SignalFn, d: VectorData, cost: CostModelConfig, rules: VectorRules, params: Mapping[str, Any]
) -> VectorizedResult:
    sig = signal_fn(d, params)
    t_n = d.ts_open.shape[0]
    side = sig.side
    et, en = np.nonzero(sig.entry[:-1])
    nxt = et + 1
    ok = (
        (d.session[nxt] == d.session[et])
        & (d.session[et] >= 0)
        & np.isfinite(d.open[nxt, en])
        & np.isfinite(sig.stop[et, en])
    )
    et, en, nxt = et[ok], en[ok], nxt[ok]
    order = np.lexsort((et, en))
    et, en, nxt = et[order], en[order], nxt[order]
    # Window of bars after the entry: up to the session end (or max holding).
    sess_last = np.zeros(t_n, dtype=np.int64)
    last = t_n - 1
    for k in range(t_n - 1, -1, -1):
        if k < t_n - 1 and d.session[k] != d.session[k + 1]:
            last = k
        sess_last[k] = last
    h = int(np.max(sess_last[nxt] - nxt) + 1) if nxt.size else 1
    if rules.max_holding_bars:
        h = min(h, rules.max_holding_bars)
    ks = np.arange(h)
    idx = np.minimum(nxt[:, None] + ks[None, :], sess_last[nxt][:, None])
    valid = nxt[:, None] + ks[None, :] <= sess_last[nxt][:, None]
    col = en[:, None]
    o, hi, lo, cl = d.open[idx, col], d.high[idx, col], d.low[idx, col], d.close[idx, col]
    stop = sig.stop[et, en][:, None]
    tgt = sig.target[et, en][:, None]
    through = rules.tick
    first = ks[None, :] == 0
    if side > 0:
        stop_hit = valid & (lo <= stop)
        tgt_hit = valid & np.isfinite(tgt) & ((~first & (o >= tgt)) | (hi >= tgt + through))
        stop_px = np.where(first | (o > stop), stop, o)
        tgt_px = np.where(~first & (o >= tgt), o, tgt)
        tgt_first = ~first & (o >= tgt)
    else:
        stop_hit = valid & (hi >= stop)
        tgt_hit = valid & np.isfinite(tgt) & ((~first & (o <= tgt)) | (lo <= tgt - through))
        stop_px = np.where(first | (o < stop), stop, o)
        tgt_px = np.where(~first & (o <= tgt), o, tgt)
        tgt_first = ~first & (o <= tgt)
    if sig.exit is not None:
        ex_bar = np.minimum(idx + 1, sess_last[nxt][:, None])
        exit_sig = valid & sig.exit[idx, col] & (idx + 1 <= sess_last[nxt][:, None])
        exit_px = d.open[ex_bar, col]
    else:
        exit_sig = np.zeros_like(valid)
        exit_px = cl
    # Event per bar: stop (first unless the open is beyond the target), target, then the exit signal
    # (filled at the next bar's open, so it ranks after same-bar stops and targets).
    ev = stop_hit | tgt_hit | exit_sig
    k_ev = np.where(ev.any(1), ev.argmax(1), -1)
    rows = np.arange(len(et))
    end_k = np.where(k_ev >= 0, k_ev, valid.sum(1) - 1)
    kk = np.clip(end_k, 0, h - 1)
    s_hit, t_hit, x_hit = stop_hit[rows, kk], tgt_hit[rows, kk], exit_sig[rows, kk]
    use_tgt = t_hit & (tgt_first[rows, kk] | ~s_hit)
    reason = np.where(
        k_ev < 0,
        "session_close",
        np.where(use_tgt, "take_profit", np.where(s_hit, "stop", np.where(x_hit, "exit", "session_close"))),
    )
    raw_exit = np.where(
        use_tgt, tgt_px[rows, kk], np.where(s_hit, stop_px[rows, kk], np.where(x_hit, exit_px[rows, kk], cl[rows, kk]))
    )
    exit_row = np.where(x_hit & ~s_hit & ~use_tgt, np.minimum(idx[rows, kk] + 1, sess_last[nxt]), idx[rows, kk])
    # One position per instrument: skip entries while the previous trade is open.
    keep = np.ones(len(et), dtype=bool)
    last_exit: dict[int, int] = {}
    for i in range(len(et)):
        n = int(en[i])
        if n in last_exit and nxt[i] <= last_exit[n]:
            keep[i] = False
            continue
        last_exit[n] = int(exit_row[i])
    sel = np.flatnonzero(keep)
    et, en, nxt = et[sel], en[sel], nxt[sel]
    raw_entry = d.open[nxt, en]
    stop_v, tgt_v = sig.stop[et, en], sig.target[et, en]
    reason, raw_exit, exit_row = reason[sel], raw_exit[sel], exit_row[sel]
    risk = np.abs(raw_entry - stop_v)
    by_risk = np.floor(rules.allocation * rules.risk_per_trade / np.where(risk > 0, risk, np.inf))
    qty = np.maximum(np.minimum(by_risk, np.floor(rules.allocation / raw_entry)), 0.0)
    slip_in = slippage_bps(cost, qty, d.minute_of_session[nxt], d.adv[nxt, en], d.daily_vol[nxt, en])
    slip_out = slippage_bps(cost, qty, d.minute_of_session[exit_row], d.adv[exit_row, en], d.daily_vol[exit_row, en])
    entry_px = apply_slippage(raw_entry, side, slip_in)
    exit_px_f = np.where(reason == "take_profit", raw_exit, apply_slippage(raw_exit, -side, slip_out))
    fee = fees(cost, side, qty, entry_px, d.ts_open[nxt]) + fees(cost, -side, qty, exit_px_f, d.ts_open[exit_row])
    pnl = side * qty * (exit_px_f - entry_px) - fee
    n_sess = int(d.session.max()) + 1 if d.session.size else 0
    daily = np.zeros(max(n_sess, 0))
    np.add.at(daily, d.session[exit_row], pnl)
    trades = pl.DataFrame(
        {
            "instrument_id": [d.instruments[int(n)] for n in en],
            "entry_time": d.ts_open[nxt],
            "exit_time": d.ts_open[exit_row],
            "qty": qty,
            "entry_price": entry_px,
            "exit_price": exit_px_f,
            "stop": stop_v,
            "target": tgt_v,
            "pnl": pnl,
            "fees": fee,
            "exit_reason": reason,
        }
    )
    rets = daily / rules.allocation
    sd = float(np.std(rets, ddof=1)) if rets.size > 1 else 0.0
    metrics = {
        "trades": float(len(pnl)),
        "net_pnl": float(pnl.sum()),
        "sharpe": float(np.mean(rets) / sd * math.sqrt(252)) if sd > 0 else math.nan,
        "hit_rate": float(np.mean(pnl > 0)) if len(pnl) else math.nan,
    }
    return VectorizedResult(dict(params), trades, daily, metrics)
