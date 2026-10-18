"""Point-in-time availability rules for context data (section 09, rules CX-004..CX-009).

Every context record carries ``available_at``: the earliest time the platform could have known it.
These pure functions compute it per source so ingestion (context-data), backfills, and tests share
one implementation. Defaults are conservative and configurable through :class:`AvailabilityConfig`
(``TB_CTX_*`` settings in context-data); the business-logic doc lists them.

Snapshot stamping (CX-004): the first time a record is seen, ``available_at`` is the rule time
(vendor/official time plus a delay); when a later fetch shows different content, the new version's
``available_at`` is the fetch time, never earlier than the rule time.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from tbcore.data.calendar import Calendar, xnys

ET = ZoneInfo("America/New_York")
NS = 1_000_000_000
_FINRA_SCHEDULE = Path(__file__).with_name("finra_short_interest_schedule.csv")


def et_ns(d: date, t: dtime) -> int:
    """UTC nanoseconds of a New York wall-clock time on date ``d`` (DST-aware, DF-011)."""
    return int(datetime.combine(d, t, tzinfo=ET).astimezone(UTC).timestamp()) * NS


def parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class Timing(StrEnum):
    """Earnings report timing relative to the regular session."""

    BMO = "bmo"  # before market open
    AMC = "amc"  # after market close
    DMH = "dmh"  # during market hours
    UNKNOWN = "unknown"


def parse_timing(raw: str | None) -> Timing:
    """Map vendor timing strings ("bmo", "amc", "pre market", "08:30", ...) to :class:`Timing`."""
    s = (raw or "").strip().lower().replace("-", " ").replace("_", " ")
    if not s or s in ("--", "n/a", "none", "time not supplied", "tns"):
        return Timing.UNKNOWN
    if s in ("bmo", "before market open", "pre market", "premarket", "before open"):
        return Timing.BMO
    if s in ("amc", "after market close", "post market", "postmarket", "after close"):
        return Timing.AMC
    if s in ("dmh", "during market hours", "market hours"):
        return Timing.DMH
    try:
        t = parse_hhmm(s[:5])
    except ValueError:
        return Timing.UNKNOWN
    if t < dtime(9, 30):
        return Timing.BMO
    if t >= dtime(16, 0):
        return Timing.AMC
    return Timing.DMH


@dataclass(frozen=True)
class AvailabilityConfig:
    """Conservative availability defaults (configuration, not code constants)."""

    news_delay_s: int = 60
    sentiment_latency_s: int = 2
    econ_actual_delay_s: int = 60
    econ_schedule_lead_days: int = 7
    earnings_bmo_time: str = "09:30"
    earnings_dmh_time: str = "16:00"
    earnings_amc_time: str = "20:00"
    earnings_unknown_time: str = "20:00"
    earnings_actual_delay_s: int = 0
    earnings_schedule_lead_days: int = 14
    fundamentals_delay_s: int = 300
    edgar_delay_s: int = 300
    finra_publish_time: str = "23:59"
    finra_fallback_sessions: int = 8
    macro_default_release_time: str = "23:59"
    # Per-series official release time (New York) of a new vintage. Unknown series use the default.
    macro_release_times: dict[str, str] = field(
        default_factory=lambda: {
            "PAYEMS": "08:30",
            "UNRATE": "08:30",
            "CPIAUCSL": "08:30",
            "CPILFESL": "08:30",
            "PPIACO": "08:30",
            "GDP": "08:30",
            "GDPC1": "08:30",
            "PCEPI": "08:30",
            "RSAFS": "08:30",
            "ICSA": "08:30",
            "INDPRO": "09:15",
        }
    )


DEFAULT = AvailabilityConfig()


def news_available_at(created_at_ns: int, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-010: first publication plus the configured receipt delay; ``updated_at`` is never used."""
    return created_at_ns + cfg.news_delay_s * NS


