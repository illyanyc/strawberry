"""The only way the assistant calls an LLM (AN-023, AN-050..AN-055).

``GuardedLLM.complete_json`` does, in order: spend-cap check (API providers), masking of the
structured prompt, serialization, the egress guard on the exact outbound bytes, the provider call,
JSON extraction, Pydantic validation (one corrective retry), pricing, and the call log. A role
cannot reach its provider any other way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from tb_assistant.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse, Message, Role
from tb_assistant.masking import EgressBlockedError, EgressGuard, Masker, dumps

log = logging.getLogger("tb_assistant.llm")
T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output). Config overrides via TB_ANALYST_LLM_PRICES (JSON).
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "anthropic": (2.0, 10.0),
    "openai": (2.0, 10.0),
    "ollama": (0.0, 0.0),
    "mlx": (0.0, 0.0),
    "fake": (0.0, 0.0),
}


class SpendCapError(RuntimeError):
    """A daily or monthly LLM spend cap is reached (AN-023)."""


class OutputInvalidError(RuntimeError):
    """The LLM's output failed schema validation twice."""


@dataclass
class LLMCallRecord:
    call_id: str
    role: str
    provider: str
    model: str
    prompt_sha256: str
    prompt: str
    response: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    ok: bool
    error: str
    lineage_id: str
    round_id: str
    created_at: datetime


class CallLog(Protocol):
    async def record_llm_call(self, rec: LLMCallRecord) -> None: ...


@dataclass
class SpendLedger:
    """Daily and monthly LLM spend across every role and lineage (AN-023)."""

    max_usd_per_day: float = 5.0
    max_usd_per_month: float = 50.0
    alert_fraction: float = 0.8
    on_alert: Callable[[str], Awaitable[None]] | None = None
    _day: str = ""
    _month: str = ""
    today_usd: float = 0.0
    month_usd: float = 0.0
    _alerted: set[str] = field(default_factory=set)

    def _roll(self, now: datetime) -> None:
        d, m = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        if d != self._day:
            self._day, self.today_usd = d, 0.0
        if m != self._month:
            self._month, self.month_usd = m, 0.0

    def check(self, now: datetime) -> None:
        self._roll(now)
        if self.today_usd >= self.max_usd_per_day:
            raise SpendCapError(f"daily LLM spend cap {self.max_usd_per_day:.2f} reached")
        if self.month_usd >= self.max_usd_per_month:
            raise SpendCapError(f"monthly LLM spend cap {self.max_usd_per_month:.2f} reached")

    async def add(self, usd: float, now: datetime) -> None:
        self._roll(now)
        self.today_usd += usd
        self.month_usd += usd
        for key, used, cap in (
            ("day", self.today_usd, self.max_usd_per_day),
            ("month", self.month_usd, self.max_usd_per_month),
        ):
            tag = f"{key}:{self._day if key == 'day' else self._month}"
            if cap > 0 and used >= self.alert_fraction * cap and tag not in self._alerted:
                self._alerted.add(tag)
                if self.on_alert:
                    await self.on_alert(f"LLM spend at {used / cap:.0%} of the {key} cap")


def extract_json(text: str) -> Any:
    """First JSON object in a response (models sometimes wrap JSON in prose or code fences)."""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in response")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unterminated JSON object in response")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class GuardedResult[T: BaseModel]:
    output: T
    record: LLMCallRecord
    masked_prompt: Any


