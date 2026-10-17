"""Connection supervision for one IB Gateway API connection (OE-004, OE-008; IB-010..IB-016).

State machine::

    disconnected -> connecting -> authenticated -> syncing -> ready
                        ^              |              |          |
                        |              v              v          v
                        +---------- (failure) <--- degraded <----+

* Only ``ready`` accepts orders (OE-004). ``ready`` is reached only after the post-connect check
  (accounts and environment, IB-005/IB-006) and a full resync (IB-016) succeed.
* 1100/2110 degrade the session in place; 1101/1102 trigger a full resync before ``ready`` again
  (IB-012). Socket loss or a failed heartbeat drops the connection and reconnects with jittered
  exponential backoff (IB-011).
* While not ``ready``, :meth:`MaintenanceSchedule.classify_outage` decides whether the outage is
  expected (daily restart, weekend maintenance) or needs the owner (re-auth / 2FA), and raises one
  deduplicated alert per outage, escalating warning -> critical (IB-013..IB-015). Reaching
  ``ready`` resolves it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from tb_broker_ibkr.backoff import Backoff
from tb_broker_ibkr.clock import Clock, to_ns, with_timeout
from tb_broker_ibkr.error_codes import Action, classify
from tb_broker_ibkr.events import RUNBOOK_2FA, RUNBOOK_OUTAGE, AlertEvent, EventSink
from tb_broker_ibkr.models import Environment, SessionState, SessionStatus
from tb_broker_ibkr.port import IbPort
from tb_broker_ibkr.schedule import MaintenanceSchedule, OutageReason, Severity

log = logging.getLogger("tb_broker_ibkr.session")

_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


class FatalSessionError(Exception):
    """The gateway is reachable but must not be used (e.g. environment mismatch, IB-006)."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class SessionMetrics(Protocol):
    def set_state(self, connection: str, state: SessionState) -> None: ...

    def set_reauth_required(self, connection: str, value: bool) -> None: ...

    def reconnect_attempt(self, connection: str) -> None: ...

    def heartbeat(self, connection: str, when: datetime) -> None: ...


class NullMetrics:
    def set_state(self, connection: str, state: SessionState) -> None:
        return None

    def set_reauth_required(self, connection: str, value: bool) -> None:
        return None

    def reconnect_attempt(self, connection: str) -> None:
        return None

    def heartbeat(self, connection: str, when: datetime) -> None:
        return None


class _Event(StrEnum):
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    connection: str  # "orders" | "marketdata"
    environment: Environment
    heartbeat_interval: float = 15.0
    heartbeat_timeout: float = 10.0
    stable_after: float = 300.0  # seconds of ready before the backoff resets
    degrade_codes: frozenset[int] = frozenset({1100, 2110})
    weekly_reminder: bool = True


