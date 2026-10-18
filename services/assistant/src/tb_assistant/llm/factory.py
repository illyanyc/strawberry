"""Build one provider per role from settings (AN-050, AN-053, AN-054)."""

from __future__ import annotations

from pathlib import Path

import httpx

from tb_assistant.config import Settings
from tb_assistant.llm.base import LLMProvider, Role
from tb_assistant.llm.fake import FakeLLM
from tb_assistant.llm.http import AnthropicProvider, OpenAICompatibleProvider
from tbcore.secrets import SecretNotFoundError, get_secret

LOCAL_KINDS = frozenset({"fake", "ollama", "mlx"})
ALL_KINDS = LOCAL_KINDS | {"anthropic", "openai"}


def _secret(name: str, secrets_dir: str | Path) -> str:
    try:
        return get_secret(name, secrets_dir).decode().strip()
    except SecretNotFoundError:
        return ""


def build_provider(kind: str, cfg: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> LLMProvider:
    if kind not in ALL_KINDS:
        raise ValueError(f"unknown LLM provider {kind!r} (expected one of {sorted(ALL_KINDS)})")
    allow, proxy, timeout = cfg.egress_allowlist(), cfg.llm_proxy or None, cfg.llm_timeout_seconds
    if kind == "fake":
        return FakeLLM(mode=cfg.llm_fake_mode)
    if kind == "anthropic":
        return AnthropicProvider(
            model=cfg.llm_anthropic_model,
            api_key=_secret("llm_anthropic_api_key", cfg.secrets_dir),
            base_url=cfg.llm_anthropic_url,
            allowlist=allow,
            proxy=proxy,
            timeout=timeout,
            transport=transport,
        )
    if kind == "openai":
        return OpenAICompatibleProvider(
            kind="openai",
            model=cfg.llm_openai_model,
            api_key=_secret("llm_openai_api_key", cfg.secrets_dir),
            base_url=cfg.llm_openai_url,
            allowlist=allow,
            proxy=proxy,
            timeout=timeout,
            transport=transport,
        )
    url, model = (
        (cfg.llm_ollama_url, cfg.llm_ollama_model) if kind == "ollama" else (cfg.llm_mlx_url, cfg.llm_mlx_model)
    )
    return OpenAICompatibleProvider(
        kind=kind, model=model, base_url=url, allowlist=allow, proxy=proxy, timeout=timeout, transport=transport
    )


def build_providers(cfg: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> dict[Role, LLMProvider]:
    """Providers per role. Local-only mode routes every role to the local provider; the quarantined
    role must always be local (AN-054)."""
    wanted = {
        Role.PLANNER: cfg.llm_planner_provider,
        Role.CODER: cfg.llm_coder_provider,
        Role.CHAT: cfg.llm_chat_provider,
        Role.QUARANTINED: cfg.llm_quarantine_provider,
    }
    if cfg.llm_local_only:
        if cfg.llm_local_provider not in LOCAL_KINDS:
            raise ValueError("llm_local_provider must be fake, ollama, or mlx")
        wanted = dict.fromkeys(wanted, cfg.llm_local_provider)
    if wanted[Role.QUARANTINED] not in LOCAL_KINDS:
        raise ValueError("the quarantined role must use a local provider (fake, ollama, or mlx)")
    cache: dict[str, LLMProvider] = {}
    out: dict[Role, LLMProvider] = {}
    for role, kind in wanted.items():
        if kind not in cache:
            cache[kind] = build_provider(kind, cfg, transport=transport)
        out[role] = cache[kind]
    return out
