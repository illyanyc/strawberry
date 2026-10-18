"""Vectorized screening tier (BT-006, BT-025) and its consistency with the event-driven tier."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
import pytest

from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.synthetic import random_walk_bars
from tbcore.backtest.vectorized import EntrySignals, SignalFn, VectorData, VectorizedResult, VectorRules, run_vectorized
from tbcore.strategy.orb_vector import orb_signals

from .btkit import NS, ORB_PARAMS, gate_bars, iid, orb_config

SYMS = ["AAPL", "NVDA", "AMD", "XLK", "SPY"]
SCALES = {"AAPL": 1.0, "NVDA": 1.5, "AMD": 0.8, "XLK": 1.2, "SPY": 4.0}
FREE = CostModelConfig(
    broker="none", half_spread_bps=0, open_close_half_spread_bps=0, impact_k=0, fallback_impact_bps=0
)


def one(r: VectorizedResult | list[VectorizedResult]) -> VectorizedResult:
    assert isinstance(r, VectorizedResult)
    return r


def test_bt006_vectorized_orb_matches_the_event_driven_orb() -> None:
    bars = gate_bars(SYMS, SCALES)
    vec = one(
        run_vectorized(
            orb_signals, VectorData.from_bars(bars), CostModelConfig(), VectorRules(allocation=30000), ORB_PARAMS
        )
    )
    cfg = orb_config(
        [iid(s) for s in SYMS],
        datetime(2026, 10, 12, tzinfo=UTC),
        datetime(2026, 10, 17, tzinfo=UTC),
        allowlist=[iid("AAPL")],
    )
    ev = run_event_backtest(cfg, bars)
    opens = ev.intents.filter(pl.col("kind") == "open")
    # Same entries (instrument, decision bar) with the same stop and target levels.
    ev_keys = {
        (r["instrument_id"], r["decision_time"] - 60 * NS, float(r["stop_loss"]), float(r["take_profit"]))
        for r in opens.iter_rows(named=True)
    }
    vec_keys = {(r["instrument_id"], r["entry_time"], r["stop"], r["target"]) for r in vec.trades.iter_rows(named=True)}
    assert len(vec_keys) == len(ev_keys) == 19
    assert {(a, s, t) for a, _, s, t in ev_keys} == {(a, s, t) for a, _, s, t in vec_keys}
    # Same exit reason for every trade (the event tier runs the gate, collars, and compounding).
    e = ev.trades.sort(["instrument_id", "entry_time"])["exit_reason"].to_list()
    v = vec.trades.sort(["instrument_id", "entry_time"])["exit_reason"].to_list()
    assert e == v
    assert vec.known_differences


def _bars(rows: Sequence[tuple[float, float, float, float]]) -> pl.DataFrame:
    opn = int(datetime(2026, 10, 13, 13, 30, tzinfo=UTC).timestamp()) * NS
    return pl.DataFrame(
        [
            (iid("X"), opn + i * 900 * NS, opn + (i + 1) * 900 * NS, opn + (i + 1) * 900 * NS + 60 * NS, *r, 1e5)
            for i, r in enumerate(rows)
        ],
        schema=["instrument_id", "ts_open", "ts_close", "available_at", "open", "high", "low", "close", "volume"],
        orient="row",
    )


def _signal_at(t: int, stop: float, target: float) -> SignalFn:
    def fn(d: VectorData, _: Mapping[str, Any]) -> EntrySignals:
        e = np.zeros_like(d.close, dtype=bool)
        s = np.full_like(d.close, np.nan)
        g = np.full_like(d.close, np.nan)
        e[t, 0], s[t, 0], g[t, 0] = True, stop, target
        return EntrySignals(e, s, g)

    return fn


def test_bt025_next_bar_open_and_gap_through_stop() -> None:
    # Signal on bar 0 (close 101); entry at bar 1's open 100.5; bar 2 gaps below the 99 stop -> 98.
    d = VectorData.from_bars(
        _bars([(100, 101, 99.5, 101), (100.5, 101, 100, 100.8), (98, 98.5, 97, 97.5), (97.5, 98, 97, 97.8)])
    )
    r = one(run_vectorized(_signal_at(0, 99.0, 110.0), d, FREE, VectorRules(allocation=10_000, risk_per_trade=0.01)))
    (t,) = r.trades.iter_rows(named=True)
    assert t["entry_price"] == 100.5 and t["exit_price"] == 98.0 and t["exit_reason"] == "stop"
    assert t["qty"] == np.floor(10_000 * 0.01 / 1.5)


def test_bt025_stop_first_and_target_at_open() -> None:
    # Bar 2 touches both the stop and the target: the stop wins. A gap above the target fills at the open.
    rows = [(100, 101, 99.5, 101), (100.5, 101, 100, 100.8), (100.8, 106, 98, 100), (100, 100, 100, 100)]
    r = one(
        run_vectorized(
            _signal_at(0, 99.0, 105.0), VectorData.from_bars(_bars(rows)), FREE, VectorRules(allocation=10_000)
        )
    )
    assert r.trades["exit_reason"].to_list() == ["stop"]
    rows = [(100, 101, 99.5, 101), (100.5, 101, 100, 100.8), (106, 107, 105.5, 106), (100, 100, 100, 100)]
    r = one(
        run_vectorized(
            _signal_at(0, 99.0, 105.0), VectorData.from_bars(_bars(rows)), FREE, VectorRules(allocation=10_000)
        )
    )
    assert r.trades.row(0, named=True)["exit_price"] == 106 and r.trades["exit_reason"].to_list() == ["take_profit"]


@pytest.mark.perf
@pytest.mark.skipif(
    os.environ.get("TB_PERF") != "1", reason="performance target; run with TB_PERF=1 (make perf-backtest)"
)
def test_bt006_perf_vectorized_1000_parameter_sets_under_5_minutes() -> None:
    d = VectorData.from_bars(random_walk_bars(100, 252, 5, seed=1))
    grid = [
        {**ORB_PARAMS, "opening_bars": 1 + i % 6, "target_r": str(1 + (i % 10) / 4), "stop_buffer_bps": str(i % 17)}
        for i in range(1000)
    ]
    t0 = time.perf_counter()
    out = run_vectorized(
        orb_signals, d, CostModelConfig(), VectorRules(allocation=1_000_000, risk_per_trade=0.0005), grid
    )
    elapsed = time.perf_counter() - t0
    print(f"BT-006 perf: 1000 parameter sets on {d.close.size:,} cells in {elapsed:.1f} s")
    assert isinstance(out, list) and len(out) == 1000
    assert elapsed < 300
