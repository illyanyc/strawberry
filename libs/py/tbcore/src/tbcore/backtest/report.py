"""Backtest reports (section 08, BT-050, BT-051, BT-015): metrics, regimes, cost stress, per-date IC.

Every ratio is computed on non-overlapping session returns of the strategy's equity (BT-050):
intraday strategies are aggregated to sessions, then annualized with sqrt(252) / 252.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import polars as pl

from tbcore.backtest.result import BacktestResult
from tbcore.validation.stats import (
    bootstrap_ci,
    deflated_sharpe,
    min_track_record_length,
    moments,
    probabilistic_sharpe,
    sharpe,
)

PERIODS = 252
NS = 1_000_000_000


def session_returns(result: BacktestResult, strategy_id: str | None = None) -> pl.DataFrame:
    """Session returns from the ledger core's end-of-session equity (first session vs allocation)."""
    sid = strategy_id or result.config.strategies[0].strategy_id
    alloc = float(next(s.allocation for s in result.config.strategies if s.strategy_id == sid))
    d = result.daily.filter(pl.col("strategy_id") == sid).sort("time")
    eq = d["equity"].to_numpy()
    prev = np.concatenate([[alloc], eq[:-1]]) if eq.size else eq
    return d.select("date", "equity").with_columns(pl.Series("ret", (eq / prev - 1.0) if eq.size else []))


def drawdown(equity: np.ndarray) -> tuple[float, int]:
    """Max drawdown (fraction of the peak) and its longest duration in sessions."""
    if equity.size == 0:
        return 0.0, 0
    peak = np.maximum.accumulate(equity)
    dd = 1.0 - equity / peak
    longest, cur = 0, 0
    for under in dd > 0:
        cur = cur + 1 if under else 0
        longest = max(longest, cur)
    return float(dd.max()), longest


def capacity_estimate(trades: pl.DataFrame, fills: pl.DataFrame, half_spread_bps: float) -> float:
    """Notional per trade at which square-root impact would eat half of the gross edge (research 02 §3.4).

    impact(s) = impact_now x sqrt(s) for a size multiple s, so s* = (edge / (2 x impact_now))^2.
    Returns NaN when there is no positive gross edge.
    """
    if trades.height == 0 or fills.height == 0:
        return math.nan
    notional = float(str((trades["qty"] * trades["entry_price"]).mean()))
    edge_bps = float(trades["gross_pnl"].sum()) / float((trades["qty"] * trades["entry_price"]).sum()) * 1e4
    impact_now = max(float(fills["slippage_bps"].mean()) - half_spread_bps, 0.01)  # type: ignore[arg-type]
    if edge_bps <= 0:
        return math.nan
    return notional * (edge_bps / (2 * impact_now)) ** 2