class GuardedLLM:
    """Wraps one provider per role with masking, egress guard, caps, validation, and logging."""

    def __init__(
        self,
        providers: dict[Role, LLMProvider],
        *,
        spend: SpendLedger,
        call_log: CallLog | None = None,
        prices: dict[str, tuple[float, float]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.providers = providers
        self.spend = spend
        self.call_log = call_log
        self.prices = {**DEFAULT_PRICES, **(prices or {})}
        self.clock = clock
        self._ids = id_factory or _counter()

    def provider(self, role: Role) -> LLMProvider:
        return self.providers[role]

    def price(self, resp: LLMResponse) -> float:
        pin, pout = self.prices.get(resp.provider, (0.0, 0.0))
        return (resp.input_tokens * pin + resp.output_tokens * pout) / 1e6

    async def complete_json(
        self,
        role: Role,
        *,
        system: str,
        payload: dict[str, Any],
        schema: type[T],
        masker: Masker | None,
        guard: EgressGuard | None,
        lineage_id: str = "",
        round_id: str = "",
        max_tokens: int = 4096,
    ) -> GuardedResult[T]:
        prov = self.providers[role]
        now = self.clock()
        if not prov.is_local:
            self.spend.check(now)
        masked = masker.mask(payload) if masker is not None else payload
        user = dumps(masked)
        if guard is not None:
            try:
                guard.check(system + "\n" + user)
            except EgressBlockedError as exc:
                await self._log(
                    role,
                    prov,
                    system,
                    user,
                    None,
                    False,
                    f"egress_blocked:{','.join(exc.findings)}",
                    lineage_id,
                    round_id,
                )
                raise
        messages: list[Message] = [Message("user", user)]
        last_err = ""
        for attempt in range(2):
            req = LLMRequest(
                role=role,
                system=system,
                messages=tuple(messages),
                json_schema=schema.model_json_schema(),
                max_tokens=max_tokens,
            )
            try:
                resp = await prov.complete(req)
            except LLMError as exc:
                await self._log(role, prov, system, user, None, False, str(exc), lineage_id, round_id)
                raise
            cost = self.price(resp)
            if not prov.is_local or cost:
                await self.spend.add(cost, self.clock())
            try:
                raw = extract_json(resp.text)
                if masker is not None:
                    raw = masker.unmask(raw)
                out = schema.model_validate(raw)
            except (ValueError, ValidationError) as exc:
                last_err = _short_error(exc)
                await self._log(
                    role, prov, system, user, resp, False, f"invalid_output:{last_err}", lineage_id, round_id
                )
                if attempt == 0:
                    messages += [
                        Message("assistant", resp.text[:20000]),
                        Message(
                            "user",
                            "Your reply did not validate against the required JSON schema: "
                            + last_err
                            + ". Reply again with only the corrected JSON object.",
                        ),
                    ]
                    if guard is not None:
                        guard.check(messages[-1].content)
                    continue
                raise OutputInvalidError(last_err) from None
            rec = await self._log(role, prov, system, user, resp, True, "", lineage_id, round_id)
            return GuardedResult(out, rec, masked)
        raise OutputInvalidError(last_err)

    async def _log(
        self,
        role: Role,
        prov: LLMProvider,
        system: str,
        user: str,
        resp: LLMResponse | None,
        ok: bool,
        error: str,
        lineage_id: str,
        round_id: str,
    ) -> LLMCallRecord:
        prompt = system + "\n" + user
        rec = LLMCallRecord(
            call_id=self._ids(),
            role=role.value,
            provider=prov.name,
            model=resp.model if resp else prov.model,
            prompt_sha256=sha256(prompt),
            prompt=prompt,
            response=resp.text if resp else "",
            input_tokens=resp.input_tokens if resp else 0,
            output_tokens=resp.output_tokens if resp else 0,
            cost_usd=self.price(resp) if resp else 0.0,
            latency_ms=resp.latency_ms if resp else 0.0,
            ok=ok,
            error=error[:500],
            lineage_id=lineage_id,
            round_id=round_id,
            created_at=self.clock(),
        )
        log.info(
            "llm call",
            extra={
                "role": rec.role,
                "provider": rec.provider,
                "model": rec.model,
                "ok": ok,
                "prompt_sha256": rec.prompt_sha256,
                "tokens_in": rec.input_tokens,
                "tokens_out": rec.output_tokens,
                "cost_usd": round(rec.cost_usd, 6),
                "error": rec.error,
                "lineage_id": lineage_id,
                "round_id": round_id,
            },
        )
        if self.call_log is not None:
            await self.call_log.record_llm_call(rec)
        return rec


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]
        return "; ".join(parts)[:600]
    return str(exc)[:300]


def _counter() -> Callable[[], str]:
    n = [0]

    def nxt() -> str:
        n[0] += 1
        return f"llm-{int(time.time())}-{n[0]}"

    return nxt
