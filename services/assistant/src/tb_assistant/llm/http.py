"""HTTP LLM providers (AN-050, AN-053).

* :class:`AnthropicProvider`: Anthropic Messages API (``/v1/messages``).
* :class:`OpenAICompatibleProvider`: OpenAI Chat Completions (``/v1/chat/completions``), also used
  for local Ollama and MLX (``mlx_lm.server``) endpoints, which speak the same protocol.

API keys come only from mounted secret files (SE-010) and are sent only in request headers; they
never appear in logs or errors (SE-012). Every base URL must be on the egress allowlist before a
provider is constructed; in Compose all traffic additionally goes through the allowlisting
``llm-egress`` proxy.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from tb_assistant.llm.base import LLMError, LLMRequest, LLMResponse

ANTHROPIC_VERSION = "2023-06-01"


class EgressNotAllowedError(ValueError):
    """A provider endpoint is not on the egress allowlist (AN-053)."""


def endpoint_key(base_url: str) -> str:
    """``host:port`` of a base URL (default ports filled in), as used by the allowlist."""
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise EgressNotAllowedError(f"unsupported LLM endpoint URL scheme or host: {parts.scheme!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname.lower()}:{port}"


def check_allowed(base_url: str, allowlist: frozenset[str]) -> None:
    """AN-053: refuse any endpoint not on the allowlist (``host:port`` entries)."""
    key = endpoint_key(base_url)
    if key not in allowlist:
        raise EgressNotAllowedError(f"LLM endpoint {key} is not on the egress allowlist")


class _HTTPProvider:
    def __init__(
        self,
        *,
        kind: str,
        model: str,
        base_url: str,
        allowlist: frozenset[str],
        local: bool,
        proxy: str | None = None,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        check_allowed(base_url, allowlist)
        self._kind = kind
        self._model = model
        self._base = base_url.rstrip("/")
        self._local = local
        # Redirects are refused: a redirect could leave the allowlisted host.
        self._client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, proxy=proxy, transport=transport, trust_env=False
        )

    @property
    def name(self) -> str:
        return self._kind

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_local(self) -> bool:
        return self._local

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, headers: dict[str, str], body: dict[str, object]) -> dict[str, object]:
        try:
            resp = await self._client.post(self._base + path, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self._kind}: transport error ({type(exc).__name__})") from None
        if resp.status_code >= 300:
            # Never echo the response body: providers sometimes reflect request headers.
            raise LLMError(f"{self._kind}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise LLMError(f"{self._kind}: response is not JSON") from None
        if not isinstance(data, dict):
            raise LLMError(f"{self._kind}: unexpected response shape")
        return data


class AnthropicProvider(_HTTPProvider):
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        allowlist: frozenset[str],
        base_url: str = "https://api.anthropic.com",
        proxy: str | None = None,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key or api_key == "unset":
            raise ValueError("anthropic: API key secret llm_anthropic_api_key is not set")
        super().__init__(
            kind="anthropic",
            model=model,
            base_url=base_url,
            allowlist=allowlist,
            local=False,
            proxy=proxy,
            timeout=timeout,
            transport=transport,
        )
        self._key = api_key

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, object] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": request.system,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        headers = {"x-api-key": self._key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        start = time.perf_counter()
        data = await self._post("/v1/messages", headers, body)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise LLMError("anthropic: response has no content")
        text = "".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return LLMResponse(
            text=text,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            provider=self._kind,
            model=str(data.get("model") or self._model),
            latency_ms=(time.perf_counter() - start) * 1000,
        )


class OpenAICompatibleProvider(_HTTPProvider):
    """OpenAI (``kind="openai"``) or a local OpenAI-compatible server (``ollama``, ``mlx``)."""

    def __init__(
        self,
        *,
        kind: str,
        model: str,
        base_url: str,
        allowlist: frozenset[str],
        api_key: str = "",
        proxy: str | None = None,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if kind not in {"openai", "ollama", "mlx"}:
            raise ValueError(f"unsupported OpenAI-compatible provider kind {kind!r}")
        if kind == "openai" and (not api_key or api_key == "unset"):
            raise ValueError("openai: API key secret llm_openai_api_key is not set")
        super().__init__(
            kind=kind,
            model=model,
            base_url=base_url,
            allowlist=allowlist,
            local=kind != "openai",
            proxy=proxy,
            timeout=timeout,
            transport=transport,
        )
        self._key = api_key

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = [{"role": "system", "content": request.system}]
        messages += [{"role": m.role, "content": m.content} for m in request.messages]
        body: dict[str, object] = {"model": self._model, "messages": messages, "temperature": request.temperature}
        # OpenAI's current models take max_completion_tokens; Ollama and mlx_lm.server take max_tokens.
        body["max_completion_tokens" if self._kind == "openai" else "max_tokens"] = request.max_tokens
        if request.json_schema is not None:
            body["response_format"] = {"type": "json_object"}
        headers = {"content-type": "application/json"}
        if self._key:
            headers["authorization"] = f"Bearer {self._key}"
        start = time.perf_counter()
        data = await self._post("/v1/chat/completions", headers, body)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError(f"{self._kind}: response has no choices")
        msg = choices[0].get("message")
        text = str(msg.get("content") or "") if isinstance(msg, dict) else ""
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        return LLMResponse(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            provider=self._kind,
            model=str(data.get("model") or self._model),
            latency_ms=(time.perf_counter() - start) * 1000,
        )
