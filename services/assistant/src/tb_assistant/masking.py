"""Prompt masking and the egress guard (AN-051, AN-052, D31 rule 10).

LLMs memorize financial history; a model that can recognize "AAPL in March 2020" leaks the future
into research. Every planner, coder, and chat prompt is therefore built as structured data, masked,
serialized, and only then scanned by the egress guard:

* tickers and instrument IDs become per-round aliases (``I001``, ``I002``, ...);
* calendar dates and timestamps become day offsets from a per-round anchor (``D+12``);
* account, balance, position, quantity, raw price, and currency-amount fields are dropped.

The guard scans the final payload text and refuses the call if anything identifying survived.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

# Keys whose values never leave the box (exact match on the lower-cased key, AN-052).
DROP_KEYS = frozenset(
    {
        "account",
        "account_id",
        "accounts",
        "broker_account_id",
        "account_number",
        "balance",
        "balances",
        "equity",
        "cash",
        "buying_power",
        "position",
        "positions",
        "qty",
        "quantity",
        "filled_qty",
        "avg_cost",
        "avg_price",
        "price",
        "prices",
        "limit_price",
        "stop_price",
        "open",
        "high",
        "low",
        "close",
        "vwap",
        "notional",
        "allocation",
        "pnl",
        "realized_pnl",
        "unrealized_pnl",
    }
)
DROP_SUFFIXES = ("_usd", "_amount", "_dollars", "_notional")
SYMBOL_KEYS = frozenset({"symbol", "ticker", "symbols", "tickers"})
INSTRUMENT_KEYS = frozenset({"instrument_id", "instrument_ids"})
TIME_KEY_RE = re.compile(r"(^|_)(at|time|ts|date|as_of|start|end|timestamp|day)(_|$)|^ts_|_ns$")

_ISO_DT_RE = re.compile(
    r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"
)
_COMPACT_DATE_RE = re.compile(r"\b((?:19|20)\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])\b")
_EPOCH_NS_RE = re.compile(r"\b1[0-9]{18}\b")
_EPOCH_MS_RE = re.compile(r"\b1[0-9]{12}\b")
_CURRENCY_RE = re.compile(r"(?:[$€£]\s?\d)|(?:\b\d[\d,]*(?:\.\d+)?\s?(?:USD|dollars)\b)", re.IGNORECASE)
# IBKR (U1234567, DU1234567, F...), Alpaca (PA..., 9-12 alnum after PA) account identifiers.
_ACCOUNT_RE = re.compile(r"\b(?:D?U\d{6,9}|PA[0-9A-Z]{8,12}|[FI]\d{7,8})\b")
_MONTH_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(?:\d{1,2},?\s+)?(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:19[89]\d|20[0-4]\d)\b")
_ALIAS_RE = re.compile(r"\bI\d{3,4}\b")

NS_PER_DAY = 86_400 * 10**9


class EgressBlockedError(RuntimeError):
    """The egress guard found identifying content in an outbound payload (AN-052)."""

    def __init__(self, findings: list[str]) -> None:
        super().__init__("egress guard blocked the LLM call: " + ", ".join(findings[:5]))
        self.findings = findings


def _anchor_day(anchor: datetime) -> date:
    return anchor.astimezone(UTC).date()


@dataclass
class Masker:
    """Per-round masking context. Aliases are stable within a round and unknown to the LLM's past."""

    anchor: datetime
    symbols: set[str] = field(default_factory=set)
    instrument_ids: set[str] = field(default_factory=set)
    _forward: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)

    def learn(self, symbols: Iterable[str] = (), instrument_ids: Iterable[str] = ()) -> None:
        self.symbols.update(s for s in symbols if s)
        self.instrument_ids.update(i for i in instrument_ids if i)

    def alias(self, token: str) -> str:
        if token not in self._forward:
            a = f"I{len(self._forward) + 1:03d}"
            self._forward[token] = a
            self._reverse[a] = token
        return self._forward[token]

    @property
    def aliases(self) -> dict[str, str]:
        """alias → real identifier (kept in the orchestrator only)."""
        return dict(self._reverse)

    # ---- dates

    def rel_day(self, d: date) -> str:
        n = (d - _anchor_day(self.anchor)).days
        return f"D{n:+d}"

    def _rel_from_ns(self, ns: int) -> str:
        return self.rel_day(datetime.fromtimestamp(ns / 1e9, tz=UTC).date())

    def _mask_dates(self, text: str) -> str:
        def iso(m: re.Match[str]) -> str:
            try:
                return self.rel_day(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                return "D?"

        def compact(m: re.Match[str]) -> str:
            try:
                return self.rel_day(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                return m.group(0)

        text = _ISO_DT_RE.sub(iso, text)
        text = _COMPACT_DATE_RE.sub(compact, text)
        text = _EPOCH_NS_RE.sub(lambda m: self._rel_from_ns(int(m.group(0))), text)
        text = _EPOCH_MS_RE.sub(lambda m: self._rel_from_ns(int(m.group(0)) * 10**6), text)
        text = _MONTH_RE.sub("D?", text)
        return text

    # ---- text

    def mask_text(self, text: str) -> str:
        text = self._mask_dates(text)
        text = _ACCOUNT_RE.sub("[account]", text)
        text = _CURRENCY_RE.sub("[amount]", text)
        for ident in sorted(self.instrument_ids, key=len, reverse=True):
            if ident in text:
                text = text.replace(ident, self.alias(ident))
        if self.symbols:
            pattern = re.compile(
                r"(?<![A-Za-z0-9_.])("
                + "|".join(re.escape(s) for s in sorted(self.symbols, key=len, reverse=True))
                + r")(?![A-Za-z0-9_])"
            )
            text = pattern.sub(lambda m: self.alias(m.group(1)), text)
        return text

    # ---- structures

    def mask(self, obj: Any, key: str = "") -> Any:
        """Mask a JSON-like structure; returns a new structure."""
        k = key.lower()
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for raw_k, v in obj.items():
                sk = str(raw_k)
                lk = sk.lower()
                if lk in DROP_KEYS or lk.endswith(DROP_SUFFIXES):
                    continue
                out[self.mask_text(sk)] = self.mask(v, sk)
            return out
        if isinstance(obj, list | tuple):
            return [self.mask(v, key) for v in obj]
        if isinstance(obj, bool) or obj is None:
            return obj
        if isinstance(obj, int):
            if TIME_KEY_RE.search(k) and abs(obj) > 10**9:
                if obj > 10**17:
                    return self._rel_from_ns(obj)
                if obj > 10**11:
                    return self._rel_from_ns(obj * 10**6)
                return self._rel_from_ns(obj * 10**9)
            if 10**17 < obj < 4 * 10**18:
                return self._rel_from_ns(obj)
            return obj
        if isinstance(obj, float):
            return obj
        if isinstance(obj, str):
            if k in SYMBOL_KEYS or k in INSTRUMENT_KEYS:
                if k in SYMBOL_KEYS:
                    self.symbols.add(obj)
                else:
                    self.instrument_ids.add(obj)
                return self.alias(obj)
            return self.mask_text(obj)
        if isinstance(obj, datetime):
            return self.rel_day(obj.astimezone(UTC).date())
        if isinstance(obj, date):
            return self.rel_day(obj)
        return self.mask_text(str(obj))

    def unmask(self, obj: Any) -> Any:
        """Replace aliases in LLM output with the real identifiers (orchestrator side only)."""
        if isinstance(obj, dict):
            return {self.unmask(str(k)): self.unmask(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.unmask(v) for v in obj]
        if isinstance(obj, str):
            return _ALIAS_RE.sub(lambda m: self._reverse.get(m.group(0), m.group(0)), obj)
        return obj


@dataclass
class EgressGuard:
    """Last check on the exact bytes that would leave the box (AN-052)."""

    symbols: set[str] = field(default_factory=set)
    instrument_ids: set[str] = field(default_factory=set)
    account_ids: set[str] = field(default_factory=set)
    # Off by default: bare four-digit numbers (iteration counts, window lengths) are common in
    # configs; dates are caught by the ISO, compact, month-name, and epoch patterns.
    block_years: bool = False

    def scan(self, payload: str) -> list[str]:
        findings: list[str] = []
        if _ISO_DT_RE.search(payload) or _COMPACT_DATE_RE.search(payload) or _MONTH_RE.search(payload):
            findings.append("calendar_date")
        if _EPOCH_NS_RE.search(payload) or _EPOCH_MS_RE.search(payload):
            findings.append("epoch_timestamp")
        if self.block_years and _YEAR_RE.search(payload):
            findings.append("calendar_year")
        if _CURRENCY_RE.search(payload):
            findings.append("currency_amount")
        if _ACCOUNT_RE.search(payload) or any(a and a in payload for a in self.account_ids):
            findings.append("account_identifier")
        if any(i and i in payload for i in self.instrument_ids):
            findings.append("instrument_id")
        # Tickers: whole-word, case-sensitive, at least two characters (one-letter tickers are
        # masked by key but too ambiguous to scan for in prose).
        syms = [s for s in self.symbols if len(s) >= 2]
        if syms:
            pat = re.compile(r"(?<![A-Za-z0-9_.])(" + "|".join(re.escape(s) for s in syms) + r")(?![A-Za-z0-9_])")
            if pat.search(payload):
                findings.append("ticker")
        return findings

    def check(self, payload: str) -> None:
        findings = self.scan(payload)
        if findings:
            raise EgressBlockedError(findings)


def dumps(obj: Any) -> str:
    """Deterministic JSON for prompts (sorted keys, compact) so prompt hashes are reproducible."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
