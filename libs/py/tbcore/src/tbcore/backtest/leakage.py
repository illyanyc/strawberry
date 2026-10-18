"""Leakage test suite (section 08, BT-060..BT-065; N2, DF-012).

Each check targets one way future information reaches a decision:

* future shift (BT-060): replacing every bar after a cutoff must not change any decision taken up
  to the cutoff; a strategy that reads ahead (closures over full data, centered windows, full-sample
  normalization) changes them;
* random walk (BT-061): on martingale prices no causal strategy earns a significant gross profit;
* timestamp assertion (BT-004): the engine refuses data available before it closes;
* shuffled labels (BT-062): a pipeline trained and evaluated out of sample on shuffled labels must
  score about zero;
* random-entry baseline (BT-063): the strategy's percentile against random entries with the same
  number of trades and holding periods (reported, not a leakage verdict).

The point-in-time lint for feature code (BT-064) is ``tbcore.features.lint``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
import polars as pl
from numpy.typing import NDArray

from tbcore.backtest.config import BacktestConfig, UniverseRef
from tbcore.backtest.costs import CostModelConfig
from tbcore.backtest.event import LeakageError, run_event_backtest
from tbcore.backtest.result import BacktestResult
from tbcore.backtest.synthetic import random_walk_bars
from tbcore.validation.splitters import Split

NS = 1_000_000_000
GROSS = CostModelConfig(
    broker="none", half_spread_bps=0, open_close_half_spread_bps=0, impact_k=0, fallback_impact_bps=0
)


@dataclass(frozen=True)
class LeakageCheck:
    name: str
    passed: bool
    value: float
    detail: str


@dataclass
class LeakageReport:
    checks: list[LeakageCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def summary(self) -> str:
        return "; ".join(f"{c.name} {'ok' if c.passed else 'FAIL'} ({c.detail})" for c in self.checks)


def _decisions(r: BacktestResult, until: int) -> list[tuple[object, ...]]:
    d = r.intents.filter(pl.col("decision_time") <= until).sort(["decision_time", "intent_id"])
    return list(
        d.select(
            "intent_id", "instrument_id", "side", "kind", "qty", "reference_price", "stop_loss", "take_profit"
        ).iter_rows()
    )


Prepare = Callable[[pl.DataFrame], None]


def future_shift_test(
    config: BacktestConfig, bars: pl.DataFrame, cutoff: int | None = None, seed: int = 0, prepare: Prepare | None = None
) -> LeakageCheck:
    """BT-060: decisions up to ``cutoff`` (default: 60 % through the data) with the original bars versus
    with every bar after the cutoff perturbed. ``prepare`` is the strategy's offline step over the run's
    bars (feature or score materialization); it runs on exactly the bars of each run, so a feature that
    reads ahead changes decisions before the cutoff."""
    av = bars["available_at"].to_numpy()
    t_cut = int(cutoff if cutoff is not None else np.quantile(av, 0.6))
    after = bars.filter(pl.col("available_at") > t_cut)
    rng = np.random.default_rng(seed)
    noise = np.exp(rng.normal(0, 0.01, after.height))
    replaced = after.with_columns(
        (pl.col(c) * pl.Series(noise)).round(2).alias(c) for c in ("open", "high", "low", "close")
    ).with_columns(
        pl.max_horizontal("open", "high", "close").alias("high"), pl.min_horizontal("open", "low", "close").alias("low")
    )
    shifted = pl.concat([bars.filter(pl.col("available_at") <= t_cut), replaced.select(bars.columns)])
    if prepare is not None:
        prepare(bars)
    a = _decisions(run_event_backtest(config, bars), t_cut)
    if prepare is not None:
        prepare(shifted)
    b = _decisions(run_event_backtest(config, shifted), t_cut)
    changed = sum(1 for x, y in zip(a, b, strict=False) if x != y) + abs(len(a) - len(b))
    return LeakageCheck(
        "future_shift", changed == 0, float(changed), f"{changed} of {len(a)} decisions before the cutoff changed"
    )


def random_walk_test(
    config: BacktestConfig,
    n_instruments: int = 20,
    n_sessions: int = 60,
    timeframe_min: int = 15,
    seed: int = 7,
    t_max: float = 3.0,
    prepare: Prepare | None = None,
    vol: float = 0.008,
    min_trades: int = 30,
) -> LeakageCheck:
    """BT-061: run the strategy (gross of costs) on martingale prices; a mean trade P&L with a
    t-statistic above ``t_max`` is impossible without look-ahead. Fewer than ``min_trades``
    trades is inconclusive and fails."""
    bars = random_walk_bars(n_instruments, n_sessions, timeframe_min, seed=seed, vol=vol)
    if prepare is not None:
        prepare(bars)
    ids = sorted(bars["instrument_id"].unique().to_list())
    first = datetime.fromtimestamp(int(bars["ts_open"].min()) / NS, tz=UTC)  # type: ignore[arg-type]
    last = datetime.fromtimestamp(int(bars["ts_open"].max()) / NS + 86_400, tz=UTC)  # type: ignore[arg-type]
    strategies = tuple(
        s.model_copy(update={"instruments": tuple(ids), "segregation_allowlist": ()}) for s in config.strategies
    )
    cfg = config.model_copy(
        update={
            "strategies": strategies,
            "universe": UniverseRef(name="random-walk", instruments=tuple(ids)),
            "start": first,
            "end": last,
            "cost_model": GROSS,
            "account": config.account.model_copy(update={"manual_positions": ()}),
            # A martingale produces loss streaks; the live consecutive-loss halt would end the
            # sample after a few trades, so this diagnostic (synthetic data, no broker) lifts it.
            "risk_limits": {**config.risk_limits, "max_consecutive_losses": "1000000"},
        }
    )
    pnl = run_event_backtest(cfg, bars).trades["gross_pnl"].to_numpy()
    if pnl.size < min_trades:
        return LeakageCheck(
            "random_walk",
            False,
            float(pnl.size),
            f"inconclusive: only {pnl.size} trades on random-walk prices (need {min_trades})",
        )
    t = float(pnl.mean() / (pnl.std(ddof=1) / math.sqrt(pnl.size))) if pnl.std(ddof=1) > 0 else math.inf
    return LeakageCheck(
        "random_walk",
        t <= t_max,
        t,
        f"t-stat {t:.2f} of mean gross trade P&L over {pnl.size} random-walk trades (limit {t_max})",
    )


def timestamp_assertion_test(config: BacktestConfig, bars: pl.DataFrame) -> LeakageCheck:
    """BT-004: bars marked available before they close must be refused by the engine."""
    early = bars.with_columns((pl.col("ts_close") - NS).alias("available_at"))
    try:
        run_event_backtest(config, early)
    except LeakageError as e:
        return LeakageCheck("timestamp_assertion", True, 1.0, f"engine refused early data: {e}")
    return LeakageCheck("timestamp_assertion", False, 0.0, "engine accepted bars available before their close")


FitPredict = Callable[
    [NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.int64], NDArray[np.int64]],
    NDArray[np.float64],
]


def shuffled_label_test(
    features: NDArray[np.float64],
    labels: NDArray[np.float64],
    fit_predict: FitPredict,
    splits: Sequence[Split],
    seed: int = 0,
    max_ic: float = 0.05,
) -> LeakageCheck:
    """BT-062: shuffle the labels, train and predict out of sample with ``fit_predict(X, y, ...)``
    (called with the full arrays and each split's train/test indices), and require |IC| <= ``max_ic``."""
    rng = np.random.default_rng(seed)
    y = labels.copy()
    rng.shuffle(y)
    preds = np.full(y.shape, np.nan)
    for s in splits:
        preds[s.test] = fit_predict(features, y, features, s.train, s.test)
    ok = np.isfinite(preds)
    ranks_p = np.argsort(np.argsort(preds[ok]))
    ranks_y = np.argsort(np.argsort(y[ok]))
    ic = float(np.corrcoef(ranks_p, ranks_y)[0, 1]) if ok.sum() > 2 else 0.0
    return LeakageCheck(
        "shuffled_labels", abs(ic) <= max_ic, ic, f"out-of-sample IC {ic:.3f} on shuffled labels (limit {max_ic})"
    )


def random_entry_baseline(
    result: BacktestResult, bars: pl.DataFrame, n_draws: int = 500, seed: int = 0
) -> dict[str, float]:
    """BT-063: net P&L percentile of the strategy against random entries with the same number of
    trades, holding periods (in bars), and quantities, priced at the same costs (next-bar opens)."""
    trades = result.trades
    if trades.height == 0:
        return {"percentile": math.nan, "draws": 0.0}
    b = bars.sort(["instrument_id", "ts_open"])
    rng = np.random.default_rng(seed)
    by_inst = {k: g for k, g in b.partition_by("instrument_id", as_dict=True).items()}
    keys = list(by_inst)
    hold = ((trades["exit_time"] - trades["entry_time"]) // NS // 60).to_numpy()
    qty = trades["qty"].to_numpy()
    cost = (float(trades["fees"].sum()) + float(trades["slippage_cost"].sum())) / trades.height
    outcomes = np.empty(n_draws)
    for k in range(n_draws):
        total = 0.0
        for h, q in zip(hold, qty, strict=True):
            g = by_inst[keys[rng.integers(len(keys))]]
            o = g["open"].to_numpy()
            ts = g["ts_open"].to_numpy()
            if o.size < 3:
                continue
            i = int(rng.integers(0, o.size - 2))
            j = min(int(np.searchsorted(ts, ts[i] + int(h) * 60 * NS)), o.size - 1)
            total += q * (o[j] - o[i]) - cost
        outcomes[k] = total
    actual = float(trades["pnl"].sum())
    return {
        "percentile": float((outcomes < actual).mean()),
        "draws": float(n_draws),
        "actual": actual,
        "median_random": float(np.median(outcomes)),
    }


def run_leakage_suite(config: BacktestConfig, bars: pl.DataFrame, prepare: Prepare | None = None) -> LeakageReport:
    """BT-065: the strategy-level checks run in CI for every strategy change."""
    return LeakageReport(
        [
            future_shift_test(config, bars, prepare=prepare),
            random_walk_test(config, prepare=prepare),
            timestamp_assertion_test(config, bars),
        ]
    )
