"""JetStream recorder for live and paper streams (MN-045, ADR-0046).

Durable pull consumers (``monitor-rec-<stream>``) feed one length-delimited ``RecordedMessage`` file per
XNYS session (``<dir>/<YYYY-MM-DD>.tbrec``), the format ``tbcore.backtest.recording`` and ``tbcore.parity``
read. A message is acknowledged only after it is written and flushed; files are fsynced every few seconds
and at rollover. On start the current file is scanned for the highest stream sequence per stream so a
redelivery never duplicates a message. A JSON manifest per session records counts and gaps; a session is
``complete`` only when the recorder ran from before the open to after the close without losing messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from nats.errors import TimeoutError as NatsTimeout

from tb.backtest.v1 import simcore_pb2
from tbcore.backtest.recording import iter_recording
from tbcore.backtest.simcore import _varint
from tbcore.data.calendar import xnys

log = logging.getLogger("monitor.recorder")

# (stream, subject filter) pairs recorded by default (ADR-0046).
DEFAULT_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("MARKETDATA", "md.bar.*.*"),
    ("MARKETDATA", "md.bar.correction.>"),
    ("MARKETDATA", "feat.>"),
    ("SIGNALS", "sig.>"),
    ("ORDERS", "ord.>"),
    ("ORDERS", "exec.>"),
    ("ORDERS", "risk.kill"),
    ("LEDGER", "ledger.update.>"),
    ("LEDGER", "recon.result.>"),
)
STREAM_OF = {
    "md.": "MARKETDATA",
    "feat.": "MARKETDATA",
    "sig.": "SIGNALS",
    "ord.": "ORDERS",
    "exec.": "ORDERS",
    "risk.": "ORDERS",
    "ledger.": "LEDGER",
    "recon.": "LEDGER",
}


def stream_of(subject: str) -> str:
    return next((v for k, v in STREAM_OF.items() if subject.startswith(k)), "")


def subject_class(subject: str) -> str:
    parts = subject.split(".")
    return ".".join(parts[:2]) if parts[0] in ("md", "ord", "ledger", "recon") else parts[0]


@dataclass
class SessionTimes:
    date: str
    open_ns: int
    close_ns: int


def xnys_session(ns: int) -> SessionTimes:
    """The XNYS session a message belongs to: the one containing it (extended hours included), else the
    last one that closed before it (overnight messages follow their session)."""
    cal = xnys()
    ts = datetime.fromtimestamp(ns / 1e9, tz=UTC)
    s, _ = cal.session_at(ts)
    s = s or cal.previous_session(ts)
    if s is None:
        return SessionTimes(ts.date().isoformat(), ns, ns)
    return SessionTimes(s.date.isoformat(), int(s.open.timestamp() * 1e9), int(s.close.timestamp() * 1e9))


@dataclass
class _Open:
    session: SessionTimes
    f: BinaryIO
    counts: Counter[str] = field(default_factory=Counter)
    started_at: int = 0
    last_at: int = 0
    gaps: list[str] = field(default_factory=list)
    last_sync: float = 0.0


class SessionRecorder:
    """File side of the recorder (pure I/O, no NATS): dedupe, rollover, manifests, retention."""

    def __init__(
        self, directory: str | Path, session_of: Callable[[int], SessionTimes], retention_days: int = 180
    ) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.session_of = session_of
        self.retention = retention_days
        self.cur: _Open | None = None
        self.max_seq: dict[str, int] = {}
        self.pending_gaps: list[str] = []
        # When this process started recording: a session opened later by the same running recorder
        # was covered from before its open.
        self.running_since = 0

    def path(self, d: str) -> Path:
        return self.dir / f"{d}.tbrec"

    def manifest_path(self, d: str) -> Path:
        return self.dir / f"{d}.json"

    def _open(self, s: SessionTimes, now: int) -> _Open:
        p = self.path(s.date)
        counts: Counter[str] = Counter()
        started = min(self.running_since or now, now)
        gaps: list[str] = []
        if p.exists():
            with open(p, "rb") as f:
                for m in iter_recording(f):
                    st = stream_of(m.subject)
                    self.max_seq[st] = max(self.max_seq.get(st, 0), int(m.stream_sequence))
                    counts[subject_class(m.subject)] += 1
            mf = self.manifest_path(s.date)
            if mf.exists():
                old = json.loads(mf.read_text(encoding="utf-8"))
                started, gaps = int(old.get("started_at", now)), list(old.get("gaps", []))
        else:
            self.max_seq = {}
        o = _Open(s, open(p, "ab"), counts, started, now, gaps + self.pending_gaps, time.monotonic())  # noqa: SIM115 - kept open across writes
        self.pending_gaps = []
        self._manifest(o, complete=False)
        return o

    def write(self, subject: str, data: bytes, msg_id: str, stream_seq: int, recorded_at: int) -> bool:
        """Append one message to its session's file; False when it was recorded before (redelivery)."""
        s = self.session_of(recorded_at)
        self.running_since = self.running_since or recorded_at
        if self.cur is None or self.cur.session.date != s.date:
            self.rollover(recorded_at)
            self.cur = self._open(s, recorded_at)
        st = stream_of(subject)
        if stream_seq and stream_seq <= self.max_seq.get(st, 0):
            return False
        m = simcore_pb2.RecordedMessage(
            subject=subject, data=data, msg_id=msg_id, recorded_at=recorded_at, stream_sequence=stream_seq
        )
        b = m.SerializeToString()
        self.cur.f.write(_varint(len(b)) + b)
        self.cur.f.flush()
        if stream_seq:
            self.max_seq[st] = stream_seq
        self.cur.counts[subject_class(subject)] += 1
        self.cur.last_at = recorded_at
        if time.monotonic() - self.cur.last_sync > 5:
            os.fsync(self.cur.f.fileno())
            self.cur.last_sync = time.monotonic()
        return True

    def gap(self, reason: str) -> None:
        """Messages were lost (a consumer fell behind its stream's retention, or it started mid-session)."""
        if self.cur is not None:
            self.cur.gaps.append(reason)
            self._manifest(self.cur, complete=False)
        else:
            self.pending_gaps.append(reason)

    def rollover(self, now: int) -> None:
        """Close the current session's file (fsync) and write its final manifest."""
        if self.cur is None:
            return
        o = self.cur
        o.f.flush()
        os.fsync(o.f.fileno())
        o.f.close()
        complete = o.started_at <= o.session.open_ns and now >= o.session.close_ns and not o.gaps
        self._manifest(o, complete=complete, stopped_at=now)
        self.cur = None

    def _manifest(self, o: _Open, complete: bool, stopped_at: int = 0) -> None:
        body = {
            "date": o.session.date,
            "session_open": o.session.open_ns,
            "session_close": o.session.close_ns,
            "started_at": o.started_at,
            "last_message_at": o.last_at,
            "stopped_at": stopped_at,
            "counts": dict(sorted(o.counts.items())),
            "gaps": o.gaps,
            "complete": complete,
        }
        tmp = self.manifest_path(o.session.date).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body, sort_keys=True))
        tmp.replace(self.manifest_path(o.session.date))

    def manifest(self, d: str) -> dict[str, Any]:
        p = self.manifest_path(d)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def sessions(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.tbrec"))

    def prune(self, today: date) -> list[str]:
        cut = today - timedelta(days=self.retention)
        gone = []
        for d in self.sessions():
            if date.fromisoformat(d) < cut and (self.cur is None or self.cur.session.date != d):
                self.path(d).unlink(missing_ok=True)
                self.manifest_path(d).unlink(missing_ok=True)
                gone.append(d)
        return gone

    def close(self) -> None:
        if self.cur is not None:
            self.cur.f.flush()
            os.fsync(self.cur.f.fileno())
            self.cur.f.close()
            self.cur = None


def iter_sessions(directory: str | Path, dates: list[str]) -> Any:
    """Raw messages of the given sessions in file order (the monitor's history replay at start)."""
    for d in dates:
        p = Path(directory) / f"{d}.tbrec"
        if p.exists():
            with open(p, "rb") as f:
                yield from iter_recording(f)


async def run(
    js: Any,
    rec: SessionRecorder,
    stop: asyncio.Event,
    subjects: tuple[tuple[str, str], ...] = DEFAULT_SUBJECTS,
    on_message: Callable[[str, bytes], Any] | None = None,
    batch: int = 256,
) -> None:
    """Durable pull consumers per (stream, filter); each message is written, then acked."""
    subs = []
    for stream, subj in subjects:
        durable = "monitor-rec-" + subj.replace(".", "_").replace("*", "s").replace(">", "all")
        try:
            info = await js.consumer_info(stream, durable)
            sinfo = await js.stream_info(stream)
            floor = info.ack_floor.stream_seq if info.ack_floor else 0
            if floor and floor + 1 < sinfo.state.first_seq:
                rec.gap(f"{stream}/{subj}: messages {floor + 1}..{sinfo.state.first_seq - 1} expired before recording")
        except Exception:  # first start: recording begins now
            rec.gap(f"{stream}/{subj}: consumer created at {datetime.now(tz=UTC).isoformat()}")
        subs.append(await js.pull_subscribe(subj, durable=durable, stream=stream))
    while not stop.is_set():
        idle = True
        for sub in subs:
            try:
                msgs = await sub.fetch(batch, timeout=1)
            except (TimeoutError, NatsTimeout):
                continue
            for m in msgs:
                idle = False
                seq = m.metadata.sequence.stream if m.metadata else 0
                fresh = rec.write(m.subject, m.data, (m.headers or {}).get("Nats-Msg-Id", ""), seq, time.time_ns())
                if fresh and on_message is not None:
                    with contextlib.suppress(Exception):
                        on_message(m.subject, m.data)
                await m.ack()
        if idle:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=0.5)
    rec.close()
