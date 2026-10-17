"""Every Python placeholder service imports and serves the standard ops surface (OP-010)."""

import importlib

import pytest
from fastapi.testclient import TestClient

from tbcore.service import ServiceSettings, create_ops_app

SERVICES = ["context-data", "monitor", "assistant", "ml", "backtest", "optimizer"]


@pytest.mark.parametrize("service", SERVICES)
def test_op010_placeholder_service(service: str) -> None:
    module = importlib.import_module(f"tb_{service.replace('-', '_')}.__main__")
    assert callable(module.main)
    client = TestClient(create_ops_app(ServiceSettings(service=service)))
    assert client.get("/healthz").json()["service"] == service
    assert client.get("/readyz").status_code == 200
    assert "tb_build_info" in client.get("/metrics").text
