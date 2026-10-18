"""Compose boundaries for the AI analyst (AN-032, AN-053, AN-071; BT-081): network isolation, the
egress proxy, and the sandbox and checker containers. Closes the Phase 3 follow-up that the analyst
sandbox must not mount evaluator paths."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "infra" / "compose" / "docker-compose.yml").read_text())
SERVICES: dict[str, Any] = COMPOSE["services"]
MANIFEST = json.loads((ROOT / "services" / "backtest" / "evaluator_manifest.json").read_text())
ORDER_PATH = {
    "broker-gateway",
    "broker-gateway-ibkr-paper",
    "broker-gateway-ibkr-live",
    "execution",
    "ledger",
    "marketdata",
    "strategy-runtime",
    "ib-gateway-paper",
    "ib-gateway-live",
}


def _nets(name: str) -> set[str]:
    n = SERVICES[name].get("networks", [])
    return set(n if isinstance(n, list) else n.keys())


def test_an032_assistant_only_on_the_research_network() -> None:
    assert _nets("assistant") == {"research"}
    assert COMPOSE["networks"]["research"]["internal"] is True
    for name in ORDER_PATH & set(SERVICES):
        assert "research" not in _nets(name), f"{name} must not share a network with the analyst"
    assert {"postgres", "nats", "ml", "optimizer", "api-gateway"} <= {s for s in SERVICES if "research" in _nets(s)}


def test_an053_only_llm_egress_leaves_the_box() -> None:
    eg = SERVICES["llm-egress"]
    assert _nets("llm-egress") == {"research", "edge"}
    assert eg["command"] == ["egress-proxy"]
    assert "secrets" not in eg
    env = SERVICES["assistant"]["environment"]
    assert env["TB_LLM_PROXY"] == "http://llm-egress:3128"
    assert "ports" not in eg and "ports" not in SERVICES["assistant"]


def test_an071_sandbox_has_no_network_secrets_lake_or_evaluator() -> None:
    sb = SERVICES["analyst-sandbox"]
    assert sb["network_mode"] == "none" and "networks" not in sb
    assert sb["read_only"] is True and sb["cap_drop"] == ["ALL"] and sb["user"] == "10001:10001"
    assert "secrets" not in sb and "environment" not in sb
    assert sb["volumes"] == ["analyst-jobs:/jobs"]
    assert sb["ulimits"]["nproc"] <= 64 and "memory" in sb["deploy"]["resources"]["limits"]
    dockerfile = (ROOT / sb["build"]["dockerfile"]).read_text()
    copies = [line for line in dockerfile.splitlines() if line.startswith("COPY")]
    assert copies == ["COPY services/assistant/sandbox/worker.py /opt/sandbox/worker.py"]
    for path in MANIFEST["paths"]:
        assert path not in dockerfile and all(path not in v for v in sb["volumes"])
    tree = ast.parse((ROOT / "services" / "assistant" / "sandbox" / "worker.py").read_text())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= {
        "__future__",
        "importlib",
        "inspect",
        "json",
        "os",
        "resource",
        "subprocess",
        "sys",
        "time",
        "pathlib",
        "numpy",
    }


def test_an072_checker_has_no_network() -> None:
    ck = SERVICES["candidate-checker"]
    assert ck["network_mode"] == "none" and "networks" not in ck
    assert ck["command"] == ["candidates", "watch", "/jobs"] and ck["volumes"] == ["analyst-jobs:/jobs"]
    assert "secrets" not in ck


def test_assistant_secrets_are_files_and_minimal() -> None:
    sec = set(SERVICES["assistant"]["secrets"])
    assert sec == {
        "pg_svc_assistant_password",
        "nats_assistant_password",
        "svc_assistant_ed25519",
        "svc_api_gateway_ed25519.pub",
        "llm_anthropic_api_key",
        "llm_openai_api_key",
    }
    assert not [k for k in SERVICES["assistant"]["environment"] if "KEY" in k or "PASSWORD" in k]
    for name in ("ml", "optimizer"):
        assert "svc_assistant_ed25519.pub" in SERVICES[name]["secrets"]
