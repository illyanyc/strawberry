"""Data-quality checks for bars (rule ST-010).

Same rules as the Go implementation (``services/marketdata/internal/dq``); both are tested against
``tests/fixtures/dq``. Checks: duplicates, OHLC consistency, non-positive prices, negative volume,
timestamps off the session grid, non-monotonic input, volume spikes, and gaps versus the exchange
calendar (runs of consecutive missing expected bars within a session).
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import polars as pl

from tbcore.data.calendar import TIMEFRAMES, Calendar, xnys


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    instrument_id: str
    ts_open: datetime
    detail: str


@dataclass(frozen=True, slots=True)
class Gap:
    instrument_id: str
    start: datetime  # first missing bar open
    end: datetime  # open of the first bar after the run (or session end)
    missing: int


@dataclass(slots=True)
class DQReport:
    rows: int
    findings: list[Finding] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)

    @property
    def clean(self) -> bool:
        return not self.findings and not self.gaps


def dq_check(
    bars: pl.DataFrame,
    start: datetime,
    end: datetime,
    timeframe: str = "1m",
    instruments: list[str] | None = None,
    extended: bool = False,
    min_gap_run: int = 1,
    spike_factor: float = 20.0,
    spike_window: int = 20,
    calendar: Calendar | None = None,
) -> DQReport:
    """Validate ``bars`` (columns instrument_id, ts_open, ts_close, open, high, low, close, volume)
    over sessions whose open is in ``[start, end)``."""
    cal = calendar or xnys()
    rep = DQReport(rows=bars.height)

    def add(check: str, inst: str, ts: datetime, detail: str) -> None:
        rep.findings.append(Finding(check, inst, ts, detail))
        rep.counts[check] += 1

    ids = set(bars["instrument_id"].unique().to_list()) if bars.height else set()
    ids |= set(instruments or [])
    sessions = cal.sessions(start, end)
    step = TIMEFRAMES[timeframe]
    for inst in sorted(ids):
        rows = bars.filter(pl.col("instrument_id") == inst).to_dicts()
        for i in range(1, len(rows)):
            if rows[i]["ts_open"] < rows[i - 1]["ts_open"]:
                add("non_monotonic", inst, rows[i]["ts_open"], "ts_open decreases in input order")
                break
        rows.sort(key=lambda r: r["ts_open"])
        present: set[datetime] = set()
        vols: list[float] = []
        prev: datetime | None = None
        for r in rows:
            t = r["ts_open"]
            if prev is not None and t == prev:
                add("duplicate", inst, t, "same ts_open twice")
                continue
            prev = t
            present.add(t)
            o, h, lo, c, v = r["open"], r["high"], r["low"], r["close"], r["volume"]
            if min(o, h, lo, c) <= 0:
                add("nonpositive_price", inst, t, f"o={o} h={h} l={lo} c={c}")
            elif h < max(o, c, lo) or lo > min(o, c, h):
                add("ohlc_inconsistent", inst, t, f"o={o} h={h} l={lo} c={c}")
            if v < 0:
                add("negative_volume", inst, t, f"v={v}")
            if r["ts_close"] <= t or not _aligned(cal, t, timeframe, step):
                add("misaligned_ts", inst, t, "ts_open off the session grid or ts_close <= ts_open")
            if len(vols) >= spike_window:
                med = statistics.median(vols[-spike_window:])
                if med > 0 and v > spike_factor * med:
                    add("volume_spike", inst, t, f"v={v} median={med}")
            if v >= 0:
                vols.append(v)
        for s in sessions:
            run, run_start = 0, s.open
            for op in s.bar_opens(timeframe, extended):
                if op in present:
                    if run:
                        if run >= min_gap_run:
                            rep.gaps.append(Gap(inst, run_start, op, run))
                            rep.counts["gap"] += 1
                        run = 0
                    continue
                if run == 0:
                    run_start = op
                run += 1
            if run and run >= min_gap_run:
                rep.gaps.append(Gap(inst, run_start, s.post_close if extended else s.close, run))
                rep.counts["gap"] += 1
    return rep


def _aligned(cal: Calendar, t: datetime, timeframe: str, step: timedelta) -> bool:
    s, _ = cal.session_at(t)
    if s is None:
        return False
    if timeframe == "1d":
        return t == s.open
    base = s.pre_open if t < s.open else s.open
    return (t - base) % step == timedelta(0)
