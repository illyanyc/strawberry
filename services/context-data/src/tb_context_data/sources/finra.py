"""FINRA consolidated short interest, keyed by publication date (CX-005).

The Query API (``otcMarket/consolidatedShortInterest``, no credentials for this public dataset)
carries settlement dates only; availability is the official publication date from the seeded
schedule (fallback: settlement + N sessions) at the configured New York time. A record already
revised by FINRA when first seen (``revisionFlag``) cannot be placed at its original publication
and is stamped at the fetch time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from tb_context_data.http import HttpSource
from tb_context_data.records import Record, Version
from tb_context_data.resolve import Resolver
from tb_context_data.sources import dstr
from tbcore.context import availability as av
from tbcore.data.calendar import Calendar, xnys

PATH = "/data/group/otcMarket/name/consolidatedShortInterest"
PAGE = 5000


def settlement_dates(start: date, end: date, cal: Calendar | None = None) -> list[date]:
    """FINRA settlement dates in ``[start, end]``: the 15th and the month end, each moved back to the
    previous XNYS session when it is not one (matches the published 2025-26 schedule)."""
    c = cal or xnys()
    # Sessions over whole months: a target after ``end`` must resolve to its own session, never walk
    # back into the window.
    after = date(end.year + (end.month == 12), end.month % 12 + 1, 1) + timedelta(days=1)
    sessions = {
        s.date
        for s in c.sessions(
            datetime.combine(date(start.year, start.month, 1) - timedelta(days=10), datetime.min.time(), UTC),
            datetime.combine(after, datetime.min.time(), UTC),
        )
    }
    out: set[date] = set()
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        for target in (date(y, m, 15), nxt - timedelta(days=1)):
            d = target
            while d not in sessions and d > target - timedelta(days=10):
                d -= timedelta(days=1)
            if d in sessions and start <= d <= end:
                out.add(d)
        y, m = nxt.year, nxt.month
    return sorted(out)


class Finra:
    def __init__(
        self, http: HttpSource, resolver: Resolver, cfg: av.AvailabilityConfig, schedule: dict[date, date] | None = None
    ) -> None:
        self.http = http
        self.resolver = resolver
        self.cfg = cfg
        self.schedule = schedule if schedule is not None else av.load_finra_schedule()

    async def short_interest(self, settlement: date, symbols: list[str], fetched_ns: int) -> list[Record]:
        rows: list[dict[str, Any]] = []
        for i in range(0, len(symbols), 1000):
            offset = 0
            while True:
                body = {
                    "limit": PAGE,
                    "offset": offset,
                    "compareFilters": [
                        {"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": settlement.isoformat()}
                    ],
                    "domainFilters": [{"fieldName": "symbolCode", "values": symbols[i : i + 1000]}],
                }
                page = await self.http.request("POST", PATH, json=body)
                page = page if isinstance(page, list) else []
                rows += page
                if len(page) < PAGE:
                    break
                offset += PAGE
        return self.records(rows, fetched_ns)

    def records(self, rows: list[dict[str, Any]], fetched_ns: int) -> list[Record]:
        out = []
        for r in rows:
            sym, sd = str(r.get("symbolCode", "")).upper(), str(r.get("settlementDate", ""))
            if not sym or not sd:
                continue
            settle = date.fromisoformat(sd)
            pub = av.finra_publication_date(settle, self.schedule, cfg=self.cfg)
            rule = av.finra_available_at(settle, self.schedule, cfg=self.cfg)
            revised = r.get("revisionFlag") not in (None, "", "N")
            basis = (
                "revised_first_seen"
                if revised
                else ("publication_schedule" if settle in self.schedule else "publication_fallback")
            )
            at = max(rule, fetched_ns) if revised else rule
            inst = self.resolver.resolve(sym, rule)
            if inst is None:
                continue
            payload = {
                "settlement_date": sd,
                "publication_date": pub.isoformat(),
                "short_interest": dstr(r.get("currentShortPositionQuantity")),
                "previous_short_interest": dstr(r.get("previousShortPositionQuantity")),
                "avg_daily_volume": dstr(r.get("averageDailyVolumeQuantity")),
                "days_to_cover": dstr(r.get("daysToCoverQuantity")),
                "change_pct": dstr(r.get("changePercent")),
                "revision_flag": str(r.get("revisionFlag") or ""),
                "basis": basis,
            }
            out.append(
                Record(
                    "short_interest",
                    "finra",
                    f"{sym}|{sd}",
                    av.et_ns(settle, av.parse_hhmm("16:00")),
                    (Version(payload, at),),
                    (inst,),
                    (sym,),
                )
            )
        return out
