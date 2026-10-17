"""Maintenance calendar and outage classification: IB-013, IB-014, IB-015, IB-024; backoff IB-011."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

import pytest
from tb_broker_ibkr.backoff import Backoff
from tb_broker_ibkr.schedule import ET, MaintenanceSchedule, OutageReason, Severity

S = MaintenanceSchedule()


def et(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=ET)


TUE = (2026, 10, 13)
SAT = (2026, 10, 17)
SUN = (2026, 10, 18)


@pytest.mark.parametrize(
    ("started", "elapsed_min", "reason", "alert", "reauth"),
    [
        # Daily restart at 23:45 ET (10-minute window + 3-minute grace).
        (et(*TUE, 23, 46), 5, OutageReason.DAILY_RESTART, None, False),
        (et(*TUE, 23, 46), 11, OutageReason.DAILY_RESTART, None, False),
        (et(*TUE, 23, 46), 20, OutageReason.DAILY_RESTART_OVERRUN, Severity.CRITICAL, True),
        # Unexpected outage on a weekday.
        (et(*TUE, 10, 0), 2, OutageReason.DISCONNECTED, None, False),
        (et(*TUE, 10, 0), 4, OutageReason.GATEWAY_UNREACHABLE, Severity.CRITICAL, True),
        # Weekend maintenance until the Sunday 17:00 ET cold restart.
        (et(*SAT, 3, 0), 600, OutageReason.WEEKEND_MAINTENANCE, None, False),
        (et(*SUN, 2, 0), 60, OutageReason.WEEKEND_MAINTENANCE, None, False),
        # The weekend outage rolls into the weekly login at the cold restart.
        (et(*SUN, 2, 0), 15 * 60 + 1, OutageReason.WEEKLY_REAUTH, None, True),
        (et(*SUN, 2, 0), 15 * 60 + 4, OutageReason.WEEKLY_REAUTH, Severity.CRITICAL, True),
        # Cold restart itself.
        (et(*SUN, 17, 0), 2, OutageReason.WEEKLY_REAUTH, None, True),
        (et(*SUN, 17, 0), 4, OutageReason.WEEKLY_REAUTH, Severity.CRITICAL, True),
    ],
)
def test_ib013_ib015_classify_outage(
    started: datetime, elapsed_min: int, reason: OutageReason, alert: Severity | None, reauth: bool
) -> None:
    got = S.classify_outage(started, started + timedelta(minutes=elapsed_min))
    assert (got.reason, got.alert, got.reauth_required) == (reason, alert, reauth)


def test_ib015_outage_running_into_the_weekend_is_warning() -> None:
    started = et(2026, 10, 16, 23, 58)  # Friday, after the daily window
    got = S.classify_outage(started, started + timedelta(minutes=10))  # Saturday 00:08
    assert (got.reason, got.alert) == (OutageReason.GATEWAY_UNREACHABLE, Severity.WARNING)


def test_ib014_sunday_evening_outage_after_login_needs_reauth() -> None:
    started = et(*SUN, 20, 0)
    got = S.classify_outage(started, started + timedelta(minutes=10))
    assert (got.reason, got.alert, got.reauth_required) == (OutageReason.WEEKLY_REAUTH, Severity.CRITICAL, True)


def test_daily_window_spans_midnight() -> None:
    late = MaintenanceSchedule(daily_restart=time(23, 55))
    window = late.daily_window_containing(et(2026, 10, 14, 0, 2))
    assert window is not None and window[0].hour == 23


def test_cold_restart_instants() -> None:
    assert S.cold_restart_on(et(*TUE, 10)) == et(*SUN, 17)
    assert S.last_cold_restart(et(*TUE, 10)) == et(2026, 10, 11, 17)
    assert S.last_cold_restart(et(*SUN, 17, 30)) == et(*SUN, 17)


@pytest.mark.parametrize(
    ("tif", "outside_rth", "placed", "now", "lapsed"),
    [
        ("DAY", False, et(*TUE, 10), et(*TUE, 15, 59), False),
        ("DAY", False, et(*TUE, 10), et(*TUE, 16, 0), True),
        ("DAY", True, et(*TUE, 10), et(*TUE, 19, 0), False),
        ("DAY", True, et(*TUE, 10), et(*TUE, 20, 0), True),
        ("DAY", False, et(*TUE, 17), et(*TUE, 18), False),  # placed after the close: next session
        ("OPG", False, et(*TUE, 8), et(*TUE, 9, 31), True),
        ("MOC", False, et(*TUE, 10), et(*TUE, 16, 1), True),
        ("GTC", False, et(*TUE, 10), et(2026, 10, 20, 10), False),
    ],
)
def test_ib024_tif_lapsed(tif: str, outside_rth: bool, placed: datetime, now: datetime, lapsed: bool) -> None:
    assert S.tif_lapsed(tif, outside_rth, placed, now) is lapsed


def test_ib011_backoff_bounded_jitter_and_reset() -> None:
    b = Backoff(base=1, cap=60, rng=random.Random(7))  # noqa: S311 - reconnect jitter, not crypto
    delays = [b.next_delay() for _ in range(12)]
    assert all(d >= 0.5 for d in delays), "equal jitter keeps a floor: no tight loops"
    assert all(d <= 60 for d in delays)
    assert delays[-1] >= 30, "grows to the cap"
    b.reset()
    assert b.next_delay() <= 1


def test_ib011_backoff_survives_multi_day_outage() -> None:
    b = Backoff(base=1, cap=60, rng=random.Random(1))  # noqa: S311 - reconnect jitter, not crypto
    b._attempt = 5000  # ~3.5 days of attempts at the cap
    assert 30 <= b.next_delay() <= 60
