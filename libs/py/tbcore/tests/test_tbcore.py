"""Unit tests for the tbcore service template, redaction (SE-012), and secrets (SE-010)."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tbcore.logging import JsonFormatter, correlation_id
from tbcore.redact import REDACTED, redact, register_secret, unregister_secret
from tbcore.secrets import SecretNotFoundError, get_secret
from tbcore.service import ServiceSettings, create_ops_app


@pytest.mark.parametrize(
    "secret",
    [
        "PKABCDEFGHIJKLMNOPQRST",
        "Bearer abcdefghijklmnop.qrstuvwx",
        "AKIAABCDEFGHIJKLMNOP",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    ],
)
def test_se012_patterns_redacted_in_logs(secret: str) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("test"))
    log = logging.getLogger(f"t-{secret[:4]}")
    log.handlers[:] = [handler]
    log.propagate = False
    token = correlation_id.set("corr-1")
    try:
        log.error("failed with %s", secret, extra={"detail": secret, "api_secret": "x", "nested": {"k": secret}})
        try:
            raise RuntimeError(f"upstream {secret}")
        except RuntimeError:
            log.exception("boom")
    finally:
        correlation_id.reset(token)
    out = stream.getvalue()
    assert secret not in out
    first = json.loads(out.splitlines()[0])
    assert first["correlation_id"] == "corr-1"
    assert first["service"] == "test"
    assert first["api_secret"] == REDACTED


def test_se012_registered_secret_and_kv() -> None:
    register_secret("registered-secret-value-42")
    try:
        assert "registered-secret-value-42" not in redact("x registered-secret-value-42 y")
    finally:
        unregister_secret("registered-secret-value-42")
    assert "hunter2hunter2" not in redact("password=hunter2hunter2")
    plain = "order tb1-abc123-01JABCDEFGHJKMNPQRSTVWXYZ0-1 accepted"
    assert redact(plain) == plain


def test_se010_get_secret(tmp_path: Path) -> None:
    (tmp_path / "db_password").write_bytes(b"correct-horse-battery\n")
    assert get_secret("db_password", tmp_path) == b"correct-horse-battery"
    assert "correct-horse-battery" not in redact("pw correct-horse-battery")
    with pytest.raises(ValueError, match="invalid"):
        get_secret("../etc/passwd", tmp_path)
    with pytest.raises(SecretNotFoundError):
        get_secret("missing", tmp_path)


def test_op010_ops_endpoints() -> None:
    async def failing() -> None:
        raise RuntimeError("db down password=hunter2hunter2")

    settings = ServiceSettings(service="unit")
    client = TestClient(create_ops_app(settings, {"db": failing}))
    assert client.get("/healthz").json() == {"status": "ok", "service": "unit"}
    ready = client.get("/readyz")
    assert ready.status_code == 503
    assert "hunter2" not in ready.text
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "tb_build_info" in metrics.text
