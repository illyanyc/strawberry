"""Event-driven evaluation of model decisions (BT-006, EX-003, EX-051): the out-of-fold (or holdout)
decision table drives the live ``model_signal`` / ``meta_filter`` strategy on the event-driven tier
(``tb-simcore``: the real OMS, gate, and ledger core) at 1x and 2x costs.
"""

from __future__ import annotations

import itertools
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from tbcore.backtest.config import AccountSimConfig, BacktestConfig, StrategySpec, UniverseRef
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.report import drawdown, session_returns
from tbcore.backtest.result import BacktestResult

EPOCH = datetime(1970, 1, 1, tzinfo=UTC).date()


@dataclass
class EventEvaluation:
    days: np.ndarray  # session days (since epoch)
    rets: np.ndarray
    rets_2x: np.ndarray
    trades: int
    net_pnl_2x: float
    max_drawdown: float
    warnings: list[str]
    result: BacktestResult


def strategy_spec(
    family_task: str,
    timeframe: str,
    instruments: list[str],
    benchmark: str,
    params: dict[str, Any],
    allocation: Decimal,
    strategy_id: str = "ml-eval",
    short_id: str = "mleval1",
) -> StrategySpec:
    swing = timeframe == "1d"
    cls = "meta_filter" if family_task == "rule_filter" else "model_signal"
    return StrategySpec(
        strategy_id=strategy_id,
        short_id=short_id,
        name=strategy_id,
        horizon="swing" if swing else "intraday",
        allocation=allocation,
        runtime_class=cls,
        timeframe=timeframe,
        instruments=tuple([*instruments, benchmark]),
        params={**params, "benchmark": benchmark, "swing": swing},
    )


def next_open_decisions(bars: pl.DataFrame) -> pl.DataFrame:
    """Daily bars become available at the next session's open + 60 s (BT-026): swing decisions made on
    a session's daily bar are submitted at the next open, when the gate accepts orders (RM-003
    market session). Later availability than the bar's own finality, so never look-ahead; the last
    session (no next open in the data) is dropped."""
    opens = np.sort(bars["ts_open"].unique().to_numpy()).astype(np.int64)
    nxt = {int(a): int(b) for a, b in itertools.pairwise(opens)}
    return (
        bars.filter(pl.col("ts_open").is_in(list(nxt)))
        .with_columns(pl.col("ts_open").replace_strict(nxt, return_dtype=pl.Int64).alias("_next"))
        .with_columns((pl.col("_next") + 60 * 1_000_000_000).alias("available_at"))
        .drop("_next")
    )


def evaluate_event(
    decisions: pl.DataFrame,
    bars: pl.DataFrame,
    spec: StrategySpec,
    start: int,
    end: int,
    cost: CostModelConfig | None = None,
    survivorship: str = "unknown",
    snapshot_id: str = "",
) -> EventEvaluation:
    """Run ``spec`` with ``decisions`` (instrument_id, ts_open, score, take) between ``start`` and ``end``."""
    cost = cost or CostModelConfig()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scores.parquet"
        decisions.select("instrument_id", "ts_open", "score", "take").write_parquet(path)
        s = spec.model_copy(update={"params": {**spec.params, "scores": str(path)}})
        alloc = s.allocation
        cfg = BacktestConfig(
            strategies=(s,),
            universe=UniverseRef(name="ml-eval", instruments=s.instruments, survivorship=survivorship),  # type: ignore[arg-type]
            timeframe=s.timeframe,
            start=datetime.fromtimestamp(start / 1e9, tz=UTC),
            end=datetime.fromtimestamp(end / 1e9, tz=UTC),
            data_snapshot_id=snapshot_id,
            cost_model=cost,
            account=AccountSimConfig(cash=alloc * 2),
            min_trades=0,
        )
        b = bars.filter(pl.col("instrument_id").is_in(list(s.instruments)))
        if s.timeframe == "1d":
            b = next_open_decisions(b)
        one = run_event_backtest(cfg, b)
        two = run_event_backtest(cfg.model_copy(update={"cost_model": cost.stressed(2.0)}), b)
    r1, r2 = session_returns(one), session_returns(two)
    days = np.array([(d - EPOCH).days for d in r1["date"].str.to_date().to_list()], dtype=np.int64)
    eq = r1["equity"].to_numpy().astype(np.float64)
    dd, _ = drawdown(eq) if eq.size else (0.0, 0)
    net2 = float(r2["equity"].to_numpy()[-1] - float(alloc)) if r2.height else 0.0
    return EventEvaluation(
        days=days,
        rets=r1["ret"].to_numpy().astype(np.float64),
        rets_2x=r2["ret"].to_numpy().astype(np.float64),
        trades=int(one.trades.height),
        net_pnl_2x=net2,
        max_drawdown=dd,
        warnings=list(one.warnings),
        result=one,
    )
