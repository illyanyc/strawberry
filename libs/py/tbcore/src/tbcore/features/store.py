"""Offline materialization, dataset snapshots, the online engine, and parity checks (section 03,
ADR-0021, rules DF-002..DF-004, DF-009).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import polars as pl

from tbcore.context.features import ContextFeatureSpec, context_features
from tbcore.context.store import ContextStore
from tbcore.data.calendar import TIMEFRAMES, Calendar, xnys
from tbcore.data.lake import Lake, TimeLike, apply_adjustment, to_dt, to_ns
from tbcore.features.core import BAR_COLUMNS, FeatureSet, compute

#: Documented offline/online tolerance (DF-004): |a - b| <= TOL * max(1, |a|, |b|).
PARITY_TOLERANCE = 1e-9


def warmup_start(cal: Calendar, start: datetime, bars: int, timeframe: str) -> datetime:
    """Earliest time far enough before ``start`` to provide ``bars`` bars of warm-up (extended hours
    included), plus two sessions of slack for holidays and half days."""
    step = TIMEFRAMES[timeframe]
    per_session = 1 if timeframe == "1d" else max(1, int(timedelta(hours=16) / step))
    needed = -(-bars // per_session) + 2
    sessions = cal.sessions(start - timedelta(days=needed * 2 + 14), start)
    if not sessions:
        return start - timedelta(days=needed * 2 + 14)
    return sessions[-min(needed, len(sessions))].pre_open


@dataclass(frozen=True)
class DatasetSnapshot:
    snapshot_id: str
    path: Path
    manifest: dict[str, Any]

    def load(self) -> pl.DataFrame:
        return pl.read_parquet(self.path / "features.parquet")


def _members(lake: Lake, universe: str, start: int, end: int) -> pl.DataFrame:
    m = lake.ref("universe_members").filter(
        (pl.col("universe") == universe)
        & (pl.col("valid_from") < end)
        & ((pl.col("valid_to") == 0) | (pl.col("valid_to") > start))
    )
    inst = lake.ref("instruments").select("instrument_id", "delisted_at")
    return m.join(inst, on="instrument_id", how="left")


def _sector_map(lake: Lake, instruments: Sequence[str], as_of: int) -> dict[str, str]:
    try:
        inst = lake.ref("instruments").filter(
            pl.col("instrument_id").is_in(list(instruments)) & (pl.col("sector_etf") != "")
        )
    except FileNotFoundError:
        return {}
    out = {}
    for row in inst.iter_rows(named=True):
        etf = lake.resolve(row["sector_etf"], as_of)
        if etf:
            out[row["instrument_id"]] = etf
    return out


def materialize(
    fs: FeatureSet,
    lake: Lake,
    universe: str,
    start: TimeLike,
    end: TimeLike,
    timeframe: str = "15m",
    as_of: TimeLike | None = None,
    versions: Literal["latest", "first"] = "first",
    out_root: str | os.PathLike[str] | None = None,
    calendar: Calendar | None = None,
    exclude_quarantined: bool = True,
    context: ContextFeatureSpec | None = None,
) -> DatasetSnapshot:
    """Compute ``fs`` over the point-in-time ``universe`` for bars in ``[start, end)`` and store an
    immutable snapshot (DF-009).

    * Inputs are read with ``as_of`` (default: ``end``) so nothing published later is used (DF-002);
      ``versions="first"`` (default) uses each bar as first published, as live decisions saw it.
    * Rows are kept only while the instrument was a universe member and listed (DF-006), and
      quarantined symbol-days are dropped (MD-010) unless ``exclude_quarantined=False``.
    * ``snapshot_id`` hashes the feature-set hash, universe membership, range, as_of, versions, and
      the exact input files (immutable names), so the same ID always means the same data (DF-I3).
    * With ``context``, the point-in-time context features (CX-018) are joined per row using the
      row's ``available_at`` as the decision time; context inputs are part of the snapshot ID.
    """
    cal = calendar or xnys()
    s_ns, e_ns = to_ns(start), to_ns(end)
    a_ns = to_ns(as_of) if as_of is not None else e_ns
    members = _members(lake, universe, s_ns, e_ns)
    insts = sorted(members["instrument_id"].unique().to_list())
    sector = _sector_map(lake, insts, a_ns)
    ctx_ids = sorted(set(fs.context.values()) | set(sector.values()))
    inputs = [os.path.relpath(p, lake.root) for ds in ("bars", "bar_revisions") for p in lake.files(ds, timeframe)]
    ref_files = sorted(os.path.relpath(p, lake.root) for p in lake.files("ref"))
    ctx_files = sorted(os.path.relpath(p, lake.root) for p in lake.files("context")) if context else []
    ident = {
        "feature_set": fs.hash,
        "universe": universe,
        "members": members.sort(["instrument_id", "valid_from"]).to_dicts(),
        "start": s_ns,
        "end": e_ns,
        "as_of": a_ns,
        "versions": versions,
        "timeframe": timeframe,
        "inputs": sorted(inputs),
        "ref": ref_files,
        "sector": sector,
        **({"context": context.hash, "context_inputs": ctx_files} if context else {}),
    }
    snapshot_id = hashlib.sha256(json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()[:32]
    root = Path(out_root) if out_root else lake.root / "snapshots"
    path = root / snapshot_id
    if (path / "manifest.json").exists():
        return DatasetSnapshot(snapshot_id, path, json.loads((path / "manifest.json").read_text()))
    warm = warmup_start(cal, to_dt(s_ns), fs.max_lookback, timeframe)
    bars = lake.bars(insts + ctx_ids, timeframe, warm, to_dt(e_ns), as_of=a_ns, adjust="split_div", versions=versions)
    feats = compute(fs, bars.select(BAR_COLUMNS), sector_of=sector)
    feats = feats.filter((pl.col("ts_open").dt.epoch("ns") >= s_ns) & pl.col("instrument_id").is_in(insts))
    # Point-in-time membership: keep rows only while the instrument was a listed member (DF-006).
    t = pl.col("ts_open").dt.epoch("ns")
    feats = (
        feats.join(members.select("instrument_id", "valid_from", "valid_to", "delisted_at"), on="instrument_id")
        .filter(
            (t >= pl.col("valid_from"))
            & ((pl.col("valid_to") == 0) | (t < pl.col("valid_to")))
            & (pl.col("delisted_at").is_null() | (pl.col("delisted_at") == 0) | (t < pl.col("delisted_at")))
        )
        .drop(["valid_from", "valid_to", "delisted_at"])
        .unique(subset=["instrument_id", "ts_open"])
        .sort(["instrument_id", "ts_open"])
    )
    if exclude_quarantined:
        feats = lake.drop_quarantined(feats)
    if context is not None:
        feats = context_features(feats, ContextStore(lake), context, time_col="available_at")
    # Leakage guard: no feature row may be computed from data unavailable at its decision time.
    if feats.height and feats.select((pl.col("available_at").dt.epoch("ns") > a_ns).any()).item():
        raise AssertionError("materialize: a feature row uses data with available_at > as_of (DF-002)")
    manifest = {
        **{k: v for k, v in ident.items() if k != "members"},
        "snapshot_id": snapshot_id,
        "feature_set_id": fs.id,
        "features": fs.names + (context.names if context else []),
        "rows": feats.height,
        "instruments": len(insts),
        "created_at": time.time(),
    }
    tmp = root / f".{snapshot_id}.tmp-{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=False)
    feats.write_parquet(tmp / "features.parquet", compression="zstd")
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    try:
        os.rename(tmp, path)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)  # another process won the race; snapshots are immutable
    return DatasetSnapshot(snapshot_id, path, json.loads((path / "manifest.json").read_text()))


def gc_snapshots(
    root: str | os.PathLike[str], referenced: Iterable[str], min_age: timedelta = timedelta(days=7)
) -> list[str]:
    """Delete snapshots not referenced by any training run or backtest and older than ``min_age``
    (DF-009). Callers pass the referenced IDs (MLflow runs, backtests; Phase 3/4)."""
    keep = set(referenced)
    removed: list[str] = []
    base = Path(root)
    if not base.exists():
        return removed
    now = time.time()
    for d in base.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name in keep:
            continue
        if now - d.stat().st_mtime < min_age.total_seconds():
            continue
        shutil.rmtree(d)
        removed.append(d.name)
    return sorted(removed)


class OnlineFeatureEngine:
    """Live feature computation with the same code path as ``materialize`` (DF-004).

    Keeps a ring buffer of the last ``fs.max_lookback`` raw bars per instrument (universe and context
    instruments), applies the corporate actions known so far (``apply_adjustment``), runs
    ``compute`` over the buffers, and returns the vectors for the bars just added. Feed it every
    instrument's final bar for one bar time at once (cross-sectional features need the batch).
    """

    def __init__(
        self,
        fs: FeatureSet,
        sector_of: dict[str, str] | None = None,
        actions: pl.DataFrame | None = None,
        ring: int | None = None,
        context_store: ContextStore | None = None,
        context: ContextFeatureSpec | None = None,
    ) -> None:
        self.fs = fs
        self.context_store = context_store
        self.context = context
        self.sector_of = dict(sector_of or {})
        self.ring = ring or fs.max_lookback
        self.actions = actions if actions is not None else pl.DataFrame()
        self.buffers: dict[str, deque[dict[str, Any]]] = {}

    def apply_corporate_action(self, actions: pl.DataFrame) -> None:
        """Add newly known corporate actions (same columns as ref/corporate_actions)."""
        self.actions = (
            actions if self.actions.is_empty() else pl.concat([self.actions, actions], how="diagonal_relaxed")
        )

    def update(self, bars: pl.DataFrame) -> pl.DataFrame:
        """Add final raw bars (one bar time) and return their feature vectors."""
        for row in bars.select(BAR_COLUMNS).iter_rows(named=True):
            self.buffers.setdefault(row["instrument_id"], deque(maxlen=self.ring)).append(row)
        rows = [r for buf in self.buffers.values() for r in buf]
        panel = pl.DataFrame(rows, schema=bars.select(BAR_COLUMNS).schema, orient="row")
        panel = apply_adjustment(panel, self.actions, "split_div").select(BAR_COLUMNS)
        out = compute(self.fs, panel, sector_of=self.sector_of)
        keys = bars.select("instrument_id", "ts_open")
        out = out.join(keys, on=["instrument_id", "ts_open"], how="inner").sort("instrument_id")
        if self.context is not None and self.context_store is not None:
            out = context_features(out, self.context_store, self.context, time_col="available_at")
        return out


@dataclass(frozen=True)
class ParityReport:
    rows: int
    max_abs_diff: dict[str, float]
    violations: int

    @property
    def ok(self) -> bool:
        return self.violations == 0


def compare(
    offline: pl.DataFrame, online: pl.DataFrame, features: Sequence[str], tol: float = PARITY_TOLERANCE
) -> ParityReport:
    """Compare two feature frames on (instrument_id, ts_open) within the documented tolerance."""
    j = offline.join(online, on=["instrument_id", "ts_open"], suffix="_online", how="inner")
    diffs: dict[str, float] = {}
    bad = 0
    for f in features:
        a, b = pl.col(f), pl.col(f"{f}_online")
        both_null = a.is_null() & b.is_null()
        one_null = a.is_null() ^ b.is_null()
        nan_eq = a.is_nan() & b.is_nan()
        d = (a - b).abs()
        scale = pl.max_horizontal(pl.lit(1.0), a.abs(), b.abs())
        res = j.select(
            d.filter(~both_null & ~one_null & ~nan_eq).max().alias("mx"),
            ((one_null | (~both_null & ~nan_eq & (d > tol * scale))).sum()).alias("bad"),
        )
        diffs[f] = float(res["mx"][0] or 0.0)
        bad += int(res["bad"][0])
    return ParityReport(rows=j.height, max_abs_diff=diffs, violations=bad)


def parity_check(
    fs: FeatureSet,
    lake: Lake,
    online_vectors: pl.DataFrame,
    timeframe: str = "15m",
    sector_of: dict[str, str] | None = None,
    calendar: Calendar | None = None,
) -> ParityReport:
    """Recompute features offline for the window covered by stored online vectors and compare
    (DF-004). ``online_vectors`` has instrument_id, ts_open, and one column per feature."""
    cal = calendar or xnys()
    start = online_vectors["ts_open"].min()
    end = online_vectors["ts_open"].max()
    assert isinstance(start, datetime) and isinstance(end, datetime)
    insts = online_vectors["instrument_id"].unique().to_list() + sorted(
        set(fs.context.values()) | set((sector_of or {}).values())
    )
    bars = lake.bars(
        insts,
        timeframe,
        warmup_start(cal, start, fs.max_lookback, timeframe),
        end + timedelta(seconds=1),
        adjust="split_div",
        versions="first",
    )
    offline = compute(fs, bars.select(BAR_COLUMNS), sector_of=sector_of)
    return compare(offline, online_vectors, fs.names)
