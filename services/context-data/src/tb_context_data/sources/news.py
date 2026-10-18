"""Alpaca (Benzinga) news through broker-gateway, which alone holds the Alpaca credential (SE-031),
and the news pipeline: sanitize, resolve tickers as of publication, mark syndicated duplicates,
score through the quarantined model (CX-010..CX-014).

Availability: first publication (``created_at``) plus the receipt delay; an article received live
(within ``live_window`` of publication) is never available before it was received. Scores are
available when scored (live) or at the article's availability plus the scoring latency (backfill).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import grpc

from tb.broker.v1 import market_data_pb2
from tb_context_data.http import SourceUnavailable
from tb_context_data.records import Record, Version
from tb_context_data.resolve import Resolver
from tb_context_data.sanitize import BODY_MAX, HEADLINE_MAX, SUMMARY_MAX, clean
from tb_context_data.sentiment import QuarantinedScorer
from tbcore.context import availability as av
from tbcore.context.news import Article, DuplicateDetector
from tbcore.svcauth import Signer

NS = 1_000_000_000
METHOD = "/tb.broker.v1.BrokerMarketDataService/GetNews"


class NewsSource(Protocol):
    async def fetch(
        self, start_ns: int, end_ns: int, symbols: Sequence[str], include_content: bool
    ) -> list[market_data_pb2.NewsArticle]: ...


class GrpcNewsSource:
    """Pages ``BrokerMarketDataService.GetNews`` with a fresh service token per call."""

    def __init__(self, channel: grpc.aio.Channel, signer: Signer | None, timeout: float = 30.0) -> None:
        self._call = channel.unary_unary(
            METHOD,
            request_serializer=market_data_pb2.GetNewsRequest.SerializeToString,
            response_deserializer=market_data_pb2.GetNewsResponse.FromString,
        )
        self.signer = signer
        self.timeout = timeout

    async def fetch(
        self, start_ns: int, end_ns: int, symbols: Sequence[str], include_content: bool
    ) -> list[market_data_pb2.NewsArticle]:
        out: list[market_data_pb2.NewsArticle] = []
        token = ""
        for _ in range(10_000):
            req = market_data_pb2.GetNewsRequest(
                symbols=list(symbols),
                start=start_ns,
                end=end_ns,
                page_token=token,
                limit=50,
                include_content=include_content,
            )
            md = self.signer.metadata("broker-gateway") if self.signer else None
            try:
                resp = await self._call(req, metadata=md, timeout=self.timeout)
            except grpc.aio.AioRpcError as exc:
                if exc.code() == grpc.StatusCode.UNIMPLEMENTED:
                    raise SourceUnavailable("broker-gateway has no market-data account for news") from exc
                raise
            out.extend(resp.articles)
            token = resp.next_page_token
            if not token:
                break
        return out


@dataclass
class NewsPipeline:
    resolver: Resolver
    scorer: QuarantinedScorer
    cfg: av.AvailabilityConfig
    live_window_ns: int = 300 * NS
    store_body: bool = False
    detector: DuplicateDetector = field(default_factory=DuplicateDetector)
    scored: int = 0

    def warm(self, recent: Sequence[tuple[str, str, int, str]]) -> None:
        """Seed duplicate detection with stored articles (news_id, instrument_id, created_at, headline)."""
        for nid, inst, created, headline in sorted(recent, key=lambda r: r[2]):
            self.detector.check(Article(nid, inst, created, headline))

    def records(self, articles: Sequence[market_data_pb2.NewsArticle], fetched_ns: int) -> list[Record]:
        out: list[Record] = []
        t0 = time.perf_counter()
        for a in sorted(articles, key=lambda x: (x.created_at, x.id)):
            if not a.id or a.created_at <= 0:
                continue
            rule = av.news_available_at(a.created_at, self.cfg)
            live = fetched_ns - a.created_at <= self.live_window_ns
            avail = max(rule, fetched_ns) if live else rule
            syms = sorted({s.upper() for s in a.symbols if s})
            insts, kept = [], []
            for s in syms:
                inst = self.resolver.resolve(s, a.created_at)
                if inst is not None and inst not in insts:
                    insts.append(inst)
                    kept.append(s)
            headline = clean(a.headline, HEADLINE_MAX)
            dup = ""
            for inst in insts or [""]:
                d = self.detector.check(Article(a.id, inst, a.created_at, headline))
                dup = dup or d
            payload: dict[str, Any] = {
                "news_id": a.id,
                "headline": headline,
                "summary": clean(a.summary, SUMMARY_MAX),
                "author": clean(a.author, 200),
                "provider": clean(a.source, 64),
                "url": a.url[:1000],
                "created_at": str(a.created_at),
                "revised": a.updated_at > a.created_at,
                "duplicate_of": dup,
                "untrusted": True,
            }
            if self.store_body:
                payload["body"] = clean(a.content, BODY_MAX)
            ids, symbols = tuple(insts), tuple(kept if insts else syms)
            out.append(Record("news", "alpaca.news", a.id, a.created_at, (Version(payload, avail),), ids, symbols))
            s = self.scorer.score(a.headline, a.summary, a.created_at)
            if s is None:
                continue
            self.scored += 1
            scored_ns = fetched_ns + int((time.perf_counter() - t0) * NS)  # same clock as the fetch
            s_at = av.sentiment_available_at(avail, scored_ns if live else None, self.cfg)
            sp = {
                "news_id": a.id,
                "model": s.model,
                "model_version": s.model_version,
                "scorer": s.scorer,
                "training_cutoff": s.training_cutoff,
                "score": s.score.score,
                "label": s.score.label,
                "confidence": s.score.confidence,
                "contaminated": s.contaminated,
                "duplicate": bool(dup),
                "input_sha256": s.input_sha256,
            }
            out.append(
                Record(
                    "news_sentiment",
                    s.model,
                    f"{a.id}|{s.model}@{s.model_version}",
                    a.created_at,
                    (Version(sp, s_at),),
                    ids,
                    symbols,
                    authoritative=True,
                )
            )
        return out
