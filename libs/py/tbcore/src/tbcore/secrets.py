"""Deploy-time secrets read from mounted files, never environment variables (rule SE-010)."""

from __future__ import annotations

import re
from pathlib import Path

from tbcore.redact import register_secret

_VALID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


class SecretNotFoundError(LookupError):
    """Raised when a named secret file does not exist."""


def get_secret(name: str, secrets_dir: str | Path = "/run/secrets") -> bytes:
    """Return the secret ``name`` (one trailing newline stripped) and register it for redaction."""
    if not _VALID.match(name):
        raise ValueError(f"invalid secret name {name!r}")
    path = Path(secrets_dir) / name
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise SecretNotFoundError(name) from exc
    data = data.removesuffix(b"\n").removesuffix(b"\r")
    register_secret(data.decode("utf-8", errors="ignore"))
    return data
