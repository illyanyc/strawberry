"""FMP ``/stable`` earnings calendar, economic calendar, and financial statements (section 09).

FMP restates data in place with no ``as_of`` (research 04 §4.3), so every fetch is a snapshot and
changes become new versions at fetch time (CX-004). Earnings and calendar records carry two
versions when first seen with a result: the schedule (assumed public ``*_schedule_lead_days``
before) and the result (CX-006, CX-007). Builds against ``/stable`` only (spike V3).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from tb_context_data.http import HttpSource
from tb_context_data.records import Record, Version
from tb_context_data.resolve import Resolver
from tb_context_data.sources import NS, dstr, sub, utc_midnight_ns
from tbcore.context import availability as av
from tbcore.context.events import categorize

STATEMENTS = {
    "income": (
        "/stable/income-statement",
        {
            "revenue": "revenue",
            "net_income": "netIncome",
            "eps": "eps",
            "eps_diluted": "epsDiluted",
            "shares_diluted": "weightedAverageShsOutDil",
        },
    ),
    "balance": (
        "/stable/balance-sheet-statement",
        {
            "total_assets": "totalAssets",
            "total_liabilities": "totalLiabilities",
            "total_equity": "totalStockholdersEquity",
        },
    ),
    "cashflow": (
        "/stable/cash-flow-statement",
        {"operating_cash_flow": "operatingCashFlow", "free_cash_flow": "freeCashFlow"},
    ),
}


def report_quarter(d: date) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


class FMP:
    def __init__(
        self, http: HttpSource, key: str, resolver: Resolver, cfg: av.AvailabilityConfig, report_times: bool = False
    ) -> None:
        self.http = http
        self.key = key
        self.resolver = resolver
        self.cfg = cfg
        self.report_times = report_times

    async def _get(self, path: str, **params: Any) -> Any:
        return await self.http.get(path, {**params, "apikey": self.key})

    async def earnings(self, start: date, end: date, symbols: set[str] | None = None) -> list[Record]:
        params: dict[str, Any] = {"from": start.isoformat(), "to": end.isoformat()}
        if self.report_times:
            params["includeReportTimes"] = "true"
        rows = await self._get("/stable/earnings-calendar", **params)
        return self.earnings_records(rows or [], symbols)

    def earnings_records(self, rows: list[dict[str, Any]], symbols: set[str] | None = None) -> list[Record]:
        out = []
        for r in rows:
            sym = str(r.get("symbol", "")).upper()
            if not sym or (symbols is not None and sym not in symbols) or not r.get("date"):
                continue
            d = date.fromisoformat(str(r["date"])[:10])
            timing = av.parse_timing(r.get("time"))
            when = av.earnings_actual_available_at(d, timing, self.cfg)
            inst = self.resolver.resolve(sym, when)
            if inst is None:
                continue
            est, act = dstr(r.get("epsEstimated")), dstr(r.get("epsActual"))
            base = {
                "report_date": d.isoformat(),
                "timing": timing.value,
                "eps_estimate": est,
                "revenue_estimate": dstr(r.get("revenueEstimated")),
            }
            versions = [
                Version(
                    {**base, "phase": "scheduled", "basis": "assumed_lead"},
                    av.earnings_schedule_available_at(d, self.cfg),
                )
            ]
            if act:
                versions.append(
                    Version(
                        {
                            **base,
                            "phase": "reported",
                            "basis": "timing_rule",
                            "eps_actual": act,
                            "eps_surprise": sub(act, est),
                            "revenue_actual": dstr(r.get("revenueActual")),
                        },
                        when,
                    )
                )
            out.append(Record("earnings", "fmp", f"{sym}|{report_quarter(d)}", when, tuple(versions), (inst,), (sym,)))
        return out

    async def economic(self, start: date, end: date, countries: set[str]) -> list[Record]:
        rows = await self._get("/stable/economic-calendar", **{"from": start.isoformat(), "to": end.isoformat()})
        return self.economic_records(rows or [], countries)

    def economic_records(self, rows: list[dict[str, Any]], countries: set[str]) -> list[Record]:
        out = []
        for r in rows:
            country = str(r.get("country", "")).upper()
            if countries and country not in countries:
                continue
            event = str(r.get("event", "")).strip()
            raw = str(r.get("date", ""))
            if not event or not raw:
                continue
            rel = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)  # FMP calendar times are UTC
            rel_ns = int(rel.timestamp()) * NS
            est, act = dstr(r.get("estimate")), dstr(r.get("actual"))
            base = {
                "event": event,
                "country": country,
                "currency": str(r.get("currency") or ""),
                "category": categorize(event),
                "impact": str(r.get("impact") or ""),
                "release_time": rel.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "previous": dstr(r.get("previous")),
                "estimate": est,
                "unit": str(r.get("unit") or ""),
            }
            versions = [
                Version(
                    {**base, "phase": "scheduled", "basis": "assumed_lead"},
                    av.econ_schedule_available_at(rel_ns, self.cfg),
                )
            ]
            if act:
                versions.append(
                    Version(
                        {
                            **base,
                            "phase": "released",
                            "basis": "release_rule",
                            "actual": act,
                            "surprise": sub(act, est),
                        },
                        av.econ_actual_available_at(rel_ns, self.cfg),
                    )
                )
            key = f"{country}|{event}|{rel.strftime('%Y-%m-%dT%H:%M')}"
            out.append(Record("calendar", "fmp", key, rel_ns, tuple(versions)))
        return out

    async def fundamentals(
        self, symbol: str, statements: tuple[str, ...] = ("income", "balance", "cashflow"), limit: int = 8
    ) -> list[Record]:
        out: list[Record] = []
        for st in statements:
            path, _ = STATEMENTS[st]
            rows = await self._get(path, symbol=symbol, period="quarter", limit=limit)
            out += self.statement_records(symbol, st, rows or [])
        return out

    def statement_records(self, symbol: str, statement: str, rows: list[dict[str, Any]]) -> list[Record]:
        _, fields = STATEMENTS[statement]
        out = []
        for r in rows:
            acc = str(r.get("acceptedDate") or "")
            if not acc or not r.get("date"):
                continue  # no vendor acceptance time: cannot place it in time (CX-008)
            rule = av.fundamentals_available_at(acc, self.cfg)
            inst = self.resolver.resolve(symbol, rule)
            if inst is None:
                continue
            period = f"{r.get('fiscalYear', '')}{r.get('period', '')}"
            payload = {
                "statement": statement,
                "period": str(r.get("period") or ""),
                "fiscal_year": str(r.get("fiscalYear") or ""),
                "period_end": str(r["date"])[:10],
                "filing_date": str(r.get("filingDate") or ""),
                "accepted_date": acc,
                "basis": "accepted_date",
                **{k: dstr(r.get(v)) for k, v in fields.items()},
            }
            out.append(
                Record(
                    "fundamentals",
                    "fmp",
                    f"{symbol}|{statement}|{period}",
                    utc_midnight_ns(date.fromisoformat(str(r["date"])[:10])),
                    (Version(payload, rule),),
                    (inst,),
                    (symbol,),
                )
            )
        return out
