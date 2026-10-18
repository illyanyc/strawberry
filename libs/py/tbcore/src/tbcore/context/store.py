"""Point-in-time reads of the lake's ``context`` dataset (section 09; rules CX-002, DF-002, DF-010).

A context record (``record_key``) may have many versions (snapshots, vintages, revisions), each
with its own ``available_at``. Every read here takes ``as_of`` and keeps, per (record_key,
instrument_id), the version with the latest ``available_at <= as_of``; nothing published later is
ever returned. Payloads are decoded with the explicit schemas in :mod:`tbcore.context.schemas`.

The query helpers are the section 09 interface: ``news_for``, ``sentiment_agg``, ``macro``,
``upcoming_events``, ``earnings_window``, ``regime``, plus ``short_interest``, ``filings``, and
``fundamentals``.
"""

from __future__ import annotations

import glob as _glob
import os
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import polars as pl

from tbcore.context.availability import (
    DEFAULT,
    AvailabilityConfig,
    earnings_actual_available_at,
    parse_timing,
)
from tbcore.context.events import ScheduledEvent
from tbcore.context.schemas import PAYLOADS
from tbcore.context.sentiment import SentimentAgg, aggregate
from tbcore.data.lake import Lake, TimeLike, to_ns

NS = 1_000_000_000
DAY_NS = 86_400 * NS
_DT = pl.Datetime("ns", "UTC")


def _ts(ns: int) -> pl.Expr:
    return pl.lit(ns, dtype=pl.Int64).cast(_DT)


def _dec(s: Any) -> Decimal | None:
    if s is None or s == "":
        return None
    try:
        return Decimal(str(s))
    except InvalidOperation:
        return None


@dataclass(frozen=True, slots=True)
class EarningsWindow:
    """Earnings context of one instrument as known at ``as_of`` (CX-007)."""

    next_report_date: date | None
    next_timing: str | None
    days_to_next: float | None
    last_report_date: date | None
    days_since_last: float | None
    last_eps_surprise: Decimal | None
    last_eps_surprise_pct: float | None


