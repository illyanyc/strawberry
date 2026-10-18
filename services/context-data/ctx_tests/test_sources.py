"""Source adapters against recorded fixtures (section 09; rules CX-004..CX-013)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from tb_context_data.http import HttpSource
from tb_context_data.records import Versioner
from tb_context_data.resolve import Resolver
from tb_context_data.sentiment import LexiconBackend, QuarantinedScorer
from tb_context_data.sources.alfred import Alfred
from tb_context_data.sources.edgar import Edgar
from tb_context_data.sources.finra import Finra, settlement_dates
from tb_context_data.sources.fmp import FMP
from tb_context_data.sources.news import NewsPipeline

from tb.broker.v1 import market_data_pb2
from tbcore.context import availability as av
from tbcore.data.lake import Lake

from .kit import IDS, fixture, fixture_transport, write_refs

NS = 1_000_000_000
CFG = av.AvailabilityConfig()
NOW = int(datetime(2026, 10, 18, 5, 0, tzinfo=UTC).timestamp()) * NS


def et(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> int:
    return av.et_ns(date(y, mo, d), datetime(2000, 1, 1, h, mi).time()) + s * NS


@pytest.fixture
def resolver(tmp_path: Path) -> Resolver:
    write_refs(tmp_path)
    return Resolver(Lake(tmp_path))


def fmp(resolver: Resolver, report_times: bool = False) -> FMP:
    return FMP(
        HttpSource("fmp", "http://fmp.test", 0, transport=fixture_transport({})), "k", resolver, CFG, report_times
    )


# ---------------------------------------------------------------- FMP


def test_cx007_fmp_earnings_two_versions_and_unknown_timing_is_amc(resolver: Resolver) -> None:
    recs = {
        r.record_key: r
        for r in fmp(resolver).earnings_records(
            fixture("context", "fmp", "earnings_calendar.json"), {"AAPL", "MSFT", "NVDA"}
        )
    }
    assert set(recs) == {"AAPL|2026Q3", "MSFT|2026Q3", "NVDA|2026Q3"}  # ZZZZ is outside the universe
    aapl = recs["AAPL|2026Q3"]
    assert aapl.instrument_ids == (IDS["AAPL"],)
    sched, rep = aapl.versions
    assert sched.payload["phase"] == "scheduled" and "eps_actual" not in sched.payload
    assert sched.available_at == av.earnings_schedule_available_at(date(2026, 7, 30))
    assert rep.payload["eps_surprise"] == "0.14" and rep.payload["timing"] == "unknown"
    assert rep.available_at == et(2026, 7, 30, 20, 0)  # unknown timing: after the extended session
    assert len(recs["NVDA|2026Q3"].versions) == 1  # no result yet


def test_cx007_fmp_report_times_when_available(resolver: Resolver) -> None:
    rows = fixture("context", "fmp", "earnings_calendar_report_times.json")
    recs = {r.record_key: r for r in fmp(resolver, True).earnings_records(rows)}
    assert recs["NVDA|2026Q3"].versions[0].payload["timing"] == "bmo"
    assert recs["AAPL|2026Q3"].versions[-1].available_at == et(2026, 7, 30, 20, 0)


def test_cx006_fmp_economic_calendar_us_categories_and_actuals(resolver: Resolver) -> None:
    recs = fmp(resolver).economic_records(fixture("context", "fmp", "economic_calendar.json"), {"US"})
    by = {r.record_key: r for r in recs}
    assert "DE|Inflation Rate YoY Final|2026-09-11T06:00" not in by
    cpi = by["US|CPI YoY|2026-09-11T12:30"]
    assert cpi.event_time == et(2026, 9, 11, 8, 30)
    sched, rel = cpi.versions
    assert sched.payload["category"] == "CPI" and sched.payload["estimate"] == "2.8" and "actual" not in sched.payload
    assert rel.payload["actual"] == "2.7" and rel.payload["surprise"] == "-0.1"
    assert rel.available_at == et(2026, 9, 11, 8, 31)  # release + 60 s
    assert sched.available_at == et(2026, 9, 4, 8, 30)  # assumed public 7 days before
    fomc = by["US|Fed Interest Rate Decision|2026-09-16T18:00"]
    assert fomc.versions[0].payload["category"] == "FOMC" and len(fomc.versions) == 1


def test_cx008_fundamentals_first_seen_accepted_date_restatement_at_fetch(resolver: Resolver) -> None:
    f = fmp(resolver)
    v = Versioner()
    first = v.stamp(f.statement_records("AAPL", "income", fixture("context", "fmp", "income_statement_AAPL.json")), NOW)
    q2 = next(o for o in first if o.record_key == "AAPL|income|2026Q2")
    assert q2.available_at == av.fundamentals_available_at("2026-05-01 18:03:12")
    assert q2.payload["net_income"] == "24780000000" and q2.payload["eps"] == "1.65"
    assert (
        v.stamp(
            f.statement_records("AAPL", "income", fixture("context", "fmp", "income_statement_AAPL.json")), NOW + NS
        )
        == []
    )
    later = NOW + 86_400 * NS
    restated = v.stamp(
        f.statement_records("AAPL", "income", fixture("context", "fmp", "income_statement_AAPL_restated.json")), later
    )
    assert [(o.record_key, o.available_at, o.payload["net_income"]) for o in restated] == [
        ("AAPL|income|2026Q2", later, "24510000000")
    ]


# ---------------------------------------------------------------- ALFRED


def test_cx006_alfred_vintages_authoritative_times() -> None:
    a = Alfred(HttpSource("alfred", "http://fred.test", 0, transport=fixture_transport({})), "k", CFG)
    obs = fixture("context", "alfred", "series_observations_PAYEMS.json")["observations"]
    recs = {r.record_key: r for r in a.records("PAYEMS", obs)}
    march = recs["PAYEMS|2026-03-01"]
    assert [v.vintage for v in march.versions] == ["2026-04-03", "2026-05-08", "2026-06-05"]
    assert march.versions[0].available_at == et(2026, 4, 3, 8, 30)
    assert recs["PAYEMS|2026-06-01"].versions[0].payload["value"] == ""  # "." = missing
    v = Versioner()
    assert len(v.stamp(a.records("PAYEMS", obs[:2]), NOW)) == 2
    # A new vintage fetched weeks later keeps its official release time (authoritative).
    new = v.stamp(
        [r for r in a.records("PAYEMS", obs[:3]) if r.record_key == "PAYEMS|2026-03-01"], NOW + 30 * 86_400 * NS
    )
    assert [(o.vintage, o.available_at) for o in new] == [("2026-06-05", et(2026, 6, 5, 8, 30))]


def test_alfred_request_parameters() -> None:
    t = fixture_transport(
        {"/fred/series/observations": fixture("context", "alfred", "series_observations_VIXCLS.json")}
    )
    a = Alfred(HttpSource("alfred", "http://fred.test", 0, transport=t), "secretkey", CFG)
    recs = asyncio.run(a.series("VIXCLS", date(2026, 10, 1)))
    q = t.seen[0].url.params  # type: ignore[attr-defined]
    assert q["output_type"] == "1" and q["realtime_start"] == "1776-07-04" and q["observation_start"] == "2026-10-01"
    assert len(recs) == 5 and recs[-1].versions[0].payload["value"] == "16.46"


# ---------------------------------------------------------------- FINRA


def test_cx005_finra_recorded_short_interest_keyed_by_publication(resolver: Resolver) -> None:
    f = Finra(HttpSource("finra", "http://finra.test", 0, transport=fixture_transport({})), resolver, CFG)
    recs = {
        r.record_key: r
        for r in f.records(fixture("context", "finra", "consolidated_short_interest_2026-09-30.json"), NOW)
    }
    aapl = recs["AAPL|2026-09-30"]
    v = aapl.versions[0]
    assert v.payload["publication_date"] == "2026-10-09" and v.payload["basis"] == "publication_schedule"
    assert v.available_at == et(2026, 10, 9, 23, 59)
    assert v.payload["days_to_cover"] == "3.03" and v.payload["short_interest"] == "120763539"
    assert aapl.event_time < v.available_at


def test_cx005_finra_revised_record_first_seen_at_fetch(resolver: Resolver) -> None:
    rows = fixture("context", "finra", "consolidated_short_interest_2026-09-15.json")
    rows[0]["revisionFlag"] = "R"
    f = Finra(HttpSource("finra", "http://finra.test", 0, transport=fixture_transport({})), resolver, CFG)
    rec = next(r for r in f.records(rows, NOW) if r.symbols == (rows[0]["symbolCode"],))
    assert rec.versions[0].available_at == NOW and rec.versions[0].payload["basis"] == "revised_first_seen"


def test_cx005_finra_settlement_dates_match_published_schedule() -> None:
    sched = av.load_finra_schedule()
    got = settlement_dates(date(2025, 11, 1), date(2026, 12, 31))
    assert got == sorted(sched)


def test_cx005_finra_settlement_dates_window_edges() -> None:
    assert settlement_dates(date(2026, 9, 8), date(2026, 10, 6)) == [date(2026, 9, 15), date(2026, 9, 30)]
    assert settlement_dates(date(2026, 10, 15), date(2026, 10, 15)) == [date(2026, 10, 15)]
    assert settlement_dates(date(2026, 2, 14), date(2026, 2, 26)) == []  # 02-13 is the mid-month date


def test_finra_request_body_filters(resolver: Resolver) -> None:
    t = fixture_transport(
        {
            "/data/group/otcMarket/name/consolidatedShortInterest": fixture(
                "context", "finra", "consolidated_short_interest_2026-09-30.json"
            )
        }
    )
    f = Finra(HttpSource("finra", "http://finra.test", 0, transport=t), resolver, CFG)
    recs = asyncio.run(f.short_interest(date(2026, 9, 30), ["AAPL", "MSFT", "NVDA"], NOW))
    body = t.seen[0].read()  # type: ignore[attr-defined]
    assert b'"settlementDate"' in body and b'"symbolCode"' in body and len(recs) == 3


# ---------------------------------------------------------------- EDGAR


def test_cx009_edgar_recorded_filings_conservative_and_immutable(resolver: Resolver) -> None:
    t = fixture_transport(
        {
            "/files/company_tickers.json": fixture("context", "edgar", "company_tickers.json"),
            "/submissions/CIK0000320193.json": fixture("context", "edgar", "submissions_CIK0000320193.json"),
        }
    )
    e = Edgar(
        HttpSource("edgar", "", 0, transport=t),
        resolver,
        CFG,
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov",
    )
    recs = asyncio.run(e.filings("AAPL", {"4"}))
    assert recs and all(r.versions[0].payload["form"] == "4" for r in recs) and all(r.authoritative for r in recs)
    f4 = next(r for r in recs if r.record_key == "0001140361-26-039785")
    true_acceptance = et(2026, 10, 15, 18, 30, 17)  # filing header ACCEPTANCE-DATETIME (New York)
    assert f4.versions[0].available_at >= true_acceptance
    assert f4.versions[0].payload["url"].endswith("/000114036126039785/xslF345X06/form4.xml")
    assert t.seen[0].headers["user-agent"]  # type: ignore[attr-defined]


# ---------------------------------------------------------------- news


def articles() -> list[market_data_pb2.NewsArticle]:
    out = []
    for n in fixture("alpaca", "news.json")["news"]:

        def ns(s: str) -> int:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()) * NS

        out.append(
            market_data_pb2.NewsArticle(
                id=str(n["id"]),
                headline=n["headline"],
                summary=n["summary"],
                content=n["content"],
                author=n["author"],
                source=n["source"],
                url=n["url"],
                symbols=n["symbols"],
                created_at=ns(n["created_at"]),
                updated_at=ns(n["updated_at"]),
            )
        )
    return out


def test_cx010_to_cx013_news_pipeline_backfill(resolver: Resolver) -> None:
    p = NewsPipeline(resolver, QuarantinedScorer(LexiconBackend()), CFG, store_body=True)
    recs = p.records(articles(), NOW)
    news = {r.record_key: r for r in recs if r.kind == "news"}
    sent = {r.record_key.split("|")[0]: r for r in recs if r.kind == "news_sentiment"}
    first = news["41230001"]
    created = int(datetime(2026, 7, 30, 20, 31, 5, tzinfo=UTC).timestamp()) * NS
    assert first.versions[0].available_at == created + 60 * NS  # first publication + delay, not updated_at
    body = first.versions[0].payload["body"]
    assert "<" not in body and "alert(1)" not in body and first.versions[0].payload["untrusted"] is True
    assert first.versions[0].payload["revised"] is True
    assert news["41230002"].versions[0].payload["duplicate_of"] == "41230001"  # syndicated copy
    msft = news["41230003"]
    assert msft.instrument_ids == (IDS["MSFT"], IDS["NVDA"])
    assert "\x00" not in msft.versions[0].payload["summary"] and "\x1b" not in msft.versions[0].payload["summary"]
    # The injected instruction is data: the score follows the content (cuts, weak), not the instruction.
    assert sent["41230003"].versions[0].payload["score"] < 0 and sent["41230001"].versions[0].payload["score"] > 0
    s = sent["41230001"].versions[0]
    assert s.payload["model_version"] == "lexicon-v1" and s.payload["contaminated"] is False
    assert s.available_at == created + 60 * NS + 2 * NS  # backfill: article availability + scoring latency
    assert sent["41230002"].versions[0].payload["duplicate"] is True


def test_cx010_news_received_live_is_never_available_before_receipt(resolver: Resolver) -> None:
    p = NewsPipeline(resolver, QuarantinedScorer(LexiconBackend()), CFG)
    created = int(datetime(2026, 7, 30, 20, 31, 5, tzinfo=UTC).timestamp()) * NS
    fetched = created + 90 * NS  # polled 90 s after publication (> 60 s delay)
    recs = p.records(articles()[:1], fetched)
    assert recs[0].versions[0].available_at == fetched
    assert recs[1].versions[0].available_at >= fetched  # live score: when scored
    assert "body" not in recs[0].versions[0].payload  # bodies only when enabled (licensing, CX-010)


def test_cx012_sentiment_deterministic_for_fixed_model(resolver: Resolver) -> None:
    def scores() -> list[float]:
        p = NewsPipeline(resolver, QuarantinedScorer(LexiconBackend()), CFG)
        return [r.versions[0].payload["score"] for r in p.records(articles(), NOW) if r.kind == "news_sentiment"]

    assert scores() == scores()
