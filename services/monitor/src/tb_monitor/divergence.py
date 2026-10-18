"""Daily live-vs-backtest divergence from the recorder's session files (MN-037, ADR-0045, ADR-0046).

The closed session (plus ``warmup`` earlier sessions, so strategies with lookbacks see the same history
the live runtime had) is replayed through the event-driven backtester with the strategy's registry entry
and the modeled fills (``tbcore.parity.compare_recording``, BT-071). Only intents decided in the target
session are compared: matched, missed (live only), extra (replay only), decision mismatches, and the
slippage of matched legs versus the model. P&L is reported in basis points of allocation (MN-021).
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from tb.monitor.v1 import feedback_pb2
from tb.monitor.v1 import monitor_pb2 as mp
from tb_monitor.recorder import SessionRecorder, iter_sessions
from tbcore.backtest.config import AccountSimConfig, BacktestConfig, FillModelConfig, StrategySpec, UniverseRef
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.recording import Recording
from tbcore.parity import compare_recording

NS = 1_000_000_000


def parity_config(entry: dict[str, Any], start: datetime, end: datetime) -> BacktestConfig:
    rt = entry.get("runtime", {})
    alloc = Decimal(str(entry.get("allocation", "0") or "0"))
    spec = StrategySpec(
        strategy_id=entry["strategy_id"],
        short_id=entry["short_id"],
        name=entry.get("name", entry["strategy_id"]),
        horizon=entry.get("horizon", "intraday"),
        broker_account_id=entry.get("broker_account_id", "sim-paper"),
        allocation=alloc,
        allow_short=bool(entry.get("allow_short", False)),
        segregation_allowlist=tuple(entry.get("segregation_allowlist", [])),
        version=str(entry.get("version", "1")),
        runtime_class=rt["class"],
        timeframe=rt.get("timeframe", "15m"),
        instruments=tuple(rt.get("instruments", [])),
        params=dict(rt.get("params", {})),
        limits={k: str(v) for k, v in entry.get("limits", {}).items()},
    )
    return BacktestConfig(
        strategies=(spec,),
        universe=UniverseRef(name=f"live-{spec.strategy_id}", instruments=spec.instruments),
        timeframe=spec.timeframe,
        start=start,
        end=end,
        account=AccountSimConfig(broker_account_id=spec.broker_account_id, cash=alloc * 2),
        fill_model=FillModelConfig(mode="model"),
        min_trades=0,
    )


def _day(d: str) -> tuple[int, int]:
    s = datetime.fromisoformat(d).replace(tzinfo=UTC)
    return int(s.timestamp() * NS), int((s + timedelta(days=1)).timestamp() * NS)


def run(
    entry: dict[str, Any],
    recorder: SessionRecorder,
    target: str,
    warmup: int = 0,
    slippage_modeled_bps: float = 0.0,
) -> mp.DivergenceReport:
    """Divergence report for ``target`` (YYYY-MM-DD) from the recorder's files."""
    sid = entry["strategy_id"]
    dates = [d for d in recorder.sessions() if d <= target][-(warmup + 1) :]
    rep = mp.DivergenceReport(
        report_id="dv-" + hashlib.sha256(f"{sid}|{target}".encode()).hexdigest()[:20],
        strategy_id=sid,
        session_date=target,
        recording_uri=str(recorder.path(target)),
        generated_at=int(datetime.now(tz=UTC).timestamp() * NS),
    )
    if target not in dates:
        return rep
    rep.recording_complete = all(recorder.manifest(d).get("complete", False) for d in dates)
    rec = Recording.from_messages(iter_sessions(recorder.dir, dates))
    start = datetime.fromisoformat(dates[0]).replace(tzinfo=UTC)
    end = datetime.fromisoformat(target).replace(tzinfo=UTC) + timedelta(days=1)
    cfg = parity_config(entry, start, end)
    res = run_event_backtest(cfg, recording=rec)
    par = compare_recording(rec, cfg, res)
    lo, hi = _day(target)
    live = {
        it.intent_id for it in rec.intents if it.strategy_id == sid and lo <= (it.decision_time or it.created_at) < hi
    }
    bt_time = {r["intent_id"]: int(r["decision_time"] or 0) for r in res.intents.iter_rows(named=True)}
    matched = [i for i in par.matched_intents if i in live]
    missed = [i for i in par.missed_intents if i in live]
    extra = [i for i in par.extra_intents if lo <= bt_time.get(i, 0) < hi]
    mism = [m for m in par.decision_mismatches if m.split(":", 1)[0] in live]
    slips = [
        -float(leg["slippage_bps"])
        for leg in par.legs
        if leg.get("intent_id") in live and leg.get("slippage_bps") is not None
    ]
    gap = float(np.mean(slips)) if slips else 0.0
    alloc = float(cfg.strategies[0].allocation) or 1.0
    total = len(matched) + len(missed) + len(extra)
    rep.matched_intents, rep.missed_intents, rep.extra_intents = len(matched), len(missed), len(extra)
    rep.decision_mismatches = len(mism)
    rep.parity.CopyFrom(
        feedback_pb2.ParitySummary(
            trade_match_rate=len(matched) / total if total else 1.0,
            missed_trades=len(missed),
            extra_trades=len(extra),
            slippage_bps_live=slippage_modeled_bps + gap,
            slippage_bps_modeled=slippage_modeled_bps,
            pnl_gap_bps=float(Decimal(par.pnl_difference)) / alloc * 1e4,
        )
    )
    rep.pnl_live_bps = float(Decimal(par.paper_realized)) / alloc * 1e4
    rep.pnl_replay_bps = float(Decimal(par.backtest_realized)) / alloc * 1e4
    rep.unexplained_bps = float(Decimal(par.unexplained)) / alloc * 1e4
    rep.passed = par.passed
    return rep


def window_stats(reports: list[mp.DivergenceReport]) -> tuple[float, float, int, bool]:
    """(match rate, mean slippage gap in bps weighted by matched intents, matched + missed + extra,
    every recording complete) over recent daily reports."""
    m = sum(r.matched_intents for r in reports)
    tot = sum(r.matched_intents + r.missed_intents + r.extra_intents for r in reports)
    w = [
        (r.parity.slippage_bps_live - r.parity.slippage_bps_modeled, r.matched_intents)
        for r in reports
        if r.matched_intents
    ]
    gap = sum(g * n for g, n in w) / sum(n for _, n in w) if w else 0.0
    complete = all(r.recording_complete for r in reports) and bool(reports)
    return (m / tot if tot else math.nan), gap, tot, complete


def target_dates(recorder: SessionRecorder, today: date) -> list[str]:
    return [d for d in recorder.sessions() if d < today.isoformat()]


def exists(recorder: SessionRecorder, d: str) -> bool:
    return Path(recorder.path(d)).exists()
