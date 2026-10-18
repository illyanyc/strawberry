"""Backtest results and their filesystem artifacts (D8: filesystem artifact store, ADR-0031)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from tb.ml.v1 import ml_pb2
from tbcore.backtest.config import BacktestConfig


@dataclass
class BacktestResult:
    """One run: every intent (rejections included), order, fill, trade, the equity curves, warnings."""

    config: BacktestConfig
    engine: str
    intents: pl.DataFrame
    orders: pl.DataFrame
    fills: pl.DataFrame
    trades: pl.DataFrame
    equity: pl.DataFrame  # per decision time and strategy
    daily: pl.DataFrame  # per session and strategy (ledger-core valuation)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def config_hash(self) -> str:
        return self.config.config_hash

    def fingerprint(self) -> str:
        """Hash of the result tables (determinism check, BT-001)."""
        h = hashlib.sha256()
        for df in (self.intents, self.orders, self.fills, self.trades, self.equity, self.daily):
            h.update(df.write_csv().encode())
        return h.hexdigest()

    def run_record(self, run_id: str, artifact_uri: str = "") -> ml_pb2.BacktestRun:
        r = ml_pb2.BacktestRun(
            run_id=run_id,
            strategy_config_hash=self.config_hash,
            data_snapshot_id=self.config.data_snapshot_id,
            cost_model=self.config.cost_model.model_dump_json(),
            trades_artifact_uri=artifact_uri,
            code_commit=self.config.code_commit,
            seed=self.config.seed,
            survivorship_status=self.config.universe.survivorship,
            warnings=self.warnings,
            engine=self.engine,
            fill_model=self.config.fill_model.model_dump_json(),
        )
        for k, v in self.metrics.items():
            r.metrics[k] = float(v)
        return r

    def save(self, directory: str | Path) -> Path:
        """Write the run's artifacts; the directory is named by the config hash."""
        d = Path(directory) / self.config_hash[:16]
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(self.config.canonical_json(), encoding="utf-8")
        for name in ("intents", "orders", "fills", "trades", "equity", "daily"):
            getattr(self, name).write_parquet(d / f"{name}.parquet")
        (d / "summary.json").write_text(
            json.dumps(
                {"warnings": self.warnings, "stats": self.stats, "metrics": self.metrics}, indent=2, default=str
            ),
            encoding="utf-8",
        )
        (d / "run.binpb").write_bytes(self.run_record(self.config_hash[:16], str(d)).SerializeToString())
        return d