class ContextStore:
    """Read-only, point-in-time view of the context dataset."""

    def __init__(self, lake: Lake | str | os.PathLike[str], cfg: AvailabilityConfig = DEFAULT) -> None:
        self.lake = lake if isinstance(lake, Lake) else Lake(lake)
        self.cfg = cfg

    # ------------------------------------------------------------------ raw versions

    def files(self, kind: str) -> list[str]:
        pattern = Path(self.lake.root) / "context" / f"kind={kind}" / "year=*" / "month=*" / "*.parquet"
        return sorted(p for p in _glob.glob(str(pattern)) if not os.path.basename(p).startswith("."))

    def versions(
        self,
        kind: str,
        as_of: TimeLike | None = None,
        instruments: list[str] | None = None,
        source: str | None = None,
    ) -> pl.DataFrame:
        """Every stored version known at ``as_of`` (all versions when None), payload decoded."""
        paths = self.files(kind)
        if not paths:
            return self._empty(kind)
        lf = pl.scan_parquet(paths, hive_partitioning=False, missing_columns="insert", extra_columns="ignore")
        lf = lf.filter(pl.col("kind") == kind)
        if as_of is not None:
            lf = lf.filter(pl.col("available_at") <= _ts(to_ns(as_of)))  # DF-002
        if instruments is not None:
            lf = lf.filter(pl.col("instrument_id").is_in(list(instruments)))
        if source is not None:
            lf = lf.filter(pl.col("source") == source)
        df = lf.collect()
        return self._decode(kind, df)

    def latest(
        self,
        kind: str,
        as_of: TimeLike,
        instruments: list[str] | None = None,
        source: str | None = None,
    ) -> pl.DataFrame:
        """Per (record_key, instrument_id), the version with the latest ``available_at <= as_of``
        (CX-002); ties broken by the later ``ingested_at``."""
        df = self.versions(kind, as_of, instruments, source)
        if df.is_empty():
            return df
        return (
            df.sort(["available_at", "ingested_at"], descending=[True, True])
            .unique(subset=["record_key", "instrument_id", "source"], keep="first", maintain_order=True)
            .sort(["instrument_id", "event_time", "record_key"])
        )

    def latest_shas(self, kind: str, source: str | None = None) -> dict[tuple[str, str], str]:
        """``{(record_key, instrument_id): payload_sha256}`` of every record's newest version (all
        time). context-data uses it to stamp first-seen vs changed versions (CX-004)."""
        paths = self.files(kind)
        if not paths:
            return {}
        lf = pl.scan_parquet(paths, hive_partitioning=False, missing_columns="insert", extra_columns="ignore")
        if source is not None:
            lf = lf.filter(pl.col("source") == source)
        if "record_key" not in lf.collect_schema().names():
            lf = lf.with_columns(pl.lit("", dtype=pl.Utf8).alias("record_key"))
        df = (
            lf.select("record_key", "instrument_id", "payload_sha256", "available_at", "ingested_at")
            .collect()
            .sort(["available_at", "ingested_at"], descending=[True, True])
            .unique(subset=["record_key", "instrument_id"], keep="first")
        )
        return {
            (r[0] or "", r[1] or ""): r[2]
            for r in df.select("record_key", "instrument_id", "payload_sha256").iter_rows()
        }

    @staticmethod
    def _empty(kind: str) -> pl.DataFrame:
        base: dict[str, pl.DataType] = {
            "kind": pl.Utf8(),
            "instrument_id": pl.Utf8(),
            "symbol": pl.Utf8(),
            "event_time": _DT,
            "available_at": _DT,
            "ingested_at": _DT,
            "source": pl.Utf8(),
            "vintage": pl.Utf8(),
            "record_key": pl.Utf8(),
        }
        return pl.DataFrame(schema={**base, **PAYLOADS.get(kind, {})})

    @staticmethod
    def _decode(kind: str, df: pl.DataFrame) -> pl.DataFrame:
        if df.is_empty():
            return ContextStore._empty(kind)
        if "record_key" not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("record_key"))
        # Rows written before record_key existed fall back to their event time (CX-002).
        df = df.with_columns(
            pl.coalesce(
                pl.when(pl.col("record_key") != "").then(pl.col("record_key")),
                pl.lit("t") + pl.col("event_time").dt.epoch("ns").cast(pl.Utf8),
            ).alias("record_key"),
            pl.col("instrument_id").fill_null(""),
        )
        cols = [
            "kind",
            "instrument_id",
            "symbol",
            "event_time",
            "available_at",
            "ingested_at",
            "source",
            "vintage",
            "record_key",
        ]
        schema = PAYLOADS.get(kind)
        if not schema:
            return df.select(*cols, "payload_json")
        dtype = pl.Struct(schema)
        return df.select(*cols, pl.col("payload_json").str.json_decode(dtype).alias("_p")).unnest("_p")

    # ------------------------------------------------------------------ news and sentiment

    def news_for(
        self, instrument: str, start: TimeLike, end: TimeLike, as_of: TimeLike, include_duplicates: bool = False
    ) -> pl.DataFrame:
        """Articles about ``instrument`` first published in ``[start, end)`` and known at ``as_of``."""
        df = self.latest("news", as_of, [instrument])
        s, e = to_ns(start), to_ns(end)
        df = df.filter((pl.col("event_time") >= _ts(s)) & (pl.col("event_time") < _ts(e)))
        if not include_duplicates:
            df = df.filter(pl.col("duplicate_of").fill_null("") == "")
        return df.sort("event_time")

    def sentiment(
        self,
        instruments: list[str] | None,
        as_of: TimeLike,
        model: str | None = None,
        include_contaminated: bool = False,
    ) -> pl.DataFrame:
        """Sentiment scores known at ``as_of``; contaminated scores are excluded unless asked (CX-014)."""
        df = self.latest("news_sentiment", as_of, instruments)
        if model is not None:
            df = df.filter(pl.col("model") == model)
        if not include_contaminated:
            df = df.filter(~pl.col("contaminated").fill_null(True))
        return df

    def sentiment_agg(
        self,
        instrument: str,
        window: timedelta,
        as_of: TimeLike,
        half_life: timedelta = timedelta(hours=12),
        model: str | None = None,
        include_contaminated: bool = False,
    ) -> SentimentAgg:
        """Count, mean, decay-weighted mean, and novelty of ``instrument``'s news sentiment."""
        a = to_ns(as_of)
        df = self.sentiment([instrument], a, model, include_contaminated=True)
        return aggregate(
            df, a, int(window.total_seconds() * NS), int(half_life.total_seconds() * NS), include_contaminated
        )

    # ------------------------------------------------------------------ macro

    def macro(
        self, series: str, as_of: TimeLike, start_period: date | None = None, end_period: date | None = None
    ) -> pl.DataFrame:
        """The series as known at ``as_of``: per period, the latest vintage with ``available_at <=
        as_of`` (DF-010). Columns: period (date), value (str, "" when missing), vintage, available_at."""
        df = self.latest("macro", as_of)
        df = df.filter(pl.col("series_id") == series.upper())
        df = df.with_columns(pl.col("period").str.to_date().alias("period")).sort("period")
        if start_period is not None:
            df = df.filter(pl.col("period") >= start_period)
        if end_period is not None:
            df = df.filter(pl.col("period") <= end_period)
        return df.select("period", "value", "vintage", "available_at", "realtime_start")

    def macro_value(self, series: str, period: date, as_of: TimeLike) -> Decimal | None:
        df = self.macro(series, as_of, period, period)
        return None if df.is_empty() else _dec(df["value"][0])

    def macro_latest(self, series: str, as_of: TimeLike) -> tuple[date, Decimal] | None:
        """Most recent period with a value as known at ``as_of``."""
        df = self.macro(series, as_of).filter(pl.col("value").fill_null("") != "")
        if df.is_empty():
            return None
        v = _dec(df["value"][-1])
        return None if v is None else (df["period"][-1], v)

    # ------------------------------------------------------------------ calendar and earnings

    def upcoming_events(
        self,
        as_of: TimeLike,
        horizon: timedelta,
        categories: list[str] | None = None,
        instruments: list[str] | None = None,
        since: timedelta = timedelta(0),
    ) -> list[ScheduledEvent]:
        """Macro releases (and earnings of ``instruments``) scheduled in ``[as_of - since, as_of +
        horizon]`` as known at ``as_of``."""
        return self.events(
            to_ns(as_of),
            to_ns(as_of) - int(since.total_seconds() * NS),
            to_ns(as_of) + int(horizon.total_seconds() * NS),
            categories,
            instruments,
        )

    def events(
        self,
        as_of: int,
        start: int,
        end: int,
        categories: list[str] | None = None,
        instruments: list[str] | None = None,
    ) -> list[ScheduledEvent]:
        """:class:`tbcore.context.events.EventSource` implementation."""
        out: list[ScheduledEvent] = []
        cal = self.latest("calendar", as_of)
        if not cal.is_empty():
            cal = cal.filter((pl.col("event_time") >= _ts(start)) & (pl.col("event_time") <= _ts(end)))
            if categories is not None:
                cal = cal.filter(pl.col("category").is_in(categories))
            for r in cal.iter_rows(named=True):
                out.append(
                    ScheduledEvent(
                        kind="calendar",
                        name=r["event"] or "",
                        at=int(r["event_time"].timestamp() * 1e6) * 1000,
                        available_at=int(r["available_at"].timestamp() * 1e6) * 1000,
                        category=r["category"] or "",
                        impact=r["impact"] or "",
                        country=r["country"] or "",
                        record_key=r["record_key"],
                    )
                )
        if instruments:
            ern = self.latest("earnings", as_of, instruments)
            for r in ern.iter_rows(named=True):
                d = date.fromisoformat(r["report_date"])
                at = earnings_actual_available_at(d, parse_timing(r["timing"]), self.cfg)
                if start <= at <= end:
                    out.append(
                        ScheduledEvent(
                            kind="earnings",
                            name=f"earnings {r['symbol']}",
                            at=at,
                            available_at=int(r["available_at"].timestamp() * 1e6) * 1000,
                            instrument_id=r["instrument_id"],
                            record_key=r["record_key"],
                        )
                    )
        return sorted(out, key=lambda e: e.at)

    def earnings_window(self, instrument: str, as_of: TimeLike) -> EarningsWindow:
        """Next scheduled report and the last report whose result is available at ``as_of``. A
        surprise is returned only once its result version is available (an AMC report's surprise is
        not visible during that day's session, CX-007)."""
        a = to_ns(as_of)
        df = self.latest("earnings", a, [instrument])
        nxt = last = None
        for r in df.sort("report_date").iter_rows(named=True):
            d = date.fromisoformat(r["report_date"])
            when = earnings_actual_available_at(d, parse_timing(r["timing"]), self.cfg)
            if r["phase"] == "reported" and when <= a:
                last = (d, r)
            elif when > a and nxt is None:
                nxt = (d, r)

        def days(ns_at: int) -> float:
            return (ns_at - a) / DAY_NS

        surprise = pct = None
        if last is not None:
            surprise = _dec(last[1]["eps_surprise"])
            est = _dec(last[1]["eps_estimate"])
            if surprise is not None and est not in (None, Decimal(0)):
                assert est is not None
                pct = float(surprise / abs(est))
        return EarningsWindow(
            next_report_date=nxt[0] if nxt else None,
            next_timing=nxt[1]["timing"] if nxt else None,
            days_to_next=days(earnings_actual_available_at(nxt[0], parse_timing(nxt[1]["timing"]), self.cfg))
            if nxt
            else None,
            last_report_date=last[0] if last else None,
            days_since_last=-days(earnings_actual_available_at(last[0], parse_timing(last[1]["timing"]), self.cfg))
            if last
            else None,
            last_eps_surprise=surprise,
            last_eps_surprise_pct=pct,
        )

    # ------------------------------------------------------------------ other kinds

    def short_interest(self, instrument: str, as_of: TimeLike) -> dict[str, Any] | None:
        """Latest published short interest (by settlement date) known at ``as_of`` (CX-005)."""
        df = self.latest("short_interest", as_of, [instrument]).sort("settlement_date")
        return None if df.is_empty() else df.row(-1, named=True)

    def filings(
        self, instrument: str, as_of: TimeLike, forms: list[str] | None = None, since: TimeLike | None = None
    ) -> pl.DataFrame:
        df = self.latest("filing", as_of, [instrument])
        if forms is not None:
            df = df.filter(pl.col("form").is_in(forms))
        if since is not None:
            df = df.filter(pl.col("available_at") >= _ts(to_ns(since)))
        return df.sort("available_at")

    def fundamentals(self, instrument: str, as_of: TimeLike, statement: str = "income") -> pl.DataFrame:
        """Statements as known at ``as_of``: per period, the latest snapshot (restatements included
        only from when they were observed, CX-008)."""
        df = self.latest("fundamentals", as_of, [instrument])
        return df.filter(pl.col("statement") == statement).sort("period_end")

    def regime(self, as_of: TimeLike, version: str | None = None) -> dict[str, Any] | None:
        """The most recent regime row known at ``as_of`` (CX-017)."""
        df = self.latest("regime", as_of)
        if version is not None:
            df = df.filter(pl.col("version") == version)
        if df.is_empty():
            return None
        return df.sort(["event_time", "available_at"]).row(-1, named=True)