def metrics(
    result: BacktestResult, strategy_id: str | None = None, n_trials: float = 1, sr_variance: float = 0.0
) -> dict[str, float]:
    """Section 08 statistics for one strategy (BT-051). Deflation needs the trial count and the
    cross-trial Sharpe variance from the trial ledger (section 11); a single run is plain PSR."""
    sid = strategy_id or result.config.strategies[0].strategy_id
    r = session_returns(result, sid)
    ret = r["ret"].to_numpy()
    eq = r["equity"].to_numpy()
    alloc = float(next(s.allocation for s in result.config.strategies if s.strategy_id == sid))
    trades = result.trades.filter(pl.col("strategy_id") == sid)
    fills = result.fills.filter(pl.col("strategy_id") == sid)
    pnl = trades["pnl"].to_numpy()
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    n = ret.size
    sr = sharpe(ret)
    skew, kurt = moments(ret) if n > 2 else (0.0, 3.0)
    downside = ret[ret < 0]
    dd, dd_len = drawdown(eq)
    years = n / PERIODS if n else math.nan
    total = float(eq[-1] / alloc - 1.0) if n else 0.0
    cagr = (1 + total) ** (1 / years) - 1 if n and years > 0 and total > -1 else math.nan
    avg_eq = float(eq.mean()) if n else alloc
    traded = float((fills["qty"] * fills["price"]).sum()) if fills.height else 0.0
    costs = float(fills["fees"].sum()) + float(fills["slippage_cost"].sum()) if fills.height else 0.0
    eqrow = result.equity.filter(pl.col("strategy_id") == sid)
    exposure = float((eqrow["gross_exposure"] / eqrow["equity"]).mean()) if eqrow.height else 0.0  # type: ignore[arg-type]
    lo, hi = bootstrap_ci(ret, lambda x: sharpe(x, PERIODS), n_boot=500) if n > 10 else (math.nan, math.nan)
    out = {
        "sessions": float(n),
        "trades": float(trades.height),
        "net_pnl": float(pnl.sum()) if pnl.size else 0.0,
        "total_return": total,
        "cagr": cagr,
        "volatility": float(np.std(ret, ddof=1) * math.sqrt(PERIODS)) if n > 1 else math.nan,
        "sharpe": sr * math.sqrt(PERIODS) if math.isfinite(sr) else math.nan,
        "sharpe_ci_low": lo,
        "sharpe_ci_high": hi,
        "sortino": float(np.mean(ret) / np.sqrt(np.mean(np.minimum(ret, 0) ** 2)) * math.sqrt(PERIODS))
        if downside.size
        else math.nan,
        "calmar": cagr / dd if dd > 0 and math.isfinite(cagr) else math.nan,
        "max_drawdown": dd,
        "max_drawdown_sessions": float(dd_len),
        "hit_rate": float((pnl > 0).mean()) if pnl.size else math.nan,
        "avg_win": float(wins.mean()) if wins.size else math.nan,
        "avg_loss": float(losses.mean()) if losses.size else math.nan,
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.size and losses.sum() < 0 else math.nan,
        "turnover": traded / avg_eq / years if n and years > 0 else math.nan,
        "cost_drag": costs / avg_eq / years if n and years > 0 else math.nan,
        "exposure": exposure,
        "capacity_notional": capacity_estimate(trades, fills, result.config.cost_model.half_spread_bps),
        "skew": skew,
        "kurtosis": kurt,
        "psr": probabilistic_sharpe(sr, 0.0, n, skew, kurt) if n > 2 and math.isfinite(sr) else math.nan,
        "dsr": deflated_sharpe(sr, n_trials, sr_variance, skew, kurt, n) if n > 2 and math.isfinite(sr) else math.nan,
        "n_trials": float(n_trials),
        "min_track_record_sessions": min_track_record_length(sr, 0.0, skew, kurt)
        if n > 2 and math.isfinite(sr)
        else math.nan,
    }
    return out


def cost_stress(one_x: BacktestResult, two_x: BacktestResult, strategy_id: str | None = None) -> dict[str, float]:
    """1x versus 2x costs (BT-015); D31 promotion requires a positive result at 2x."""
    a, b = metrics(one_x, strategy_id), metrics(two_x, strategy_id)
    return {
        "net_pnl_1x": a["net_pnl"],
        "net_pnl_2x": b["net_pnl"],
        "sharpe_1x": a["sharpe"],
        "sharpe_2x": b["sharpe"],
        "positive_at_2x": float(b["net_pnl"] > 0),
    }


def _group(trades: pl.DataFrame, key: pl.Expr, name: str) -> pl.DataFrame:
    return (
        trades.with_columns(key.alias(name))
        .group_by(name)
        .agg(
            pl.len().alias("trades"),
            pl.col("pnl").sum().alias("pnl"),
            pl.col("pnl").mean().alias("avg_pnl"),
            (pl.col("pnl") > 0).mean().alias("hit_rate"),
        )
        .sort(name)
    )


