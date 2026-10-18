"""Leakage suite (BT-060..BT-065): passes for the ORB baseline, fails for a leaky fixture."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from tbcore.backtest.config import BacktestConfig
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.leakage import random_entry_baseline, random_walk_test, run_leakage_suite, shuffled_label_test
from tbcore.validation.splitters import PurgedKFold

from . import leaky
from .btkit import gate_bars, iid, orb_config

SYMS = ["AAPL", "NVDA", "AMD", "XLK", "SPY"]
SCALES = {"AAPL": 1.0, "NVDA": 1.5, "AMD": 0.8, "XLK": 1.2, "SPY": 4.0}
START, END = datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 24, tzinfo=UTC)


def leaky_config() -> BacktestConfig:
    cfg = orb_config([iid(s) for s in SYMS], START, END, allocation="30000")
    spec = cfg.strategies[0].model_copy(
        update={"strategy_id": "peek", "short_id": "peeknxt", "runtime_class": "peek_next", "params": {}}
    )
    return cfg.model_copy(update={"strategies": (spec,)})


def test_bt065_suite_passes_for_the_orb_baseline() -> None:
    bars = gate_bars(SYMS, SCALES, n_sessions=10)
    rep = run_leakage_suite(orb_config([iid(s) for s in SYMS], START, END, allowlist=[iid("AAPL")]), bars)
    assert rep.passed, rep.summary()
    assert [c.name for c in rep.checks] == ["future_shift", "random_walk", "timestamp_assertion"]


def test_bt065_suite_fails_for_a_leaky_strategy() -> None:
    bars = gate_bars(SYMS, SCALES, n_sessions=10)
    rep = run_leakage_suite(leaky_config(), bars, prepare=leaky.peek_from)
    checks = {c.name: c for c in rep.checks}
    assert not rep.passed
    assert not checks["future_shift"].passed, checks["future_shift"].detail
    assert not checks["random_walk"].passed, checks["random_walk"].detail
    assert checks["random_walk"].detail.startswith("t-stat"), checks["random_walk"].detail
    assert checks["timestamp_assertion"].passed


def test_bt061_random_walk_with_too_few_trades_is_inconclusive_and_fails() -> None:
    cfg = orb_config([iid(s) for s in SYMS], START, END)
    check = random_walk_test(cfg, n_instruments=1, n_sessions=3)
    assert not check.passed
    assert check.detail.startswith("inconclusive"), check.detail


def _ridge(lam: float, leak: bool):  # type: ignore[no-untyped-def]
    def fit_predict(x, y, x_all, train, test):  # type: ignore[no-untyped-def]
        rows = np.concatenate([train, test]) if leak else train  # the leak: test labels in the fit
        a = x[rows]
        w = np.linalg.solve(a.T @ a + lam * np.eye(a.shape[1]), a.T @ y[rows])
        return x_all[test] @ w

    return fit_predict


def test_bt062_shuffled_labels_score_zero_unless_the_pipeline_leaks() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(300, 400))
    y = x[:, 0] + rng.normal(size=300)
    t0 = np.arange(300, dtype=np.int64)
    splits = list(PurgedKFold(5, embargo=2, label_end_times=t0 + 2).split(t0))
    assert shuffled_label_test(x, y, _ridge(1.0, leak=False), splits).passed
    leaked = shuffled_label_test(x, y, _ridge(1.0, leak=True), splits)
    assert not leaked.passed and leaked.value > 0.5


def test_bt063_random_entry_baseline_percentile() -> None:
    bars = gate_bars(SYMS, SCALES, n_sessions=10)
    r = run_event_backtest(orb_config([iid(s) for s in SYMS], START, END, allowlist=[iid("AAPL")]), bars)
    base = random_entry_baseline(r, bars, n_draws=200)
    assert 0.0 <= base["percentile"] <= 1.0 and base["draws"] == 200


@pytest.fixture(autouse=True)
def _clear_future() -> None:
    leaky.FUTURE.clear()
