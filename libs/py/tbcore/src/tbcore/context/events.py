"""Scheduled market events and the ``EventBlackout`` strategy filter (CX-015, CX-016).

An :class:`EventSource` returns events *known at* a decision time (``available_at <= now``), so the
same filter is point-in-time correct in backtests (lake-backed :class:`tbcore.context.ContextStore`)
and in paper/live (the strategy runtime feeds :class:`InMemoryEventSource` from ``ctx.calendar``).

:class:`BlackoutStrategy` wraps any strategy and drops its *opening* intents while a matching event
is within ``[event - before, event + after]``. Closing intents always pass (risk reduction is never
blocked).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from tbcore.strategy.api import Bar, Fill, Intent, Strategy, StrategyContext

NS = 1_000_000_000

#: Economic-calendar event names → categories (US releases). Order matters: first match wins.
CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FOMC", re.compile(r"\b(fomc|fed(eral)? (funds )?(interest )?rate decision|fed interest rate)\b", re.I)),
    ("CPI", re.compile(r"\b(core )?(cpi|consumer price index)\b", re.I)),
    ("PPI", re.compile(r"\b(core )?(ppi|producer price index)\b", re.I)),
    ("NFP", re.compile(r"\b(non ?-?farm payrolls?|nonfarm payrolls?)\b", re.I)),
    ("UNEMPLOYMENT", re.compile(r"\bunemployment rate\b", re.I)),
    ("PCE", re.compile(r"\b(core )?pce\b|personal consumption expenditures", re.I)),
    ("GDP", re.compile(r"\bgdp\b|gross domestic product", re.I)),
    ("RETAIL_SALES", re.compile(r"\bretail sales\b", re.I)),
    ("JOBLESS_CLAIMS", re.compile(r"\b(initial )?jobless claims\b", re.I)),
    ("ISM", re.compile(r"\bism\b", re.I)),
)


def categorize(event_name: str) -> str:
    """Category of an economic-calendar event name ("" when none matches)."""
    for cat, pat in CATEGORY_PATTERNS:
        if pat.search(event_name):
            return cat
    return ""


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    """An event known at some time: macro release (``category``) or earnings (``instrument_id``)."""

    kind: str  # "calendar" | "earnings"
    name: str
    at: int  # scheduled time, UTC ns (earnings: report time per timing)
    available_at: int
    category: str = ""
    impact: str = ""
    country: str = ""
    instrument_id: str = ""
    record_key: str = ""


class EventSource(Protocol):
    def events(self, as_of: int, start: int, end: int) -> Sequence[ScheduledEvent]:
        """Events scheduled in ``[start, end]`` using only versions with ``available_at <= as_of``."""
        ...


@dataclass
class InMemoryEventSource:
    """Event source fed from the bus (paper/live). Later versions of a record replace earlier ones
    only once they are available, so lookups stay point-in-time."""

    _versions: dict[str, list[ScheduledEvent]] = field(default_factory=dict)

    def add(self, ev: ScheduledEvent) -> None:
        key = ev.record_key or f"{ev.kind}|{ev.name}|{ev.at}"
        vs = self._versions.setdefault(key, [])
        vs.append(ev)
        vs.sort(key=lambda e: e.available_at)

    def extend(self, evs: Iterable[ScheduledEvent]) -> None:
        for e in evs:
            self.add(e)

    def events(self, as_of: int, start: int, end: int) -> list[ScheduledEvent]:
        out = []
        for vs in self._versions.values():
            known = [v for v in vs if v.available_at <= as_of]
            if known and start <= known[-1].at <= end:
                out.append(known[-1])
        return sorted(out, key=lambda e: e.at)


IMPACT_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class EventBlackout:
    """No new positions from ``before`` until ``after`` around matching events (CX-016).

    ``kinds`` holds macro categories ("CPI", "FOMC", ...) and/or "earnings" (the instrument's own
    report). ``min_impact`` filters macro events by vendor impact; ``countries`` defaults to US.
    """

    kinds: tuple[str, ...]
    before_ns: int
    after_ns: int
    min_impact: str = ""
    countries: tuple[str, ...] = ("US",)

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> EventBlackout:
        kinds = d.get("kinds", ())
        countries = d.get("countries", ("US",))
        return cls(
            kinds=tuple(str(k) for k in kinds) if isinstance(kinds, list | tuple) else (),
            before_ns=int(float(str(d.get("before_minutes", 0))) * 60 * NS),
            after_ns=int(float(str(d.get("after_minutes", 0))) * 60 * NS),
            min_impact=str(d.get("min_impact", "")),
            countries=tuple(str(c) for c in countries) if isinstance(countries, list | tuple) else ("US",),
        )

    def matches(self, ev: ScheduledEvent, instrument_id: str) -> bool:
        if ev.kind == "earnings":
            return "earnings" in self.kinds and ev.instrument_id == instrument_id
        if ev.category not in self.kinds:
            return False
        if self.countries and ev.country and ev.country.upper() not in self.countries:
            return False
        need = IMPACT_RANK.get(self.min_impact.lower(), 0)
        return IMPACT_RANK.get(ev.impact.lower(), 3 if not ev.impact else 0) >= need

    def active(self, source: EventSource, now: int, instrument_id: str) -> ScheduledEvent | None:
        """The matching event whose blackout window contains ``now``, if any (point-in-time)."""
        for ev in source.events(now, now - self.after_ns, now + self.before_ns):
            if self.matches(ev, instrument_id) and ev.at - self.before_ns <= now <= ev.at + self.after_ns:
                return ev
        return None


class BlackoutStrategy:
    """Wraps a strategy; opening intents inside an active blackout are dropped (CX-016)."""

    def __init__(self, inner: Strategy, blackout: EventBlackout, source: EventSource) -> None:
        self.inner = inner
        self.blackout = blackout
        self.source = source
        self.strategy_id = inner.strategy_id
        self.blocked: list[tuple[int, str, str]] = []  # (decision time, instrument, event name)

    def filter_opens(self, ctx: StrategyContext, intents: list[Intent]) -> list[Intent]:
        """Drop opening intents inside an active blackout (also used for webhook signals, SM-020)."""
        return self._filter(ctx, intents)

    def _filter(self, ctx: StrategyContext, intents: list[Intent]) -> list[Intent]:
        out = []
        for it in intents:
            if it.kind == "open":
                ev = self.blackout.active(self.source, ctx.now, it.instrument_id)
                if ev is not None:
                    self.blocked.append((ctx.now, it.instrument_id, ev.name))
                    continue
            out.append(it)
        return out

    def on_start(self, ctx: StrategyContext) -> None:
        self.inner.on_start(ctx)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> list[Intent]:
        return self._filter(ctx, self.inner.on_bar(ctx, bar))

    def on_fill(self, ctx: StrategyContext, fill: Fill) -> list[Intent]:
        return self._filter(ctx, self.inner.on_fill(ctx, fill))

    def on_session_close(self, ctx: StrategyContext) -> list[Intent]:
        return self._filter(ctx, self.inner.on_session_close(ctx))
