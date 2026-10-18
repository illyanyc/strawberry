"""Source adapters. Each returns :class:`tb_context_data.records.Record` histories with rule
availability per version; stamping and publishing happen in the jobs."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

NS = 1_000_000_000


def dstr(v: Any) -> str:
    """Exact decimal string of a vendor number ("" for null/blank)."""
    if v is None or v == "":
        return ""
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return ""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def sub(a: str, b: str) -> str:
    if not a or not b:
        return ""
    return dstr(Decimal(a) - Decimal(b))


def utc_midnight_ns(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()) * NS
