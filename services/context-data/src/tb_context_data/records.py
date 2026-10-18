"""Context records, snapshot stamping, and conversion to ``tb.context.v1.ContextEvent``.

A source returns :class:`Record` histories: all versions of one logical record it can reconstruct
from one fetch, each with its *rule* availability (vendor/official time plus delay). The
:class:`Versioner` turns them into versions to publish (CX-004):

* record never seen: every version with its rule time;
* last version unchanged: nothing;
* changed, ``authoritative`` history (official vintages, immutable filings): the versions after the
  last one seen, with their rule times;
* changed, vendor-restated data (calendars, statements): only the newest version, stamped at the
  fetch time and never before its rule time.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from google.protobuf import struct_pb2

from tb.common.v1 import common_pb2
from tb.context.v1 import context_pb2
from tbcore.context.canonical import payload_sha256
from tbcore.context.store import ContextStore

KINDS = {
    "news": context_pb2.CONTEXT_KIND_NEWS,
    "macro": context_pb2.CONTEXT_KIND_MACRO,
    "earnings": context_pb2.CONTEXT_KIND_EARNINGS,
    "calendar": context_pb2.CONTEXT_KIND_CALENDAR,
    "fundamentals": context_pb2.CONTEXT_KIND_FUNDAMENTALS,
    "regime": context_pb2.CONTEXT_KIND_REGIME,
    "short_interest": context_pb2.CONTEXT_KIND_SHORT_INTEREST,
    "filing": context_pb2.CONTEXT_KIND_FILING,
    "news_sentiment": context_pb2.CONTEXT_KIND_NEWS_SENTIMENT,
}


@dataclass(frozen=True)
class Version:
    """One version of a record: payload and its rule availability (UTC ns)."""

    payload: dict[str, Any]
    available_at: int
    vintage: str = ""

    @property
    def sha(self) -> str:
        return payload_sha256(self.payload)


@dataclass(frozen=True)
class Record:
    kind: str
    source: str
    record_key: str
    event_time: int
    versions: tuple[Version, ...]
    instrument_ids: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    authoritative: bool = False


@dataclass(frozen=True)
class Out:
    """A version ready to publish."""

    kind: str
    source: str
    record_key: str
    event_time: int
    available_at: int
    payload: dict[str, Any]
    instrument_ids: tuple[str, ...]
    symbols: tuple[str, ...]
    vintage: str = ""

    @property
    def msg_id(self) -> str:
        """Nats-Msg-Id: the same version always gets the same ID (stream dedupe, CX-003)."""
        raw = "|".join(
            [
                self.kind,
                self.source,
                self.record_key,
                ",".join(self.instrument_ids),
                str(self.available_at),
                payload_sha256(self.payload),
            ]
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def event(self, ingested_at: int) -> context_pb2.ContextEvent:
        st = struct_pb2.Struct()
        st.update(self.payload)
        return context_pb2.ContextEvent(
            event_id=self.msg_id,
            kind=KINDS[self.kind],
            instrument_ids=list(self.instrument_ids),
            symbols=list(self.symbols),
            provenance=common_pb2.Provenance(
                event_time=self.event_time, available_at=self.available_at, ingested_at=ingested_at
            ),
            payload=st,
            source=self.source,
            vintage=self.vintage,
            record_key=self.record_key,
        )


@dataclass
class Versioner:
    """Tracks each record's newest stored payload hash and stamps new versions (CX-004)."""

    store: ContextStore | None = None
    _seen: dict[tuple[str, str], dict[tuple[str, str], str]] = field(default_factory=dict)

    def _kind(self, kind: str, source: str) -> dict[tuple[str, str], str]:
        k = (kind, source)
        if k not in self._seen:
            self._seen[k] = self.store.latest_shas(kind, source) if self.store is not None else {}
        return self._seen[k]

    def stamp(self, records: Iterable[Record], fetched_ns: int) -> list[Out]:
        out: list[Out] = []
        for r in records:
            if not r.versions:
                continue
            seen = self._kind(r.kind, r.source)
            ikey = r.instrument_ids[0] if r.instrument_ids else ""
            prev = seen.get((r.record_key, ikey))
            versions = sorted(r.versions, key=lambda v: v.available_at)
            last = versions[-1]
            if prev is None:
                emit = versions
            elif prev == last.sha:
                continue
            elif r.authoritative:
                shas = [v.sha for v in versions]
                emit = versions[shas.index(prev) + 1 :] if prev in shas else versions
            else:
                emit = [replace(last, available_at=max(last.available_at, fetched_ns))]
            for v in emit:
                out.append(
                    Out(
                        r.kind,
                        r.source,
                        r.record_key,
                        r.event_time,
                        v.available_at,
                        v.payload,
                        r.instrument_ids,
                        r.symbols,
                        v.vintage,
                    )
                )
            seen[(r.record_key, ikey)] = last.sha
        return out
