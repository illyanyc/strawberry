"""Untrusted text handling (CX-011, SE-012). News and filing text is data, never instructions: it
is stripped of markup and control characters, Unicode-normalized, and length-limited before it is
stored or shown to the quarantined scoring model. It is never executed and never reaches the
privileged assistant (section 12)."""

from __future__ import annotations

import html
import re
import unicodedata

HEADLINE_MAX = 512
SUMMARY_MAX = 2_000
BODY_MAX = 20_000
_BLOCK = re.compile(r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]{0,2000}>")
_CTRL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_WS = re.compile(r"\s+")


def clean(text: str | None, limit: int) -> str:
    """Plain text: no tags, scripts, ANSI or control/bidi characters, collapsed whitespace, NFKC,
    at most ``limit`` characters."""
    if not text:
        return ""
    t = _BLOCK.sub(" ", text)
    t = _TAG.sub(" ", t)
    t = html.unescape(t)
    t = _ANSI.sub("", t)
    t = unicodedata.normalize("NFKC", t)
    t = _CTRL.sub("", t)
    t = _WS.sub(" ", t).strip()
    return t[:limit]
