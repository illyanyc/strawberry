"""SEC EDGAR filing metadata from the submissions API (CX-009).

The SEC requires a descriptive ``User-Agent`` with a contact address and allows 10 requests per
second; the source is disabled until ``TB_CTX_SEC_USER_AGENT`` is set. Filings are immutable, so
each accession is one authoritative version available at its acceptance time (conservatively
parsed, see ``tbcore.context.availability.edgar_available_at``) plus a delay. Only metadata is
stored; filing text is untrusted and never fetched here.
"""

from __future__ import annotations

from typing import Any

from tb_context_data.http import HttpSource
from tb_context_data.records import Record, Version
from tb_context_data.resolve import Resolver
from tbcore.context import availability as av


class Edgar:
    def __init__(
        self, http: HttpSource, resolver: Resolver, cfg: av.AvailabilityConfig, tickers_url: str, base: str
    ) -> None:
        self.http = http
        self.resolver = resolver
        self.cfg = cfg
        self.tickers_url = tickers_url
        self.base = base.rstrip("/")
        self._ciks: dict[str, int] = {}

    async def ciks(self) -> dict[str, int]:
        """Current ticker → CIK map (SEC ``company_tickers.json``)."""
        if not self._ciks:
            body = await self.http.get(self.tickers_url)
            self._ciks = {str(v["ticker"]).upper(): int(v["cik_str"]) for v in (body or {}).values()}
        return self._ciks

    async def filings(self, symbol: str, forms: set[str]) -> list[Record]:
        cik = (await self.ciks()).get(symbol.upper())
        if cik is None:
            return []
        body = await self.http.get(f"{self.base}/submissions/CIK{cik:010d}.json")
        return self.records(symbol, cik, (body or {}).get("filings", {}).get("recent", {}), forms)

    def records(self, symbol: str, cik: int, recent: dict[str, list[Any]], forms: set[str]) -> list[Record]:
        out = []
        n = len(recent.get("accessionNumber", []))
        for i in range(n):
            form = str(recent["form"][i])
            if forms and form not in forms:
                continue
            acc, accepted = str(recent["accessionNumber"][i]), str(recent["acceptanceDateTime"][i])
            if not acc or not accepted:
                continue
            at = av.edgar_available_at(accepted, self.cfg)
            inst = self.resolver.resolve(symbol, at)
            if inst is None:
                continue
            doc = str(recent.get("primaryDocument", [""] * n)[i])
            payload = {
                "cik": str(cik),
                "accession": acc,
                "form": form,
                "filing_date": str(recent.get("filingDate", [""] * n)[i]),
                "report_date": str(recent.get("reportDate", [""] * n)[i]),
                "acceptance_raw": accepted,
                "items": str(recent.get("items", [""] * n)[i]),
                "primary_document": doc,
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}",
            }
            out.append(
                Record(
                    "filing",
                    "edgar",
                    acc,
                    at - self.cfg.edgar_delay_s * av.NS,
                    (Version(payload, at),),
                    (inst,),
                    (symbol.upper(),),
                    authoritative=True,
                )
            )
        return out
