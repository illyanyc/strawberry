"""Validation splitters and overfitting statistics (section 08, ADR-0030)."""

from tbcore.validation.splitters import (
    CombinatorialPurgedCV,
    PurgedKFold,
    Split,
    WalkForward,
    purge,
    stitch_walk_forward,
)
from tbcore.validation.stats import (
    EffectiveTrials,
    PBOResult,
    SPAResult,
    bootstrap_ci,
    deflated_sharpe,
    effective_trials,
    expected_max_sharpe,
    min_backtest_length,
    min_track_record_length,
    moments,
    onc_clusters,
    pbo_cscv,
    probabilistic_sharpe,
    romano_wolf,
    sharpe,
    spa_test,
)

# The spec name (section 08 interface).
probability_of_backtest_overfitting = pbo_cscv

__all__ = [
    "CombinatorialPurgedCV",
    "EffectiveTrials",
    "PBOResult",
    "PurgedKFold",
    "SPAResult",
    "Split",
    "WalkForward",
    "bootstrap_ci",
    "deflated_sharpe",
    "effective_trials",
    "expected_max_sharpe",
    "min_backtest_length",
    "min_track_record_length",
    "moments",
    "onc_clusters",
    "pbo_cscv",
    "probabilistic_sharpe",
    "probability_of_backtest_overfitting",
    "purge",
    "romano_wolf",
    "sharpe",
    "spa_test",
    "stitch_walk_forward",
]