class SessionSupervisor:
    """Keeps one connection alive and gates readiness on a successful resync."""

    def __init__(
        self,
        *,
        config: SupervisorConfig,
        port: IbPort,
        clock: Clock,
        schedule: MaintenanceSchedule,
        sink: EventSink,
        after_connect: Callable[[list[str]], Awaitable[None]],
        resync: Callable[[bool], Awaitable[None]],
        status_targets: Callable[[], Sequence[str]] = lambda: (),
        status_overlay: Callable[[SessionStatus], SessionStatus] = lambda s: s,
        metrics: SessionMetrics | None = None,
        backoff: Backoff | None = None,
    ) -> None:
        self._cfg = config
        self._port = port
        self._clock = clock
        self._schedule = schedule
        self._sink = sink
        self._after_connect = after_connect
        self._resync = resync
        self._targets = status_targets
        self._overlay = status_overlay
        self._metrics = metrics or NullMetrics()
        self._backoff = backoff or Backoff()
        self._events: asyncio.Queue[tuple[_Event, int, str]] = asyncio.Queue()
        self._stopping = False
        self.state = SessionState.DISCONNECTED
        self.reasons: tuple[str, ...] = ()
        self.reauth_required = False
        self.since = clock.now()
        self.last_heartbeat: datetime | None = None
        self._outage_started: datetime | None = clock.now()
        self._alerted: Severity | None = None
        self._ready_since: datetime | None = None
        self._reminded_for: datetime | None = None
        self._fatal: str | None = None
        self._sticky: str | None = None
        self._reminder_open = False

    # ---- inputs from port handlers ------------------------------------------------------------
    def notify_disconnected(self) -> None:
        self._events.put_nowait((_Event.DISCONNECTED, 0, ""))

    def notify_error(self, code: int, message: str) -> None:
        self._events.put_nowait((_Event.ERROR, code, message))

    @property
    def dedupe_key(self) -> str:
        return f"ibkr-{self._cfg.environment.value}-{self._cfg.connection}-session"

    @property
    def _reminder_key(self) -> str:
        return f"ibkr-{self._cfg.environment.value}-weekly-login"

    @property
    def is_ready(self) -> bool:
        return self.state is SessionState.READY

    def status_for(self, broker_account_id: str) -> SessionStatus:
        return SessionStatus(
            broker_account_id=broker_account_id,
            state=self.state,
            connected=self._port.is_connected(),
            authenticated=self.state
            in {SessionState.AUTHENTICATED, SessionState.SYNCING, SessionState.READY, SessionState.DEGRADED}
            and self._port.is_connected(),
            last_heartbeat=to_ns(self.last_heartbeat) if self.last_heartbeat else 0,
            degraded_reasons=self.reasons,
            reauth_required=self.reauth_required,
            since=to_ns(self.since),
        )

    def stop(self) -> None:
        self._stopping = True
        self._events.put_nowait((_Event.DISCONNECTED, 0, "stop"))

    async def fail_closed(self, reason: str, message: str) -> None:
        """Block orders until the process restarts (a configuration fault, not a transient one)."""
        self._sticky = reason
        self._fatal = reason
        await self._set(SessionState.DEGRADED, (reason,))
        await self._raise(Severity.CRITICAL, message, RUNBOOK_OUTAGE)

    # ---- main loop ----------------------------------------------------------------------------
    async def run(self) -> None:
        while not self._stopping:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let a bug end supervision: fail closed, log, and retry with backoff.
                log.exception("session supervisor error", extra={"connection": self._cfg.connection})
                self._port.disconnect()
                await self._set(SessionState.DEGRADED, ("supervisor_error",))
                await self._clock.sleep(self._backoff.next_delay())
        if self.state is not SessionState.STOPPED:
            await self._set(SessionState.DISCONNECTED, ("stopped",))

    async def _cycle(self) -> None:
        """One connect -> sync -> watch round; returns when the connection is gone."""
        await self._set(SessionState.CONNECTING, self.reasons)
        self._metrics.reconnect_attempt(self._cfg.connection)
        try:
            accounts = await self._port.connect()
        except Exception as exc:
            log.warning(
                "gateway connect failed", extra={"connection": self._cfg.connection, "error": type(exc).__name__}
            )
            await self._down_and_wait()
            return

        await self._set(SessionState.AUTHENTICATED, ())
        try:
            await self._after_connect(accounts)
            if self._sticky:
                raise FatalSessionError(self._sticky, f"orders blocked until restart: {self._sticky}")
            self._fatal = None
        except FatalSessionError as exc:
            self._fatal = exc.reason
            await self._set(SessionState.DEGRADED, (exc.reason,))
            await self._raise(Severity.CRITICAL, str(exc), RUNBOOK_OUTAGE)
            await self._watch()
            return

        if await self._sync(resubscribe=False):
            await self._watch()

    async def _sync(self, *, resubscribe: bool) -> bool:
        await self._set(SessionState.SYNCING, ())
        try:
            await self._resync(resubscribe)
        except Exception:
            log.exception("resync failed; reconnecting", extra={"connection": self._cfg.connection})
            self._port.disconnect()
            await self._set(SessionState.DEGRADED, ("resync_failed",))
            await self._down_and_wait()
            return False
        await self._became_ready()
        return True

    async def _became_ready(self) -> None:
        await self._set(SessionState.READY, ())
        self._ready_since = self._clock.now()
        self._outage_started = None
        self._set_reauth(False)
        if self._alerted is not None:
            await self._sink.resolve_alert(self.dedupe_key, f"IBKR {self._cfg.environment.value} session ready again")
            self._alerted = None
        if self._reminder_open:
            self._reminder_open = False
            await self._sink.resolve_alert(self._reminder_key, "Weekly IBKR login completed")

    async def _watch(self) -> None:
        """Run while connected; return when the connection is gone."""
        while not self._stopping:
            get = asyncio.ensure_future(self._events.get())
            tick = asyncio.ensure_future(self._clock.sleep(self._cfg.heartbeat_interval))
            done, _ = await asyncio.wait({get, tick}, return_when=asyncio.FIRST_COMPLETED)
            event: tuple[_Event, int, str] | None = None
            if get in done:
                event = get.result()
            else:
                get.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get
                if get.done() and not get.cancelled():
                    event = get.result()
            tick.cancel()

            if event is not None:
                if not await self._handle(event):
                    return
                continue
            if not await self._heartbeat():
                return
            await self._periodic()

    async def _handle(self, event: tuple[_Event, int, str]) -> bool:
        kind, code, _message = event
        if kind is _Event.DISCONNECTED:
            await self._went_down("disconnected")
            return False
        info = classify(code)
        if code in self._cfg.degrade_codes:
            await self._went_down(info.category.value + f"_{code}", keep_connection=True)
            return True
        if info.action is Action.RECONNECT:
            self._port.disconnect()
            await self._went_down(f"socket_reset_{code}")
            return False
        if info.action in (Action.RESYNC, Action.RESYNC_RESUBSCRIBE) and self._fatal is None:
            return await self._sync(resubscribe=info.action is Action.RESYNC_RESUBSCRIBE)
        return True

    async def _heartbeat(self) -> bool:
        try:
            await with_timeout(self._clock, self._port.current_time(), self._cfg.heartbeat_timeout)
        except Exception:
            log.warning("gateway heartbeat failed; reconnecting", extra={"connection": self._cfg.connection})
            self._port.disconnect()
            await self._went_down("heartbeat_timeout")
            return False
        self.last_heartbeat = self._clock.now()
        self._metrics.heartbeat(self._cfg.connection, self.last_heartbeat)
        if (
            self.state is SessionState.READY
            and self._ready_since is not None
            and self._clock.now() - self._ready_since >= timedelta(seconds=self._cfg.stable_after)
        ):
            self._backoff.reset()
        if self.state is SessionState.DEGRADED:
            await self._assess()
        return True

    async def _went_down(self, reason: str, *, keep_connection: bool = False) -> None:
        if self._outage_started is None:
            self._outage_started = self._clock.now()
        state = SessionState.DEGRADED if keep_connection else SessionState.DISCONNECTED
        await self._set(state, (reason,))
        await self._assess()

    async def _down_and_wait(self) -> None:
        """Connection attempt failed: classify, alert if due, then back off."""
        if self._outage_started is None:
            self._outage_started = self._clock.now()
        if self.state is not SessionState.DEGRADED:
            await self._set(SessionState.DISCONNECTED, self.reasons or ("disconnected",))
        await self._assess()
        await self._periodic()
        await self._clock.sleep(self._backoff.next_delay())

    async def _assess(self) -> None:
        started = self._outage_started or self._clock.now()
        assessment = self._schedule.classify_outage(started, self._clock.now())
        reasons = tuple(dict.fromkeys((*self.reasons, assessment.reason.value)))
        if self._fatal:
            reasons = (self._fatal,)
        reauth = assessment.reauth_required and (
            assessment.alert is not None or assessment.reason is OutageReason.WEEKLY_REAUTH
        )
        self._set_reauth(reauth)
        if reasons != self.reasons:
            await self._set(self.state, reasons)
        if assessment.alert is None:
            return
        if self._alerted is not None and _SEVERITY_RANK[self._alerted] >= _SEVERITY_RANK[assessment.alert]:
            return
        env = self._cfg.environment.value
        minutes = int((self._clock.now() - started).total_seconds() // 60)
        if assessment.reauth_required and assessment.reason is OutageReason.WEEKLY_REAUTH:
            message = (
                f"IBKR {env} gateway needs the weekly login: approve the IBKR Mobile push "
                f"(down {minutes} min). IBKR strategies are blocked until the session is ready."
            )
            runbook = RUNBOOK_2FA
        else:
            message = (
                f"IBKR {env} gateway unreachable for {minutes} min ({assessment.reason.value}). "
                "A gateway restart mid-week needs a fresh login with 2FA; see the runbook."
            )
            runbook = RUNBOOK_OUTAGE
        await self._raise(assessment.alert, message, runbook)

    async def _raise(self, severity: Severity, message: str, runbook: str) -> None:
        self._alerted = severity
        await self._sink.alert(
            AlertEvent(
                severity=severity,
                subject=f"broker.ibkr.{self._cfg.environment.value}.session",
                message=message,
                dedupe_key=self.dedupe_key,
                runbook=runbook,
            )
        )

    async def _periodic(self) -> None:
        """Weekly reminder at the Sunday cold restart (IB-014)."""
        if not self._cfg.weekly_reminder:
            return
        now = self._clock.now()
        cold = self._schedule.last_cold_restart(now)
        if self._reminded_for == cold or now - cold > timedelta(hours=1):
            return
        self._reminded_for = cold
        self._reminder_open = True
        await self._sink.alert(
            AlertEvent(
                severity=Severity.WARNING,
                subject=f"broker.ibkr.{self._cfg.environment.value}.weekly_login",
                message=(
                    f"Weekly IBKR {self._cfg.environment.value} login started: approve the IBKR Mobile "
                    "2FA push now. Strategies on IBKR stay blocked until the session is reconciled."
                ),
                dedupe_key=self._reminder_key,
                runbook=RUNBOOK_2FA,
            )
        )

    def _set_reauth(self, value: bool) -> None:
        self.reauth_required = value
        self._metrics.set_reauth_required(self._cfg.connection, value)

    async def _set(self, state: SessionState, reasons: tuple[str, ...]) -> None:
        if state is self.state and reasons == self.reasons:
            return
        if state is not self.state:
            self.since = self._clock.now()
        self.state = state
        self.reasons = reasons
        self._metrics.set_state(self._cfg.connection, state)
        for target in self._targets():
            await self._sink.session_status(self._overlay(self.status_for(target)))

    async def mark_stopped(self, reason: str) -> None:
        """BR-013: the adapter is deliberately not running (e.g. live gate closed)."""
        self._stopping = True
        await self._set(SessionState.STOPPED, (reason,))
