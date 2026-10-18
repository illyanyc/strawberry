"""Point-in-time lint for feature code (BT-064)."""

from __future__ import annotations

from pathlib import Path

from tbcore.features.lint import lint_file, lint_registry, lint_source, main

LEAKY = """
import polars as pl
import requests
from tbcore.features.core import feature


@feature("fwd_ret", 1, ["close"], lookback=2)
def fwd_ret():
    return pl.col("close").shift(-1) / pl.col("close")


@feature("centered", 1, ["close"], lookback=5)
def centered():
    return pl.col("close").rolling_mean(5, center=True)


@feature("zscore_full", 1, ["close"], lookback=1)
def zscore_full():
    return (pl.col("close") - pl.col("close").mean()) / pl.col("close").std()


@feature("bfilled", 1, ["close"], lookback=1)
def bfilled():
    return pl.col("close").fill_null(strategy="backward")


@feature("event_joined", 1, ["close"], lookback=1)
def event_joined():
    return pl.col("event_time")


def peek_vendor():
    return requests.get("https://example.invalid")
"""


def test_bt064_registered_features_are_clean() -> None:
    assert lint_registry() == []


def test_bt064_leaky_generated_code_is_flagged(tmp_path: Path) -> None:
    f = tmp_path / "generated_features.py"
    f.write_text(LEAKY)
    rules = sorted({x.rule for x in lint_file(f)})
    assert rules == ["PIT001", "PIT002", "PIT003", "PIT004", "PIT006", "PIT007"]
    assert main([str(f)]) == 1


def test_bt064_causal_forms_and_exemptions_pass() -> None:
    ok = """
def causal():
    x = pl.col("close")
    return (x - x.rolling_mean(20)) / x.rolling_std(20) + x.cum_sum() + x.shift(1) + x.ewm_mean(span=5)
"""
    assert lint_source(ok) == []
    assert lint_source("def xs():\n    return pl.col('r').rank() / pl.col('r').count()\n", kind="cross_sectional") == []
    exempt = "def f():\n    return pl.col('close').mean()  # pit: ok constant scale on a single-session frame\n"
    assert lint_source(exempt) == []
    assert lint_source("def f():\n    return pl.col('close').mean()  # pit: ok\n") != []  # a reason is required
    assert [x.rule for x in lint_source("def f(df):\n    return scaler.fit_transform(df)\n")] == ["PIT008"]
    assert [x.rule for x in lint_source("def f(a, b):\n    return a.join_asof(b, on='t', strategy='forward')\n")] == [
        "PIT005"
    ]
