"""Which strategies the monitor watches and what their research promised (MN-046, ADR-0045).

Entries come from the execution registry seed (``strategies.json``) and the optimizer's research
deployment file; the expectation comes from the deployment entry's ``monitoring`` block
(``expectation.json`` written by the optimizer, EX-064) or, for hand-written strategies such as the ORB
baseline, from the strategy's ``feedback.json`` entry (MN-026). Training references (features,
out-of-fold predictions, importance ranks) come from the training run (ML-044). The execution mode is
refreshed from ``ExecutionService.ListStrategies``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

log = logging.getLogger("monitor.strategies")


@dataclass
class Expectation:
    session_bps: np.ndarray
    trade_bps: np.ndarray
    slippage_bps_modeled: float = 0.0
    shortfall_bps_modeled: float = 0.0
    backtest_report_id: str = ""
    training_run_id: str = ""
    trades_per_session: float = 0.0


@dataclass
class Reference:
    features: dict[str, np.ndarray]
    predictions: np.ndarray
    labels: np.ndarray
    importance: dict[str, int]
    run_id: str = ""
    feature_set: str = ""


@dataclass
class Monitored:
    strategy_id: str
    short_id: str
    horizon: str
    timeframe: str
    mode: str
    allocation: Decimal
    lineage_id: str
    model_version: str
    entry: dict[str, Any]
    expectation: Expectation | None = None
    reference: Reference | None = None
    instruments: list[str] = field(default_factory=list)


def _read(path: str) -> list[dict[str, Any]]:
    if not path or not Path(path).exists():
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(data.get("strategies", [])) if isinstance(data, dict) else list(data)


def _expectation_from_file(path: str) -> Expectation | None:
    if not path or not Path(path).exists():
        return None
    x = json.loads(Path(path).read_text(encoding="utf-8"))
    sess = np.asarray(x.get("session_returns_bps", []), dtype=np.float64)
    trades = np.asarray(x.get("trade_returns_bps", []), dtype=np.float64)
    return Expectation(
        session_bps=sess,
        trade_bps=trades,
        slippage_bps_modeled=float(x.get("slippage_bps_modeled", 0.0)),
        shortfall_bps_modeled=float(x.get("shortfall_bps_modeled", 0.0)),
        backtest_report_id=str(x.get("backtest_report_id", "")),
        training_run_id=str(x.get("training_run_id", "")),
        trades_per_session=trades.size / sess.size if sess.size else 0.0,
    )


def _expectation_from_feedback(e: dict[str, Any]) -> Expectation | None:
    x = e.get("expectation", {})
    sess = e.get("backtest_session_returns_bps") or x.get("distributions", {}).get("session_bps", [])
    trades = e.get("backtest_trade_returns_bps", [])
    if not sess and not trades:
        return None
    s = np.asarray(sess, dtype=np.float64)
    t = np.asarray(trades, dtype=np.float64)
    return Expectation(
        session_bps=s,
        trade_bps=t,
        slippage_bps_modeled=float(x.get("slippage_bps_modeled", e.get("slippage_bps_modeled", 0.0))),
        shortfall_bps_modeled=float(x.get("shortfall_bps_modeled", e.get("shortfall_bps_modeled", 0.0))),
        backtest_report_id=str(x.get("backtest_report_id", e.get("backtest_report_id", ""))),
        trades_per_session=t.size / s.size if s.size else 0.0,
    )


def load_reference(mon: dict[str, Any]) -> Reference | None:
    """Training-window feature sample, out-of-fold predictions and labels, importance ranks (ML-044)."""
    ref_path, oof_path, imp_path = mon.get("reference", ""), mon.get("oof", ""), mon.get("importance", "")
    if not ref_path or not Path(ref_path).exists():
        return None
    ref = pl.read_parquet(ref_path)
    feats = {c: ref[c].cast(pl.Float64).to_numpy() for c in ref.columns if c not in ("instrument_id", "decision_time")}
    preds = labels = np.zeros(0)
    if oof_path and Path(oof_path).exists():
        oof = pl.read_parquet(oof_path)
        if "pred" in oof.columns:
            keep = oof.filter(pl.col("pred").is_not_null() & pl.col("pred").is_not_nan())
            preds = keep["pred"].to_numpy().astype(np.float64)
            labels = keep["y"].to_numpy().astype(np.float64) if "y" in keep.columns else np.zeros(0)
    imp: dict[str, int] = {}
    if imp_path and Path(imp_path).exists():
        imp = {k: int(v) for k, v in json.loads(Path(imp_path).read_text(encoding="utf-8")).items()}
    return Reference(feats, preds, labels, imp, str(mon.get("training_run_id", "")), str(mon.get("feature_set_id", "")))


def load(seed: str, deployments: str, feedback: str) -> dict[str, Monitored]:
    """Every strategy with a runtime entry, keyed by strategy ID (research deployments never override a
    seed entry, as in strategy-runtime, SM-016)."""
    entries: dict[str, dict[str, Any]] = {}
    for e in _read(seed):
        entries[e["strategy_id"]] = e
    for e in _read(deployments):
        if e.get("strategy_id") not in entries and e.get("mode") in ("shadow", "paper"):
            entries[e["strategy_id"]] = e
    fb = {e["strategy_id"]: e for e in _read(feedback)}
    out: dict[str, Monitored] = {}
    for sid, e in entries.items():
        mon = e.get("monitoring", {})
        rt = e.get("runtime", {})
        f = fb.get(sid, {})
        m = Monitored(
            strategy_id=sid,
            short_id=e.get("short_id", f.get("short_id", "")),
            horizon=e.get("horizon", "intraday"),
            timeframe=rt.get("timeframe", "15m"),
            mode=e.get("mode", "paper"),
            allocation=Decimal(str(e.get("allocation", "0") or "0")),
            lineage_id=mon.get("lineage_id", f.get("lineage_id", sid)),
            model_version=mon.get("model_version", f.get("model_version", e.get("version", ""))),
            entry=e,
            instruments=list(rt.get("instruments", [])),
        )
        try:
            m.expectation = _expectation_from_file(mon.get("expectation", "")) or _expectation_from_feedback(f)
            m.reference = load_reference(mon) if mon else None
        except Exception as exc:  # a broken artifact leaves the strategy listed as insufficient data
            log.warning("expectation not loaded", extra={"strategy": sid, "err": str(exc)[:200]})
        out[sid] = m
    for sid, f in fb.items():  # feedback-only strategies (no runtime entry in this deployment)
        if sid not in out and f.get("short_id"):
            out[sid] = Monitored(
                strategy_id=sid,
                short_id=f["short_id"],
                horizon=f.get("horizon", "intraday"),
                timeframe=f.get("timeframe", "15m"),
                mode=f.get("mode", "paper"),
                allocation=Decimal(str(f.get("allocation", "0") or "0")),
                lineage_id=f.get("lineage_id", sid),
                model_version=f.get("model_version", ""),
                entry={},
                expectation=_expectation_from_feedback(f),
            )
    return out
