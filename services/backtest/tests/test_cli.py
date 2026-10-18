"""Backtest service CLI: run a config on bars and write artifacts with a tearsheet (section 08)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from tb_backtest.__main__ import main

from tbcore.backtest.config import AccountSimConfig, BacktestConfig, StrategySpec, UniverseRef
from tbcore.backtest.synthetic import random_walk_bars

ORB = {
    "opening_bars": 1,
    "risk_per_trade": "0.005",
    "target_r": "2",
    "stop_buffer_bps": "5",
    "exit_minutes_before_close": 15,
}


def test_run_writes_artifacts_tearsheet_and_stress(tmp_path: Path) -> None:
    bars = random_walk_bars(5, 10, 15, seed=3)
    ids = tuple(sorted(bars["instrument_id"].unique().to_list()))
    bars.write_parquet(tmp_path / "bars.parquet")
    cfg = BacktestConfig(
        strategies=(
            StrategySpec(
                strategy_id="orb",
                short_id="orbclia",
                allocation=Decimal(30000),
                runtime_class="orb",
                instruments=ids,
                params=ORB,
            ),
        ),
        universe=UniverseRef(name="rw", instruments=ids),
        start=datetime(2025, 1, 1, tzinfo=UTC),
        end=datetime(2025, 2, 1, tzinfo=UTC),
        account=AccountSimConfig(cash=Decimal(60000)),
    )
    (tmp_path / "cfg.json").write_text(cfg.model_dump_json())
    out = tmp_path / "out"
    assert (
        main(
            [
                "run",
                "--config",
                str(tmp_path / "cfg.json"),
                "--bars",
                str(tmp_path / "bars.parquet"),
                "--out",
                str(out),
                "--stress",
            ]
        )
        == 0
    )
    run_dir = out / cfg.config_hash[:16]
    for name in (
        "config.json",
        "trades.parquet",
        "fills.parquet",
        "daily.parquet",
        "summary.json",
        "run.binpb",
        "tearsheet.html",
        "report.json",
    ):
        assert (run_dir / name).exists(), name
    rep = json.loads((run_dir / "report.json").read_text())
    assert rep["metrics"]["sessions"] == 20 and "cost_stress" in rep  # XNYS sessions 2025-01-02..31 (Jan 9 closed)
    assert main(["leakage", "--config", str(tmp_path / "cfg.json"), "--bars", str(tmp_path / "bars.parquet")]) == 0
