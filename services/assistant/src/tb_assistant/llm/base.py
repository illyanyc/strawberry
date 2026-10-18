"""Provider-neutral LLM call types (AN-050).

Every role (planner, coder, quarantined, chat) talks to an :class:`LLMProvider`. Providers only move
text: masking, the egress guard, budgets, validation, and logging wrap them in
:mod:`tb_assistant.guarded`, so no provider can be called around those checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Role(StrEnum):
    PLANNER = "planner"
    CODER = "coder"
    QUARANTINED = "quarantined"
    CHAT = "chat"


@dataclass(frozen=True)
class Message:
    role: str  # "user" or "assistant"
    content: str


@dataclass(frozen=True)
class LLMRequest:
    role: Role
    system: str
    messages: tuple[Message, ...]
    # JSON Schema of the expected output; providers that support constrained JSON use it.
    json_schema: dict[str, object] | None = None
    max_tokens: int = 4096
    temperature: float = 0.0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    provider: str
    model: str
    latency_ms: float = 0.0
    extra: dict[str, object] = field(default_factory=dict)


class LLMError(RuntimeError):
    """A provider call failed (transport, HTTP status, or malformed response)."""


class LLMProvider(Protocol):
    """One configured model behind one endpoint."""

    @property
    def name(self) -> str:
        """Provider kind: anthropic, openai, ollama, mlx, or fake."""
        ...

    @property
    def model(self) -> str: ...

    @property
    def is_local(self) -> bool:
        """True when the endpoint runs on the owner's host (no data leaves the box)."""
        ...

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
