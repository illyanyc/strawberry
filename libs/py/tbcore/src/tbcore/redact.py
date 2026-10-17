"""Secret redaction shared by logging and error responses (rule SE-012).

Mirrors ``libs/go/logging/redact.go``: sensitive keys are always redacted, registered secret
values are replaced exactly, and well-known secret shapes are replaced by pattern.
"""

from __future__ import annotations

import re
import threading

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "session_id",
    "recovery_code",
    "totp",
    "setup_code",
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:PK|AK|CK)[A-Z0-9]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{50,}"),
)
_KV_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret|token|password|passwd)([\"']?\s*[=:]\s*[\"']?)[^\s\"',;&]{4,}"
)
_MIN_REGISTERED_LEN = 6

_lock = threading.Lock()
_registered: set[str] = set()


def register_secret(value: str) -> None:
    """Add an exact value that must never appear in output."""
    if len(value) < _MIN_REGISTERED_LEN:
        return
    with _lock:
        _registered.add(value)


def unregister_secret(value: str) -> None:
    """Remove a value added with :func:`register_secret`."""
    with _lock:
        _registered.discard(value)


def is_sensitive_key(key: str) -> bool:
    """Return True if a field named ``key`` always carries secret material."""
    k = key.lower()
    return any(s in k for s in _SENSITIVE_KEYS)


def redact(text: str) -> str:
    """Remove registered secrets and known secret patterns from ``text``."""
    if not text:
        return text
    with _lock:
        values = tuple(_registered)
    for v in values:
        if v in text:
            text = text.replace(v, REDACTED)
    for pat in _PATTERNS:
        text = pat.sub(REDACTED, text)
    return _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
