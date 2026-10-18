"""End to end: jobs → versions → (sink) lake → point-in-time queries; idempotent reruns; freshness
alerts; the gRPC news client; spike V6 (section 09 Gate 3 item)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import grpc
import httpx
import polars as pl
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from prometheus_client import CollectorRegistry
from tb_context_data.app import Sources, build_engine, build_jobs, build_sources, regime_records
from tb_context_data.config import Settings
from tb_context_data.http import HttpSource, SourceUnavailable
from tb_context_data.jobs import Engine, Job, Metrics
from tb_context_data.publish import MemoryPublisher
from tb_context_data.records import Out, Record, Version, Versioner
from tb_context_data.resolve import Resolver
from tb_context_data.sources.alfred import Alfred
from tb_context_data.sources.edgar import Edgar
from tb_context_data.sources.finra import Finra
from tb_context_data.sources.fmp import FMP
from tb_context_data.sources.news import GrpcNewsSource
from tb_context_data.spike_v6 import run_spike_v6

from tb.broker.v1 import market_data_pb2
from tbcore.context import ContextStore, context_features
from tbcore.context import availability as av
from tbcore.data.lake import Lake
from tbcore.svcauth import Signer

from .kit import IDS, fixture, fixture_transport, sink, write_refs, write_spy_daily
from .test_sources import articles

NS = 1_000_000_000
NOW = int(datetime(2026, 10, 18, 5, 0, tzinfo=UTC).timestamp()) * NS


class FakeNews:
    def __init__(self, arts: Sequence[market_data_pb2.NewsArticle]) -> None:
        self.arts = list(arts)
        self.calls: list[tuple[int, int, list[str]]] = []

    async def fetch(
        self, start_ns: int, end_ns: int, symbols: Sequence[str], include_content: bool
    ) -> list[market_data_pb2.NewsArticle]:
        self.calls.append((start_ns, end_ns, list(symbols)))
        return [a for a in self.arts if start_ns <= a.created_at < end_ns]


def sources(resolver: Resolver, news: FakeNews) -> Sources:
    cfg = av.AvailabilityConfig()
    fmp_t = fixture_transport(
        {
            "/stable/economic-calendar": fixture("context", "fmp", "economic_calendar.json"),
            "/stable/earnings-calendar": fixture("context", "fmp", "earnings_calendar.json"),
            "/stable/income-statement": lambda r: (
                fixture("context", "fmp", f"income_statement_{r.url.params['symbol']}.json")
                if r.url.params["symbol"] == "AAPL"
                else []
            ),
            "/stable/balance-sheet-statement": lambda r: (
                fixture("context", "fmp", "balance_sheet_statement_AAPL.json")
                if r.url.params["symbol"] == "AAPL"
                else []
            ),
            "/stable/cash-flow-statement": lambda r: (
                fixture("context", "fmp", "cash_flow_statement_AAPL.json") if r.url.params["symbol"] == "AAPL" else []
            ),
        }
    )
    fred_t = fixture_transport(
        {
            "/fred/series/observations": lambda r: fixture(
                "context", "alfred", f"series_observations_{r.url.params['series_id']}.json"
            )
        }
    )
    finra_t = fixture_transport(
        {
            "/data/group/otcMarket/name/consolidatedShortInterest": lambda r: (
                fixture(
                    "context",
                    "finra",
                    f"consolidated_short_interest_{json.loads(r.content)['compareFilters'][0]['fieldValue']}.json",
                )
                if json.loads(r.content)["compareFilters"][0]["fieldValue"] in ("2026-09-15", "2026-09-30")
                else []
            )
        }
    )
    sec_t = fixture_transport(
        {
            "/files/company_tickers.json": fixture("context", "edgar", "company_tickers.json"),
            "/submissions/CIK0000320193.json": fixture("context", "edgar", "submissions_CIK0000320193.json"),
        }
    )
    return Sources(
        fmp=FMP(HttpSource("fmp", "http://fmp.test", 0, transport=fmp_t), "k", resolver, cfg),
        alfred=Alfred(HttpSource("alfred", "http://fred.test", 0, transport=fred_t), "k", cfg),
        finra=Finra(HttpSource("finra", "http://finra.test", 0, transport=finra_t), resolver, cfg),
        edgar=Edgar(
            HttpSource("edgar", "", 0, transport=sec_t),
            resolver,
            cfg,
            "https://www.sec.gov/files/company_tickers.json",
            "https://data.sec.gov",
        ),
        news=news,
    )


def settings(**kw: Any) -> Settings:
    return Settings(ctx_fred_series="PAYEMS,VIXCLS", ctx_edgar_forms="4", **kw)


def run_all(engine: Engine) -> int:
    return sum(asyncio.run(engine.run_job(j)) for j in engine.jobs)


def test_gate3_context_jobs_land_point_in_time_and_rerun_adds_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    write_refs(tmp_path)
    lake = Lake(tmp_path)
    s = settings()
    news = FakeNews(articles())
    pub = MemoryPublisher()
    engine, pipeline = build_engine(s, pub, CollectorRegistry(), sources(Resolver(lake), news), lake)
    engine.now = lambda: NOW / NS
    # News job looks back one hour on its first run; widen it to cover the fixture's day.
    monkeypatch.setattr(pipeline, "live_window_ns", 300 * NS)
    names = {j.name for j in engine.jobs}
    assert names == {"calendar", "earnings", "fundamentals", "macro", "short_interest", "filings", "news", "regime"}
    n = run_all(engine)
    kinds = {o.kind for o in pub.published}
    assert {"calendar", "earnings", "fundamentals", "macro", "short_interest", "filing"} <= kinds
    sink(tmp_path, pub.published)
    # Rerun with the same vendor data: zero new versions (CX-004), even with a fresh versioner
    # reading the stored state from the lake.
    pub2 = MemoryPublisher()
    engine2, _ = build_engine(s, pub2, CollectorRegistry(), sources(Resolver(lake), FakeNews([])), lake)
    engine2.now = lambda: (NOW + 3600 * NS) / NS
    assert run_all(engine2) == 0 and n > 0
    store = ContextStore(lake)
    # ALFRED: the vintage known at each time (DF-010).
    assert store.macro_value("PAYEMS", date(2026, 3, 1), datetime(2026, 5, 1, tzinfo=UTC)) == Decimal("158720")
    assert store.macro_value("PAYEMS", date(2026, 3, 1), datetime(2026, 7, 1, tzinfo=UTC)) == Decimal("158650")
    # FINRA: by publication date (CX-005).
    si = store.short_interest(IDS["AAPL"], datetime(2026, 10, 5, tzinfo=UTC))
    assert si is not None and si["settlement_date"] == "2026-09-15"
    si = store.short_interest(IDS["AAPL"], datetime(2026, 10, 12, tzinfo=UTC))
    assert si is not None and si["settlement_date"] == "2026-09-30"
    # Fundamentals snapshot as of after acceptance.
    fund = store.fundamentals(IDS["AAPL"], datetime(2026, 8, 2, tzinfo=UTC))
    assert fund["period"].to_list() == ["Q2", "Q3"]
    assert store.fundamentals(IDS["AAPL"], datetime(2026, 7, 31, 12, tzinfo=UTC))["period"].to_list() == ["Q2"]
    # EDGAR filings visible only after their (conservative) acceptance time.
    assert (
        store.filings(IDS["AAPL"], datetime(2026, 10, 15, 22, tzinfo=UTC)).height
        < store.filings(IDS["AAPL"], datetime(2026, 10, 18, tzinfo=UTC)).height
    )


def test_gate3_news_sentiment_and_calendar_queries(tmp_path: Path) -> None:
    write_refs(tmp_path)
    lake = Lake(tmp_path)
    news = FakeNews(articles())
    pub = MemoryPublisher()
    engine, _ = build_engine(settings(), pub, CollectorRegistry(), sources(Resolver(lake), news), lake)
    fetched = int(datetime(2026, 7, 30, 21, 5, tzinfo=UTC).timestamp()) * NS
    engine.now = lambda: fetched / NS
    asyncio.run(engine.run_job(next(j for j in engine.jobs if j.name == "news")))
    assert news.calls and news.calls[0][2] == ["AAPL", "MSFT", "NVDA"]
    sink(tmp_path, pub.published)
    store = ContextStore(lake)
    got = store.news_for(
        IDS["AAPL"],
        datetime(2026, 7, 30, tzinfo=UTC),
        datetime(2026, 7, 31, tzinfo=UTC),
        datetime(2026, 7, 31, tzinfo=UTC),
    )
    assert got["news_id"].to_list() == ["41230001"]  # syndicated copy excluded
    agg = store.sentiment_agg(IDS["AAPL"], timedelta(hours=24), datetime(2026, 7, 31, tzinfo=UTC))
    assert agg.count == 1 and agg.mean is not None and agg.mean > 0 and agg.novelty == 0.5
    f = context_features(
        pl.DataFrame({"instrument_id": [IDS["MSFT"]], "decision_time": [datetime(2026, 7, 31, 14, tzinfo=UTC)]}), store
    )
    assert f["ctx_news_count_24h"][0] == 1.0 and f["ctx_sent_mean_24h"][0] < 0


def test_regime_rows_from_lake_bars_and_vix(tmp_path: Path) -> None:
    write_refs(tmp_path)
    write_spy_daily(tmp_path, IDS["SPY"])
    lake = Lake(tmp_path)
    store = ContextStore(lake)
    now = int(datetime(2026, 10, 16, 22, tzinfo=UTC).timestamp()) * NS
    recs = regime_records(lake, store, Resolver(lake), now)
    assert len(recs) > 250 and recs[-1].versions[0].payload["version"] == "regime_v1"
    outs = Versioner(store).stamp(recs, now)
    sink(tmp_path, outs)
    assert regime_records(lake, ContextStore(lake), Resolver(lake), now) == []  # incremental: nothing new
    r = ContextStore(lake).regime(datetime(2026, 10, 16, 22, tzinfo=UTC))
    assert r is not None and r["vol_regime"] in {"high", "normal", "low"}


def test_cx019_stale_source_alerts_once_and_recovers() -> None:
    pub = MemoryPublisher()
    alerts: list[str] = []
    calls = {"n": 0}

    async def flaky(fetched: int) -> list[Record]:
        calls["n"] += 1
        if calls["n"] < 4:
            raise RuntimeError("upstream 503")
        return [Record("calendar", "fmp", "k", fetched, (Version({"event": "x"}, fetched),))]

    async def alert(key: str, subject: str, message: str) -> None:
        alerts.append(key)

    clock = {"t": 1000.0}
    eng = Engine(
        pub,
        Versioner(),
        Metrics(CollectorRegistry()),
        [Job("calendar", "fmp", 60, flaky)],
        3.0,
        alert,
        lambda: clock["t"],
    )
    for _ in range(3):
        asyncio.run(eng.run_job(eng.jobs[0]))
        clock["t"] += 100
    assert alerts == ["ctx-stale-calendar"]  # once, not per failure
    assert pub.healths[-1][3] == "stale"
    asyncio.run(eng.run_job(eng.jobs[0]))
    assert pub.healths[-1][3] == "ok" and len(pub.published) == 1


def test_out_event_and_msg_id_stable() -> None:
    o = Out("macro", "alfred", "PAYEMS|2026-03-01", 1, 2, {"value": "1"}, (), (), "2026-04-03")
    assert o.msg_id == Out("macro", "alfred", "PAYEMS|2026-03-01", 1, 2, {"value": "1"}, (), (), "2026-04-03").msg_id
    ev = o.event(3)
    assert ev.record_key == "PAYEMS|2026-03-01" and ev.provenance.available_at == 2 and ev.vintage == "2026-04-03"
    assert ev.payload["value"] == "1"


def test_grpc_news_source_pages_with_signed_service_token() -> None:
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    signer = Signer("context-data", seed)
    seen: list[tuple[str, str]] = []

    async def scenario() -> list[market_data_pb2.NewsArticle]:
        arts = articles()

        def get_news(
            req: market_data_pb2.GetNewsRequest, ctx: grpc.aio.ServicerContext
        ) -> market_data_pb2.GetNewsResponse:
            md = dict(ctx.invocation_metadata() or ())
            seen.append((str(md.get("authorization", "")), req.page_token))
            if req.page_token == "":
                return market_data_pb2.GetNewsResponse(articles=arts[:2], next_page_token="p2")  # noqa: S106 - page token, not a secret
            return market_data_pb2.GetNewsResponse(articles=arts[2:])

        async def handler(
            req: market_data_pb2.GetNewsRequest, ctx: grpc.aio.ServicerContext
        ) -> market_data_pb2.GetNewsResponse:
            return get_news(req, ctx)

        server = grpc.aio.server()
        server.add_generic_rpc_handlers(
            (
                grpc.method_handlers_generic_handler(
                    "tb.broker.v1.BrokerMarketDataService",
                    {
                        "GetNews": grpc.unary_unary_rpc_method_handler(
                            handler,
                            request_deserializer=market_data_pb2.GetNewsRequest.FromString,
                            response_serializer=market_data_pb2.GetNewsResponse.SerializeToString,
                        )
                    },
                ),
            )
        )
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        try:
            async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as ch:
                return await GrpcNewsSource(ch, signer).fetch(0, 2 * 10**18, ["AAPL"], False)
        finally:
            await server.stop(None)

    got = asyncio.run(scenario())
    assert [a.id for a in got] == ["41230001", "41230002", "41230003"]
    assert [p for _, p in seen] == ["", "p2"]
    token = seen[0][0].removeprefix("TB-Service ")
    body, sig = token.rsplit(".", 1)
    key.public_key().verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), body.encode())
    claims = json.loads(base64.urlsafe_b64decode(body.split(".")[1] + "=="))
    assert claims["iss"] == "context-data" and claims["aud"] == "broker-gateway" and claims["exp"] - claims["iat"] == 60


def test_spike_v6_reports_timing_fields_without_leaking_the_key() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.params
        if req.url.path == "/stable/earnings-calendar":
            row = {"symbol": "AAPL", "date": p["from"], "epsActual": 1.0}
            if p.get("includeReportTimes") == "true":
                row["time"] = "bmo" if p["from"] == "2026-07-14" else "amc"
            return httpx.Response(200, json=[{**row, "symbol": s} for s in ("AAPL", "MSFT", "JPM", "NVDA")])
        return httpx.Response(200, json=[{"symbol": p["symbol"], "date": "2026-07-30"}])

    report = asyncio.run(run_spike_v6("SECRETKEY123", "http://fmp.test", httpx.MockTransport(handler)))
    assert report["summary"]["timing_fields_found"] == ["time"]
    assert report["summary"]["recommendation"].startswith("set TB_CTX_FMP_REPORT_TIMES=true")
    assert "SECRETKEY123" not in json.dumps(report)
    assert asyncio.run(run_spike_v6(""))["summary"]["status"] == "skipped"


def test_disabled_sources_without_credentials(tmp_path: Path) -> None:
    s = Settings(secrets_dir=str(tmp_path), ctx_news_enabled=True)
    src = build_sources(s, Resolver(Lake(tmp_path)))
    assert src.fmp is None and src.alfred is None and src.edgar is None and src.news is None
    assert set(src.disabled) == {"fmp", "alfred", "edgar", "news"} and src.finra is not None  # FINRA needs no key
    jobs = build_jobs(s, src, Lake(tmp_path), ContextStore(Lake(tmp_path)), Resolver(Lake(tmp_path)), None)  # type: ignore[arg-type]
    assert {j.name for j in jobs} == {"short_interest", "regime"}


def test_unavailable_source_reports_without_alerting() -> None:
    pub = MemoryPublisher()
    alerts: list[str] = []

    async def no_news(fetched: int) -> list[Record]:
        raise SourceUnavailable("no market-data account")

    async def alert(key: str, subject: str, message: str) -> None:
        alerts.append(key)

    clock = {"t": 1000.0}
    eng = Engine(
        pub,
        Versioner(),
        Metrics(CollectorRegistry()),
        [Job("news", "alpaca.news", 15, no_news)],
        3.0,
        alert,
        lambda: clock["t"],
    )
    for _ in range(5):
        asyncio.run(eng.run_job(eng.jobs[0]))
        clock["t"] += 100
    assert (
        alerts == [] and eng.jobs[0].unavailable and pub.healths[-1][3] == "unavailable" and eng.jobs[0].failures == 0
    )
