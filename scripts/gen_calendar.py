#!/usr/bin/env python3
"""Generate the shared XNYS session table from exchange_calendars (rule MD-005, ADR-0020).

Writes one CSV that Go (embedded in ``libs/go/calendar``) and Python (package data in
``tbcore.data``) both read, so every service uses the same sessions, half days, and holidays.

Columns (UTC, ISO 8601): session date (exchange-local), pre-market open (04:00 ET), regular open,
regular close (early close on half days), post-market close (20:00 ET, 17:00 ET on half days),
early_close flag.

Usage: ``uv run python scripts/gen_calendar.py [--check]``. ``--check`` fails if the committed files
differ from a fresh generation (run in CI by tests).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

ROOT = Path(__file__).resolve().parent.parent
TARGETS = [
    ROOT / "libs/go/calendar/xnys_sessions.csv",
    ROOT / "libs/py/tbcore/src/tbcore/data/xnys_sessions.csv",
]
START, END = "2005-01-03", "2030-12-31"
NY = ZoneInfo("America/New_York")


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate() -> str:
    cal = xcals.get_calendar("XNYS", start=START, end=END)
    lines = ["date,pre_open_utc,open_utc,close_utc,post_close_utc,early_close"]
    early = set(cal.early_closes.date) if len(cal.early_closes) else set()
    for session in cal.sessions:
        d = session.date()
        open_utc = cal.session_open(session).to_pydatetime()
        close_utc = cal.session_close(session).to_pydatetime()
        is_early = d in early or close_utc.astimezone(NY).time() < time(16, 0)
        pre = datetime.combine(d, time(4, 0), NY)
        post = datetime.combine(d, time(17, 0) if is_early else time(20, 0), NY)
        lines.append(f"{d.isoformat()},{iso(pre)},{iso(open_utc)},{iso(close_utc)},{iso(post)},{int(is_early)}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if committed files are stale")
    args = parser.parse_args()
    content = generate()
    stale = []
    for target in TARGETS:
        if args.check:
            if not target.exists() or target.read_text() != content:
                stale.append(str(target.relative_to(ROOT)))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    if stale:
        print("calendar files are stale; run scripts/gen_calendar.py:", ", ".join(stale), file=sys.stderr)
        return 1
    print(f"calendar: {content.count(chr(10)) - 1} sessions {START}..{END}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
