"""Reports and tearsheet (BT-015, BT-050, BT-051)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import pytest

from tbcore.backtest import report, tearsheet
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.result import BacktestResult

from .btkit import gate_bars, iid, orb_config

SYMS = ["AAPL", "NVDA", "AMD", "XLK", "SPY"]
SCALES = {"AAPL": 1.0, "NVDA": 1.5, "AMD": 0.8, "XLK": 1.2, "SPY": 4.0}


@pytest.fixture(scope="module")
def runs() -> tuple[BacktestResult, BacktestResult]:
    bars = gate_bars(SYMS, SCALES, n_sessions=10)
    cfg = orb_config(
        [iid(s) for s in SYMS],
        datetime(2026, 10, 12, tzinfo=UTC),
        datetime(2026, 10, 31, tzinfo=UTC),
        allowlist=[iid("AAPL")],
    )
    return run_event_backtest(cfg, bars), run_event_backtest(
        cfg.model_copy(update={"cost_model": cfg.cost_model.stressed()}), bars
    )


def test_bt050_ratios_use_session_returns(runs: tuple[BacktestResult, BacktestResult]) -> None:
    one, _ = runs
    r = report.session_returns(one)
    assert r.height == one.daily.height == 15  # sessions 2026-10-12..30, flat days included
    m = report.metrics(one)
    ret = r["ret"].to_numpy()
    assert m["sharpe"] == pytest.approx(ret.mean() / ret.std(ddof=1) * math.sqrt(252))
    assert m["trades"] == one.trades.height
    assert m["net_pnl"] == pytest.approx(float(one.trades["pnl"].sum()))
    assert 0 <= m["max_drawdown"] < 1 and m["psr"] == m["dsr"]  # one trial: DSR is PSR
    deflated = report.metrics(one, n_trials=100, sr_variance=0.002)
    assert deflated["dsr"] < m["dsr"]
    for k in (
        "sortino",
        "calmar",
        "turnover",
        "cost_drag",
        "exposure",
        "capacity_notional",
        "profit_factor",
        "hit_rate",
        "min_track_record_sessions",
    ):
        assert k in m


def test_bt015_cost_stress_is_reported(runs: tuple[BacktestResult, BacktestResult]) -> None:
    one, two = runs
    cs = report.cost_stress(one, two)
    assert cs["net_pnl_2x"] < cs["net_pnl_1x"]
    assert cs["positive_at_2x"] in (0.0, 1.0)


def test_bt051_regimes_microcaps_and_per_date_ic(runs: tuple[BacktestResult, BacktestResult]) -> None:
    one, _ = runs
    labels = pl.DataFrame(
        {
            "date": [date(2026, 10, d) for d in range(12, 31)],
            "label": ["calm" if d % 2 else "volatile" for d in range(12, 31)],
        }
    )
    br = report.regime_breakdown(one, {"vix": labels})
    assert {"time_of_day", "weekday", "year", "vix"} <= set(br)
    assert int(br["year"]["trades"].sum()) == one.trades.height == int(br["vix"]["trades"].sum())
    split = report.microcap_split(one, [iid("AMD")])
    assert split["all"]["trades"] > split["ex_microcaps"]["trades"]
    rng = np.random.default_rng(1)
    dates = [date(2026, 1, 1 + i) for i in range(20) for _ in range(10)]
    insts = [f"i{j}" for _ in range(20) for j in range(10)]
    score = rng.normal(size=200)
    scores = pl.DataFrame({"date": dates, "instrument_id": insts, "score": score})
    realized = pl.DataFrame({"date": dates, "instrument_id": insts, "ret": score + rng.normal(scale=0.5, size=200)})
    ic = report.per_date_ic(scores, realized)
    assert ic["ic_mean"] > 0.5 and ic["dates"] == 20


def test_tearsheet_renders_without_scripts(runs: tuple[BacktestResult, BacktestResult], tmp_path: object) -> None:
    one, two = runs
    page = tearsheet.render(one, stressed=two, overfitting={"dsr": 0.5, "pbo": 0.3})
    assert page.startswith("<!doctype html>") and "<script" not in page and "http" not in page.split("<body>")[1]
    for section in (
        "Warnings",
        "Equity",
        "Drawdown",
        "Metrics",
        "Cost stress",
        "Overfitting statistics",
        "Monthly returns",
        "By time of day",
        "Trades",
    ):
        assert section in page
    assert "survivorship <b>unknown</b>" in page
