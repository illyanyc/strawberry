"""Opt-in tests against the real public APIs (no keys exist in CI). Run with
``TB_CTX_LIVE_TEST=1 uv run pytest services/context-data/ctx_tests -m ctx_live`` and, for keyed
sources, ``TB_CTX_FMP_KEY`` / ``TB_CTX_FRED_KEY`` in the environment (never committed)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from tb_context_data.http import HttpSource
from tb_context_data.resolve import Resolver
from tb_context_data.sources.alfred import Alfred
from tb_context_data.sources.edgar import Edgar
from tb_context_data.sources.finra import Finra, settlement_dates
from tb_context_data.sources.fmp import FMP

from tbcore.context import availability as av
from tbcore.data.lake import Lake

from .kit import write_refs

pytestmark = [
    pytest.mark.ctx_live,
    pytest.mark.skipif(os.environ.get("TB_CTX_LIVE_TEST") != "1", reason="opt-in: TB_CTX_LIVE_TEST=1"),
]
CFG = av.AvailabilityConfig()


@pytest.fixture
def resolver(tmp_path: Path) -> Resolver:
    write_refs(tmp_path)
    return Resolver(Lake(tmp_path))


def test_live_finra_short_interest(resolver: Resolver) -> None:
    f = Finra(HttpSource("finra", "https://api.finra.org", 30, headers={"Accept": "application/json"}), resolver, CFG)
    sd = settlement_dates(date.today() - timedelta(days=40), date.today() - timedelta(days=12))[-1]
    recs = asyncio.run(f.short_interest(sd, ["AAPL", "MSFT"], 0))
    assert recs and all(r.versions[0].available_at > av.et_ns(sd, av.parse_hhmm("16:00")) for r in recs)


def test_live_edgar_submissions(resolver: Resolver) -> None:
    ua = os.environ.get("TB_CTX_SEC_USER_AGENT", "")
    if not ua:
        pytest.skip("TB_CTX_SEC_USER_AGENT not set")
    e = Edgar(
        HttpSource("edgar", "", 60, headers={"User-Agent": ua}),
        resolver,
        CFG,
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov",
    )
    assert asyncio.run(e.filings("AAPL", set()))


def test_live_fred_vintages() -> None:
    key = os.environ.get("TB_CTX_FRED_KEY", "")
    if not key:
        pytest.skip("TB_CTX_FRED_KEY not set")
    recs = asyncio.run(
        Alfred(HttpSource("alfred", "https://api.stlouisfed.org", 60), key, CFG).series(
            "PAYEMS", date.today() - timedelta(days=200)
        )
    )
    assert any(len(r.versions) > 1 for r in recs)  # revised periods carry several vintages


def test_live_fmp_calendars(resolver: Resolver) -> None:
    key = os.environ.get("TB_CTX_FMP_KEY", "")
    if not key:
        pytest.skip("TB_CTX_FMP_KEY not set")
    f = FMP(HttpSource("fmp", "https://financialmodelingprep.com", 30), key, resolver, CFG)
    d = date.today()
    assert asyncio.run(f.economic(d - timedelta(days=7), d + timedelta(days=7), {"US"}))
