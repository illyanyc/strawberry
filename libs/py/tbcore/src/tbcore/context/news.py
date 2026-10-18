"""News text handling shared by ingestion and features: headline normalization and syndicated
duplicate detection (CX-013).

Syndicated copies of the same story (same wire item re-published, re-titled, or re-punctuated) are
marked ``duplicate_of`` the first article seen for an instrument within ``window``; aggregates count
a story once. Detection is deterministic: exact match on the normalized headline, or token Jaccard
similarity >= ``threshold``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9$%.]+")
_STOP = frozenset({"a", "an", "the", "of", "to", "in", "on", "for", "and", "as", "at", "by", "with", "is"})
DEFAULT_WINDOW_NS = 24 * 3600 * 1_000_000_000
DEFAULT_THRESHOLD = 0.8


def normalize_headline(text: str) -> str:
    """Lowercase ASCII-folded words without punctuation (keeps $, %, and decimals)."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(w.strip(".") for w in _WORD.findall(t) if w.strip("."))


def tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in normalize_headline(text).split() if w not in _STOP)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True, slots=True)
class Article:
    news_id: str
    instrument_id: str
    created_at: int
    headline: str


class DuplicateDetector:
    """Streaming detector: feed articles in first-publication order; returns ``duplicate_of``."""

    def __init__(self, window_ns: int = DEFAULT_WINDOW_NS, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.window_ns = window_ns
        self.threshold = threshold
        self._recent: dict[str, deque[tuple[int, str, str, frozenset[str]]]] = {}

    def check(self, a: Article) -> str:
        """The news ID this article duplicates for its instrument, or "" if it is original."""
        norm = normalize_headline(a.headline)
        toks = tokens(a.headline)
        q = self._recent.setdefault(a.instrument_id, deque())
        while q and a.created_at - q[0][0] > self.window_ns:
            q.popleft()
        for _, nid, n, t in q:
            if nid == a.news_id:
                return ""
            if n == norm or jaccard(t, toks) >= self.threshold:
                return nid
        q.append((a.created_at, a.news_id, norm, toks))
        return ""


def find_duplicates(
    articles: Iterable[Article], window_ns: int = DEFAULT_WINDOW_NS, threshold: float = DEFAULT_THRESHOLD
) -> dict[tuple[str, str], str]:
    """``{(news_id, instrument_id): duplicate_of}`` for every duplicate in a batch."""
    det = DuplicateDetector(window_ns, threshold)
    out: dict[tuple[str, str], str] = {}
    for a in sorted(articles, key=lambda x: (x.created_at, x.news_id)):
        d = det.check(a)
        if d:
            out[(a.news_id, a.instrument_id)] = d
    return out