def sentiment_available_at(news_available_ns: int, scored_at_ns: int | None, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-012: live scores are available when scored; backfilled scores at the article's
    availability plus the configured scoring latency (whichever is later)."""
    rule = news_available_ns + cfg.sentiment_latency_s * NS
    return rule if scored_at_ns is None else max(rule, scored_at_ns)


def econ_actual_available_at(release_ns: int, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-006: an economic release's actual (and surprise) at the scheduled release time plus delay."""
    return release_ns + cfg.econ_actual_delay_s * NS


def econ_schedule_available_at(release_ns: int, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-006: first-seen schedule/consensus is assumed public ``econ_schedule_lead_days`` before."""
    return release_ns - cfg.econ_schedule_lead_days * 86_400 * NS


def earnings_actual_available_at(report_date: date, timing: Timing, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-007: an earnings result (actuals, surprise) is available at a timing-dependent New York time
    on the report date: BMO 09:30, DMH 16:00, AMC and unknown 20:00. An AMC surprise is therefore
    never visible during that day's regular session."""
    t = {
        Timing.BMO: cfg.earnings_bmo_time,
        Timing.DMH: cfg.earnings_dmh_time,
        Timing.AMC: cfg.earnings_amc_time,
        Timing.UNKNOWN: cfg.earnings_unknown_time,
    }[timing]
    return et_ns(report_date, parse_hhmm(t)) + cfg.earnings_actual_delay_s * NS


def earnings_schedule_available_at(report_date: date, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-007: a first-seen report date is assumed public ``earnings_schedule_lead_days`` before it."""
    return et_ns(report_date - timedelta(days=cfg.earnings_schedule_lead_days), dtime(0, 0))


def fundamentals_available_at(accepted_et: str, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-008: FMP ``acceptedDate`` ("YYYY-MM-DD HH:MM:SS", SEC acceptance in New York time) plus
    delay. Only for the first snapshot of a statement; restatements are stamped at fetch time."""
    dt = datetime.strptime(accepted_et.strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
    return int(dt.astimezone(UTC).timestamp()) * NS + cfg.fundamentals_delay_s * NS


def edgar_available_at(acceptance: str, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-009: EDGAR ``acceptanceDateTime`` from the submissions API parsed as labeled (UTC) plus
    delay. Observed 2026-10-18: the API value is the New York acceptance time shifted by about +8 h,
    i.e. 4-5 h later than true UTC, so this is conservative; if the SEC fixes the label it stays
    correct."""
    s = acceptance.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1e6) * 1_000 + cfg.edgar_delay_s * NS


def load_finra_schedule(path: str | Path = _FINRA_SCHEDULE) -> dict[date, date]:
    """FINRA short-interest settlement → publication dates (official schedule, CSV)."""
    out: dict[date, date] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            out[date.fromisoformat(row["settlement_date"])] = date.fromisoformat(row["publication_date"])
    return out


def finra_publication_date(
    settlement: date,
    schedule: dict[date, date] | None = None,
    cal: Calendar | None = None,
    cfg: AvailabilityConfig = DEFAULT,
) -> date:
    """CX-005: the official publication date for a settlement date; outside the seeded schedule,
    ``finra_fallback_sessions`` XNYS sessions after settlement (the 2025-26 schedule is 7)."""
    sched = schedule if schedule is not None else load_finra_schedule()
    if settlement in sched:
        return sched[settlement]
    c = cal or xnys()
    start = datetime.combine(settlement + timedelta(days=1), dtime(0, 0), tzinfo=UTC)
    sessions = c.sessions(start, start + timedelta(days=cfg.finra_fallback_sessions * 2 + 14))
    if len(sessions) < cfg.finra_fallback_sessions:
        return settlement + timedelta(days=cfg.finra_fallback_sessions * 2)
    return sessions[cfg.finra_fallback_sessions - 1].date


def finra_available_at(
    settlement: date,
    schedule: dict[date, date] | None = None,
    cal: Calendar | None = None,
    cfg: AvailabilityConfig = DEFAULT,
) -> int:
    """CX-005: short interest is available at the end of its publication date (New York), never at
    the settlement date."""
    return et_ns(finra_publication_date(settlement, schedule, cal, cfg), parse_hhmm(cfg.finra_publish_time))


def macro_available_at(series_id: str, vintage: date, cfg: AvailabilityConfig = DEFAULT) -> int:
    """CX-006 / DF-010: an ALFRED vintage (``realtime_start``) is available at the series' official
    release time on that date; unknown series at the end of the day."""
    t = cfg.macro_release_times.get(series_id.upper(), cfg.macro_default_release_time)
    return et_ns(vintage, parse_hhmm(t))


def stamp(rule_ns: int, fetched_ns: int, previous_sha: str | None, sha: str) -> int | None:
    """CX-004 snapshot stamping. Returns the version's ``available_at``, or None when the content is
    unchanged since the previous version (nothing to store).

    * first seen: the rule time (vendor/official time plus delay);
    * changed: the fetch time, never earlier than the rule time.
    """
    if previous_sha is None:
        return rule_ns
    if previous_sha == sha:
        return None
    return max(rule_ns, fetched_ns)
