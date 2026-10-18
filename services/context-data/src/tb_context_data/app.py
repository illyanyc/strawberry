"""Wiring: sources → jobs → engine (section 09). Sources without credentials or contact details are
not scheduled and are reported in the startup log and readiness detail (fail closed: no guessing)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import grpc
import httpx
import polars as pl

from tb_context_data.config import Settings
from tb_context_data.http import HttpSource
from tb_context_data.jobs import Engine, Job, Metrics
from tb_context_data.publish import Publisher
from tb_context_data.records import Record, Version, Versioner
from tb_context_data.resolve import Resolver
from tb_context_data.sentiment import Backend, LexiconBackend, LocalLLMBackend, QuarantinedScorer
from tb_context_data.sources.alfred import Alfred
from tb_context_data.sources.edgar import Edgar
from tb_context_data.sources.finra import Finra, settlement_dates
from tb_context_data.sources.fmp import FMP
from tb_context_data.sources.news import GrpcNewsSource, NewsPipeline, NewsSource
from tbcore.context.regime import REGIME_VERSION, compute_regimes
from tbcore.context.store import ContextStore
from tbcore.data.lake import Lake
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.svcauth import Signer

log = logging.getLogger("context-data")
NS = 1_000_000_000


def secret(name: str, settings: Settings) -> str:
    try:
        v = get_secret(name, settings.secrets_dir).decode().strip()
    except SecretNotFoundError:
        return ""
    return "" if v in ("", "unset") else v


def make_backend(s: Settings, transport: httpx.BaseTransport | None = None) -> Backend:
    if s.ctx_sentiment_scorer == "local_llm":
        return LocalLLMBackend(
            url=s.ctx_llm_url,
            model=s.ctx_llm_model,
            version=s.ctx_llm_model_version,
            training_cutoff=s.ctx_llm_training_cutoff,
            allowed_hosts=tuple(s.list(s.ctx_llm_allowed_hosts)),
            timeout=s.ctx_llm_timeout_seconds,
            transport=transport,
        )
    if s.ctx_sentiment_scorer != "lexicon":
        raise ValueError(f"unknown TB_CTX_SENTIMENT_SCORER {s.ctx_sentiment_scorer!r}")
    return LexiconBackend()


@dataclass
class Sources:
    fmp: FMP | None = None
    alfred: Alfred | None = None
    finra: Finra | None = None
    edgar: Edgar | None = None
    news: NewsSource | None = None
    disabled: dict[str, str] = field(default_factory=dict)
    http: list[HttpSource] = field(default_factory=list)


def build_sources(
    s: Settings,
    resolver: Resolver,
    transports: dict[str, httpx.AsyncBaseTransport] | None = None,
    news: NewsSource | None = None,
) -> Sources:
    tr = transports or {}
    cfg = s.availability()
    out = Sources()
    fmp_key = secret("fmp_api_key", s)
    if fmp_key or "fmp" in tr:
        h = HttpSource("fmp", s.ctx_fmp_base, s.ctx_fmp_rate_per_min, transport=tr.get("fmp"))
        out.fmp, out.http = FMP(h, fmp_key, resolver, cfg, s.ctx_fmp_report_times), [*out.http, h]
    else:
        out.disabled["fmp"] = "fmp_api_key not set"
    fred_key = secret("fred_api_key", s)
    if fred_key or "alfred" in tr:
        h = HttpSource("alfred", s.ctx_fred_base, 100, transport=tr.get("alfred"))
        out.alfred, out.http = Alfred(h, fred_key, cfg), [*out.http, h]
    else:
        out.disabled["alfred"] = "fred_api_key not set (free key from fred.stlouisfed.org)"
    h = HttpSource("finra", s.ctx_finra_base, 60, headers={"Accept": "application/json"}, transport=tr.get("finra"))
    out.finra, out.http = Finra(h, resolver, cfg), [*out.http, h]
    if s.ctx_sec_user_agent or "edgar" in tr:
        h = HttpSource(
            "edgar",
            "",
            300,
            headers={"User-Agent": s.ctx_sec_user_agent or "strawberry-tests test@example.com"},
            transport=tr.get("edgar"),
        )
        out.edgar, out.http = Edgar(h, resolver, cfg, s.ctx_sec_tickers_url, s.ctx_sec_base), [*out.http, h]
    else:
        out.disabled["edgar"] = "TB_CTX_SEC_USER_AGENT not set (SEC requires a contact)"
    if news is not None:
        out.news = news
    elif s.ctx_news_enabled:
        try:
            signer: Signer | None = Signer.from_secrets("context-data", s.secrets_dir)
        except (SecretNotFoundError, ValueError):
            signer = None
            out.disabled["news"] = "svc_context_data_ed25519 not mounted"
        if signer is not None:
            out.news = GrpcNewsSource(grpc.aio.insecure_channel(s.broker_gateway_addr), signer)
    return out


def _today(fetched_ns: int) -> date:
    return datetime.fromtimestamp(fetched_ns / NS, tz=UTC).date()


def regime_records(
    lake: Lake, store: ContextStore, resolver: Resolver, fetched_ns: int, lookback_days: int = 800
) -> list[Record]:
    """New daily regime rows since the last stored one (CX-017)."""
    spy = resolver.resolve("SPY", fetched_ns)
    if spy is None:
        return []
    start = fetched_ns - lookback_days * 86_400 * NS
    bars = lake.bars([spy], "1d", start, fetched_ns, as_of=fetched_ns, adjust="split_div")
    if bars.is_empty():
        return []
    macro = store.versions("macro", fetched_ns, source="alfred")

    def series(sid: str) -> pl.DataFrame | None:
        if macro.is_empty():
            return None
        f = macro.filter((pl.col("series_id") == sid) & (pl.col("value").fill_null("") != ""))
        return f.select("available_at", pl.col("value").cast(pl.Float64)) if not f.is_empty() else None

    rows = compute_regimes(bars.select("ts_close", "close", "available_at"), series("VIXCLS"), series("VXVCLS"))
    last = store.regime(fetched_ns, REGIME_VERSION)
    if last is not None:
        rows = rows.filter(pl.col("event_time") > last["event_time"])
    out = []
    for r in rows.iter_rows(named=True):
        ev = int(r["event_time"].timestamp()) * NS
        payload: dict[str, Any] = {
            k: (round(v, 6) if isinstance(v, float) else v)
            for k, v in r.items()
            if k not in ("event_time", "available_at")
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        out.append(
            Record(
                "regime",
                "context-data",
                f"{REGIME_VERSION}|{r['event_time'].date().isoformat()}",
                ev,
                (Version(payload, int(r["available_at"].timestamp() * 1e6) * 1000),),
                authoritative=True,
            )
        )
    return out


async def per_symbol(name: str, symbols: list[str], fetch: Callable[[str], Awaitable[list[Record]]]) -> list[Record]:
    """Run a per-symbol fetch; one symbol's failure is logged and skipped, all failing fails the job."""
    out: list[Record] = []
    failed: list[str] = []
    for sym in symbols:
        try:
            out += await fetch(sym)
        except Exception as exc:
            failed.append(sym)
            log.warning("context fetch failed", extra={"job": name, "symbol": sym, "err": str(exc)[:200]})
    if symbols and len(failed) == len(symbols):
        raise RuntimeError(f"{name}: every symbol failed")
    return out


