"""Structured JSON logging with redaction and correlation IDs (rules OP-010, SE-012)."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from tbcore.redact import REDACTED, is_sensitive_key, redact

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formats records as one JSON object per line, redacting every string field."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "msg": redact(record.getMessage()),
            "service": self._service,
        }
        if cid := correlation_id.get():
            out["correlation_id"] = cid
        for key, value in vars(record).items():
            if key in _RESERVED or key.startswith("_"):
                continue
            out[key] = REDACTED if is_sensitive_key(key) else _clean(value)
        if record.exc_info:
            out["error"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: REDACTED if is_sensitive_key(str(k)) else _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact(str(value))


def configure(service: str, level: str = "INFO") -> logging.Logger:
    """Install the JSON formatter on the root logger and return the service logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    return logging.getLogger(service)
