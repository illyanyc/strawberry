# ADR-0042: Analyst LLM providers, prompt masking, and egress control

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D60 (refines D26)
- **Related:** AN-023, AN-050..AN-056; ADR-0033 (quarantined sentiment), ADR-0011 (secrets); research [03 §4–§5](../research/03-optimization-and-ai-agent.md), D26, D31 rules 10 and 12

## Context

The AI Research Analyst needs an LLM for planning, code generation, untrusted-text triage, and chat. The owner's Mac Mini RAM is not yet known (V5), so the platform must work with an API model, a local model, or both, and switch with configuration only. LLMs reproduce pre-cutoff financial history even when names are masked, and the "lethal trifecta" (private data + untrusted text + external communication) makes prompt injection a data-exfiltration risk. D26 accepted a pluggable hybrid with masked, de-identified prompts and an egress allowlist; this ADR fixes how.

## Decision

1. **One provider per role** behind a tiny interface (`LLMProvider.complete(LLMRequest) -> LLMResponse`): `anthropic` (Messages API) and `openai` (Chat Completions) with keys only from mounted secrets `llm_anthropic_api_key` / `llm_openai_api_key`; `ollama` and `mlx` (both OpenAI-compatible local endpoints on the Mac host); `fake` (deterministic, CI). Roles: planner, coder, chat, quarantined. `TB_LLM_LOCAL_ONLY=true` routes every role to the local provider. Plain `httpx`, no vendor SDKs: two wire formats cover every provider, and we control redirects, proxies, and error text.
2. **Every call goes through `GuardedLLM`**: spend-cap check (API providers), masking of the structured prompt, deterministic serialization, the egress guard on the exact outbound bytes, the call, JSON extraction, Pydantic v2 validation against the role's strict schema (one corrective retry), pricing, and an LLM call log (role, provider, model, prompt SHA-256, masked prompt and response, tokens, cost, latency, outcome).
3. **Masking for planner, coder, and chat prompts regardless of provider** (memorization is a property of the model, not of where it runs): tickers and instrument IDs → per-round aliases `I001…`; dates and timestamps → day offsets from a per-round anchor (`D+12`); account, balance, position, quantity, raw price, and currency-amount fields dropped by key. Aliases are reversed only in the orchestrator after validation.
4. **Egress guard** (fail closed): blocks a payload that still contains a known ticker or instrument ID, an ISO/compact/month-name date, an epoch timestamp, an account identifier pattern, or a currency amount.
5. **Egress allowlist in two layers**: provider base URLs must be on `TB_LLM_EGRESS_ALLOW` (`host:port`), redirects are refused, and in Compose the assistant has no route to the internet: it reaches providers only through `llm-egress`, a small allowlisting HTTP CONNECT proxy that is the only analyst component on an egress network.
6. **Quarantined role is local-only and tool-less** and returns only `UntrustedSummary` (enumerated topics, sentiment, injection and urgency flags). Untrusted text (run notes, news, user feature descriptions) never enters planner or coder prompts.
7. **Spend caps**: daily (default $5) and monthly (default $50) caps across the analyst and chat, priced from token counts (`TB_LLM_PRICES`); alert at 80 %; at a cap, API calls are refused and local providers keep working.

## Alternatives considered

| Option | Why not |
|---|---|
| Vendor SDKs (`anthropic`, `openai`) | Two more dependency trees in a security-sensitive container for two small JSON endpoints; harder to guarantee no redirects, no env-proxy pickup, and no key echo in errors |
| Pydantic AI model adapters | Their value (typed outputs, tool calls) is covered by our strict schemas and the deterministic tool server (ADR-0041); the masking and egress guard must sit below every provider, which is simpler in our own thin layer |
| Mask only for API providers | Local models memorize the same history; look-ahead through the model is independent of where it runs |
| Network allowlist only in-process | A compromised dependency could open its own socket; the Compose network plus the proxy make the allowlist independent of the Python process |
| Free-text summaries from the quarantined model | Free text is an injection channel into the planner; enumerated fields are not |

## Consequences

- Positive: switching between API, local, and local-only is configuration; no identifying data or untrusted text can reach an API model without tripping a test-covered guard; every call is replayable and priced.
- Negative / risks: masking removes context a model might use (e.g., sector names stay, tickers do not); the guard can block a legitimate payload (fails closed, visible in the call log); local tool calling quality on small models is uneven, which is why the analyst never relies on model-side tool calling.
- Follow-up: `llm-egress` Compose service and the `research` network (infra commit); owner action to write API keys or start a local model.

## Validation

`services/assistant/tests/test_llm_layer.py` (request shapes, keys only in headers, allowlist and redirect refusal, masking and unmasking, guard patterns, retry and logging, spend caps, quarantine locality and enumerated output); the evaluation suite's injection and egress scenarios (AN-081). Revisit when a provider needs streaming tool calls or when V5 shows enough RAM for a local planner.
