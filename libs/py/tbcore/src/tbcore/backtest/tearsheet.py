"""Static HTML tearsheet for one backtest (section 08 reports; BT-005, BT-015, BT-051).

Self-contained: inline SVG charts, no scripts and no external resources, so it can be stored as an
artifact and served by the UI under its strict CSP (as a document, not inlined into the SPA).
"""

from __future__ import annotations

import html
import math
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import polars as pl

from tbcore.backtest.report import drawdown, metrics, monthly_returns, regime_breakdown, session_returns
from tbcore.backtest.result import BacktestResult

TRADE_COLUMNS = ("instrument_id", "side", "qty", "entry_price", "exit_price", "pnl", "exit_reason")
STYLE = (
    "body{font:14px system-ui,sans-serif;margin:24px;color:#111}h1{font-size:20px}h2{font-size:16px;margin-top:28px}"
    "table{border-collapse:collapse}td,th{padding:3px 10px;border-bottom:1px solid #ddd;text-align:right}"
    "th:first-child,td:first-child{text-align:left}.warn{color:#a40}.muted{color:#666}"
)


def _fmt(v: object) -> str:
    if isinstance(v, float):
        if math.isnan(v):
            return "n/a"
        return f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}"
    return html.escape(str(v))


def _svg_line(y: np.ndarray, width: int = 720, height: int = 180, color: str = "#2563eb", fill: bool = False) -> str:
    if y.size < 2:
        return "<p class='muted'>not enough sessions to chart</p>"
    lo, hi = float(np.min(y)), float(np.max(y))
    span = hi - lo or 1.0
    xs = np.linspace(0, width, y.size)
    ys = height - (y - lo) / span * (height - 10) - 5
    pts = " ".join(f"{x:.1f},{v:.1f}" for x, v in zip(xs, ys, strict=True))
    area = f"<polygon points='0,{height} {pts} {width},{height}' fill='{color}' opacity='0.15'/>" if fill else ""
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' role='img'>{area}"
        f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='1.5'/></svg>"
        f"<div class='muted'>min {_fmt(lo)} · max {_fmt(hi)}</div>"
    )


def _table(df: pl.DataFrame) -> str:
    head = "".join(f"<th>{html.escape(c)}</th>" for c in df.columns)
    rows = "".join("<tr>" + "".join(f"<td>{_fmt(v)}</td>" for v in r) + "</tr>" for r in df.iter_rows())
    return f"<table><tr>{head}</tr>{rows}</table>"


def _kv(d: Mapping[str, object]) -> str:
    return (
        "<table>" + "".join(f"<tr><td>{html.escape(k)}</td><td>{_fmt(v)}</td></tr>" for k, v in d.items()) + "</table>"
    )


def render(
    result: BacktestResult,
    stressed: BacktestResult | None = None,
    overfitting: Mapping[str, float] | None = None,
    regimes: Mapping[str, pl.DataFrame] | None = None,
) -> str:
    sid = result.config.strategies[0].strategy_id
    r = session_returns(result, sid)
    eq = r["equity"].to_numpy()
    peak = np.maximum.accumulate(eq) if eq.size else eq
    dd = 1.0 - eq / peak if eq.size else eq
    m = metrics(result, sid)
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        f"<title>Backtest {html.escape(sid)}</title><style>{STYLE}</style></head><body>",
        f"<h1>Backtest: {html.escape(sid)} ",
        f"<span class='muted'>({result.engine}, config {result.config_hash[:12]})</span></h1>",
        f"<p>{html.escape(result.config.start.isoformat())} → {html.escape(result.config.end.isoformat())}",
        f" · universe {html.escape(result.config.universe.name)}",
        f" · survivorship <b>{html.escape(result.config.universe.survivorship)}</b></p>",
    ]
    if result.warnings:
        parts.append(
            "<h2>Warnings</h2><ul>"
            + "".join(f"<li class='warn'>{html.escape(w)}</li>" for w in result.warnings)
            + "</ul>"
        )
    parts += ["<h2>Equity</h2>", _svg_line(eq), "<h2>Drawdown</h2>", _svg_line(-dd, color="#dc2626", fill=True)]
    parts += ["<h2>Metrics</h2>", _kv(m)]
    if stressed is not None:
        s = metrics(stressed, sid)
        parts += [
            "<h2>Cost stress (1x vs 2x)</h2>",
            _kv(
                {
                    "net P&L 1x": m["net_pnl"],
                    "net P&L 2x": s["net_pnl"],
                    "Sharpe 1x": m["sharpe"],
                    "Sharpe 2x": s["sharpe"],
                    "positive at 2x": "yes" if s["net_pnl"] > 0 else "no",
                }
            ),
        ]
    if overfitting:
        parts += ["<h2>Overfitting statistics</h2>", _kv(overfitting)]
    fills = result.fills
    if fills.height:
        parts += [
            "<h2>Costs</h2>",
            _kv(
                {
                    "fees": float(fills["fees"].sum()),
                    "slippage": float(fills["slippage_cost"].sum()),
                    "mean slippage (bps)": float(str(fills["slippage_bps"].mean())),
                }
            ),
        ]
    parts += ["<h2>Monthly returns</h2>", _table(monthly_returns(result, sid))]
    for name, frame in regime_breakdown(result, regimes).items():
        parts += [f"<h2>By {html.escape(name.replace('_', ' '))}</h2>", _table(frame)]
    if result.trades.height:
        parts += [
            "<h2>Trades</h2>",
            _table(
                result.trades.select("instrument_id", "side", "qty", "entry_price", "exit_price", "pnl", "exit_reason")
            ),
        ]
    dd_max, dd_len = drawdown(eq)
    parts.append(f"<p class='muted'>max drawdown {dd_max:.2%} over {dd_len} sessions</p></body></html>")
    return "".join(parts)


def write(path: str | Path, result: BacktestResult, **kw: object) -> Path:
    p = Path(path)
    p.write_text(render(result, **kw), encoding="utf-8")  # type: ignore[arg-type]
    return p
