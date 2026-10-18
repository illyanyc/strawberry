"""IB Gateway maintenance calendar and outage classification (IB-013, IB-014, IB-015, IB-024).

All configured times are US/Eastern wall-clock times, matching the gateway's ``TIME_ZONE`` and
IBKR's own schedule:

* **Daily restart.** IBC restarts the gateway every day at ``AUTO_RESTART_TIME`` and reuses the
  session token, so no 2FA is needed. A disconnect that starts inside the restart window is
  expected; it only alerts if the session is not back by the end of the window plus the grace.
* **Weekly re-authentication.** IBKR invalidates sessions on Sunday after 01:00 ET. IBC performs a
  cold restart (full login, IBKR Mobile 2FA push for live) on Sunday at ``ColdRestartTime``. From
  Saturday 00:00 ET until the cold restart, outages are weekend maintenance (no alert). From the
  cold restart on, the owner must approve the push: a reminder fires at the cold restart and a
  critical re-auth alert fires if the session is not ready after the grace.
* **Anything else** is unexpected: after the grace it alerts critical on weekdays (a gateway
  container restart mid-week also forces 2FA, so the alert says so) and warning on weekends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SUNDAY = 6
SATURDAY = 5


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class OutageReason(StrEnum):
    DISCONNECTED = "disconnected"
    DAILY_RESTART = "daily_restart"
    DAILY_RESTART_OVERRUN = "daily_restart_overrun"
    WEEKEND_MAINTENANCE = "weekend_maintenance"
    WEEKLY_REAUTH = "weekly_reauth"
    GATEWAY_UNREACHABLE = "gateway_unreachable"


@dataclass(frozen=True, slots=True)
class OutageAssessment:
    reason: OutageReason
    # None: expected so far, no alert yet.
    alert: Severity | None
    # The fix is most likely a login/2FA approval by the owner (runbook: ibkr-weekly-2fa).
    reauth_required: bool


@dataclass(frozen=True, slots=True)
class MaintenanceSchedule:
    daily_restart: time = time(23, 45)
    daily_window: timedelta = timedelta(minutes=10)
    cold_restart: time = time(17, 0)
    grace: timedelta = timedelta(minutes=3)

    # ---- windows -------------------------------------------------------------------------
    def daily_window_containing(self, ts: datetime) -> tuple[datetime, datetime] | None:
        local = ts.astimezone(ET)
        for day_offset in (0, -1):
            day = local.date() + timedelta(days=day_offset)
            start = datetime.combine(day, self.daily_restart, tzinfo=ET)
            end = start + self.daily_window
            if start <= local < end:
                return start, end
        return None

    def cold_restart_on(self, ts: datetime) -> datetime:
        """The cold restart instant of the week containing ``ts`` (its Sunday)."""
        local = ts.astimezone(ET)
        sunday = local.date() + timedelta(days=(SUNDAY - local.weekday()) % 7)
        return datetime.combine(sunday, self.cold_restart, tzinfo=ET)

    def in_weekend_maintenance(self, ts: datetime) -> bool:
        """Saturday 00:00 ET up to Sunday's cold restart."""
        local = ts.astimezone(ET)
        if local.weekday() == SATURDAY:
            return True
        return local.weekday() == SUNDAY and local < self.cold_restart_on(local)

    def is_weekend(self, ts: datetime) -> bool:
        return ts.astimezone(ET).weekday() in (SATURDAY, SUNDAY)

    def last_cold_restart(self, ts: datetime) -> datetime:
        cold = self.cold_restart_on(ts)
        return cold if cold <= ts.astimezone(ET) else cold - timedelta(days=7)

    # ---- classification ------------------------------------------------------------------
    def classify_outage(self, started: datetime, now: datetime) -> OutageAssessment:
        """Why the session is down and whether to alert yet (IB-013..IB-015)."""
        window = self.daily_window_containing(started)
        if window is not None:
            if now < window[1] + self.grace:
                return OutageAssessment(OutageReason.DAILY_RESTART, None, False)
            # A failed token reuse after the daily restart needs a fresh login.
            return OutageAssessment(OutageReason.DAILY_RESTART_OVERRUN, self._severity(now), True)

        if self.in_weekend_maintenance(started):
            cold = self.cold_restart_on(started)
            if now < cold:
                return OutageAssessment(OutageReason.WEEKEND_MAINTENANCE, None, False)
            alert = Severity.CRITICAL if now >= cold + self.grace else None
            return OutageAssessment(OutageReason.WEEKLY_REAUTH, alert, True)

        last_cold = self.last_cold_restart(started)
        if started.astimezone(ET).weekday() == SUNDAY and started.astimezone(ET) >= last_cold:
            alert = Severity.CRITICAL if now >= started + self.grace else None
            return OutageAssessment(OutageReason.WEEKLY_REAUTH, alert, True)

        if now < started + self.grace:
            return OutageAssessment(OutageReason.DISCONNECTED, None, False)
        return OutageAssessment(OutageReason.GATEWAY_UNREACHABLE, self._severity(now), True)

    def _severity(self, now: datetime) -> Severity:
        return Severity.WARNING if self.is_weekend(now) else Severity.CRITICAL

    # ---- order time in force -------------------------------------------------------------
    @staticmethod
    def tif_lapsed(tif: str, outside_rth: bool, placed_at: datetime, now: datetime) -> bool:
        """IB-024: has a DAY/OPG/CLS order's validity ended (used to tell expiry from cancel)?"""
        placed = placed_at.astimezone(ET)
        local = now.astimezone(ET)
        tif = tif.upper()
        if tif == "DAY":
            end = time(20, 0) if outside_rth else time(16, 0)
        elif tif == "OPG":
            end = time(9, 30)
        elif tif in {"CLS", "MOC", "LOC"}:
            end = time(16, 0)
        else:
            return False
        cutoff = datetime.combine(placed.date(), end, tzinfo=ET)
        if placed >= cutoff:
            cutoff += timedelta(days=1)
        return local >= cutoff