def regime_breakdown(
    result: BacktestResult, regimes: Mapping[str, pl.DataFrame] | None = None
) -> dict[str, pl.DataFrame]:
    """Trade P&L by time of day (entry hour, ET), weekday, year, and supplied regime labels (BT-051).

    ``regimes`` maps a name to a frame of ``date`` (session date, ``pl.Date``) and ``label``, known as
    of that session's open (e.g. VIX or trend regimes from ``tbcore.context``); trades join on the
    entry session, so a label decided later can never apply.
    """
    t = result.trades.with_columns(
        pl.col("entry_time").cast(pl.Datetime("ns", "UTC")).dt.convert_time_zone("America/New_York").alias("_et")
    )
    out = {
        "time_of_day": _group(t, pl.col("_et").dt.strftime("%H:00"), "hour"),
        "weekday": _group(t, pl.col("_et").dt.strftime("%a"), "weekday"),
        "year": _group(t, pl.col("_et").dt.year(), "year"),
    }
    for name, frame in (regimes or {}).items():
        lab = frame.select(pl.col("date").cast(pl.Date), pl.col("label").cast(pl.Utf8))
        joined = t.with_columns(pl.col("_et").dt.date().alias("date")).join(lab, on="date", how="left")
        out[name] = _group(joined, pl.col("label").fill_null("unknown"), "label")
    return out


def microcap_split(result: BacktestResult, microcaps: Iterable[str]) -> dict[str, dict[str, float]]:
    """Trade statistics with and without microcaps (Gate 3); ``microcaps`` is the point-in-time set of
    instruments classified as microcaps (market cap from snapshotted fundamentals, section 09)."""
    mc = set(microcaps)
    out = {}
    for name, frame in (
        ("all", result.trades),
        ("ex_microcaps", result.trades.filter(~pl.col("instrument_id").is_in(list(mc)))),
    ):
        pnl = frame["pnl"].to_numpy()
        out[name] = {
            "trades": float(pnl.size),
            "net_pnl": float(pnl.sum()),
            "hit_rate": float((pnl > 0).mean()) if pnl.size else math.nan,
        }
    return out


def per_date_ic(scores: pl.DataFrame, realized: pl.DataFrame) -> dict[str, float]:
    """Per-date (cross-sectional) Spearman IC for swing models (Gate 3): mean, std, IR, t-statistic.

    ``scores`` and ``realized`` have ``date``, ``instrument_id``, and ``score`` / ``ret``.
    """
    df = scores.join(realized, on=["date", "instrument_id"], how="inner")
    ics = (
        df.group_by("date")
        .agg(pl.corr(pl.col("score").rank(), pl.col("ret").rank()).alias("ic"), pl.len().alias("n"))
        .filter(pl.col("n") >= 3)
        .sort("date")["ic"]
        .drop_nans()
        .to_numpy()
    )
    if ics.size < 2:
        return {"ic_mean": math.nan, "ic_std": math.nan, "ic_ir": math.nan, "ic_t": math.nan, "dates": float(ics.size)}
    m, s = float(ics.mean()), float(ics.std(ddof=1))
    return {
        "ic_mean": m,
        "ic_std": s,
        "ic_ir": m / s if s else math.nan,
        "ic_t": m / s * math.sqrt(ics.size) if s else math.nan,
        "dates": float(ics.size),
    }


def monthly_returns(result: BacktestResult, strategy_id: str | None = None) -> pl.DataFrame:
    r = session_returns(result, strategy_id)
    return (
        r.with_columns(pl.col("date").str.slice(0, 7).alias("month"))
        .group_by("month")
        .agg(((pl.col("ret") + 1).product() - 1).alias("return"))
        .sort("month")
    )


def summary(result: BacktestResult, stressed: BacktestResult | None = None, **kw: Any) -> dict[str, Any]:
    """Everything a report shows, as plain data (the tearsheet renders it)."""
    out: dict[str, Any] = {
        "metrics": metrics(result, **kw),
        "warnings": list(result.warnings),
        "survivorship": result.config.universe.survivorship,
        "config_hash": result.config_hash,
    }
    if stressed is not None:
        out["cost_stress"] = cost_stress(result, stressed)
    return out