def build_jobs(
    s: Settings, src: Sources, lake: Lake, store: ContextStore, resolver: Resolver, pipeline: NewsPipeline
) -> list[Job]:
    override = s.list(s.ctx_symbols)

    def universe(fetched: int) -> list[str]:
        return resolver.universe_symbols(s.ctx_universe, fetched, override)

    jobs: list[Job] = []
    if src.fmp is not None:
        fmp = src.fmp
        countries = set(s.list(s.ctx_econ_countries))

        async def calendar(fetched: int) -> list[Record]:
            d = _today(fetched)
            return await fmp.economic(d - timedelta(days=7), d + timedelta(days=30), countries)

        async def earnings(fetched: int) -> list[Record]:
            d = _today(fetched)
            return await fmp.earnings(d - timedelta(days=7), d + timedelta(days=60), set(universe(fetched)))

        async def fundamentals(fetched: int) -> list[Record]:
            return await per_symbol("fundamentals", universe(fetched), fmp.fundamentals)

        jobs += [
            Job("calendar", "fmp", s.ctx_calendar_every, calendar),
            Job("earnings", "fmp", s.ctx_earnings_every, earnings),
            Job("fundamentals", "fmp", s.ctx_fundamentals_every, fundamentals),
        ]
    if src.alfred is not None:
        alfred = src.alfred

        async def macro(fetched: int) -> list[Record]:
            out: list[Record] = []
            for sid in s.list(s.ctx_fred_series):
                out += await alfred.series(sid, _today(fetched) - timedelta(days=400))
            return out

        jobs.append(Job("macro", "alfred", s.ctx_macro_every, macro))
    if src.finra is not None:
        finra = src.finra

        async def short_interest(fetched: int) -> list[Record]:
            d, syms, out = _today(fetched), universe(fetched), []
            if not syms:
                return []
            for sd in settlement_dates(d - timedelta(days=45), d):
                out += await finra.short_interest(sd, syms, fetched)
            return out

        jobs.append(Job("short_interest", "finra", s.ctx_finra_every, short_interest))
    if src.edgar is not None:
        edgar, forms = src.edgar, set(s.list(s.ctx_edgar_forms))

        async def filings(fetched: int) -> list[Record]:
            return await per_symbol("filings", universe(fetched), lambda sym: edgar.filings(sym, forms))

        jobs.append(Job("filings", "edgar", s.ctx_edgar_every, filings))
    if src.news is not None:
        news = src.news
        state = {"since": 0}

        async def news_job(fetched: int) -> list[Record]:
            start = state["since"] or fetched - 3600 * NS
            arts = await news.fetch(start - 120 * NS, fetched, universe(fetched), s.ctx_news_store_body)
            state["since"] = fetched
            return pipeline.records(arts, fetched)

        jobs.append(Job("news", "alpaca.news", s.ctx_news_poll_seconds, news_job))

    async def regime(fetched: int) -> list[Record]:
        return regime_records(lake, store, resolver, fetched)

    jobs.append(Job("regime", "context-data", s.ctx_regime_every, regime))
    return jobs


def build_engine(
    s: Settings, publisher: Publisher, registry: Any, src: Sources, lake: Lake
) -> tuple[Engine, NewsPipeline]:
    store = ContextStore(lake, s.availability())
    resolver = Resolver(lake)
    pipeline = NewsPipeline(
        resolver,
        QuarantinedScorer(make_backend(s)),
        s.availability(),
        s.ctx_news_live_window_seconds * NS,
        s.ctx_news_store_body,
    )
    try:
        recent = store.versions("news", None).filter(pl.col("event_time") > datetime.now(tz=UTC) - timedelta(days=1))
        pipeline.warm(
            [
                (r["news_id"], r["instrument_id"], int(r["event_time"].timestamp()) * NS, r["headline"] or "")
                for r in recent.iter_rows(named=True)
            ]
        )
    except Exception as exc:
        log.warning("news warm-up skipped", extra={"err": str(exc)[:200]})
    eng = Engine(
        publisher,
        Versioner(store),
        Metrics(registry),
        build_jobs(s, src, lake, store, resolver, pipeline),
        s.ctx_stale_factor,
    )
    return eng, pipeline


async def backfill(
    s: Settings, src: Sources, engine: Engine, pipeline: NewsPipeline, lake: Lake, source: str, start: date, end: date
) -> int:
    """Historical load for one source over ``[start, end]`` (first-seen versions get rule times)."""
    resolver = pipeline.resolver
    now = int(datetime.now(tz=UTC).timestamp()) * NS
    syms = resolver.universe_symbols(s.ctx_universe, now, s.list(s.ctx_symbols))
    total = 0

    async def emit(records: list[Record]) -> None:
        nonlocal total
        outs = engine.versioner.stamp(records, int(datetime.now(tz=UTC).timestamp()) * NS)
        total += await engine.publisher.publish(outs)

    d = start
    while d <= end:
        chunk_end = min(end, d + timedelta(days=29))
        if source == "calendar" and src.fmp:
            await emit(await src.fmp.economic(d, chunk_end, set(s.list(s.ctx_econ_countries))))
        elif source == "earnings" and src.fmp:
            await emit(await src.fmp.earnings(d, chunk_end, set(syms)))
        elif source == "short_interest" and src.finra:
            for sd in settlement_dates(d, chunk_end):
                await emit(await src.finra.short_interest(sd, syms, now))
        elif source == "news" and src.news:
            day = d
            while day <= chunk_end:
                a = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * NS
                arts = await src.news.fetch(a, a + 86_400 * NS, syms, s.ctx_news_store_body)
                await emit(pipeline.records(arts, now))
                day += timedelta(days=1)
        d = chunk_end + timedelta(days=1)
    if source == "macro" and src.alfred:
        for sid in s.list(s.ctx_fred_series):
            await emit(await src.alfred.series(sid, start))
    elif source == "fundamentals" and src.fmp:
        for sym in syms:
            await emit(await src.fmp.fundamentals(sym, limit=40))
    elif source == "filings" and src.edgar:
        for sym in syms:
            await emit(await src.edgar.filings(sym, set(s.list(s.ctx_edgar_forms))))
    elif source == "regime":
        await emit(
            regime_records(
                lake, ContextStore(lake, s.availability()), resolver, now, lookback_days=(end - start).days + 400
            )
        )
    return total
