# ADR-0033: News sentiment through a quarantined local model, with a lexicon baseline and contamination flags

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D51 (settles the section 09 "sentiment model" decision; applies D26 local-model role and D31 rule 10)
- **Related:** CX-011, CX-012, CX-013, CX-014, SE-012, [ADR-0032](0032-context-data-pipeline-and-point-in-time-rules.md), research 03 §3.2 rule 10 and §5 (quarantined local model, lethal trifecta), research 02 §2 (news sentiment, Profit Mirage look-ahead), section 09 prompt (FinBERT baseline, LLM scoring path)

## Context

News text is untrusted (prompt injection), and LLM sentiment is look-ahead-prone: a model trained on data after the article knows how the story ended (research 02, "Profit Mirage"). D31 rule 10 requires news to reach models only through a quarantined, tool-less local model, with scores before that model's training cutoff marked contaminated and excluded from backtests.

The section prompt names FinBERT as the baseline. But the context-data container is CPU-only with a small memory limit, the Mac Mini RAM is unknown (V5), and torch/transformers would add about 1 GB to every image and the shared lockfile.

## Decision

1. **One quarantine wrapper for every backend** (`QuarantinedScorer`):
   - Input: sanitized, length-limited headline plus summary inside fixed delimiters. No dates, prices, tickers' account data, or tool definitions.
   - Output: must validate against a fixed schema (`score` in [-1, 1], `label`, `confidence`; extra keys rejected). Invalid output leaves the article unscored and is never retried with a modified prompt.
   - Logging: model, version, input hash, raw output (truncated), and latency for every call.
   - Determinism: identical inputs hit a cache, so a fixed model version always returns the same score.
2. **Backends:**
   - `lexicon` (`lexicon-v1`, the default): a deterministic built-in financial word list with negation. It has no learned parameters, so it has no training cutoff and is never contaminated. It always runs in CI and in the container.
   - `local_llm`: an Ollama-compatible `/api/chat` endpoint (temperature 0, seed 0, JSON-schema `format`, no `tools`). It must be on an allowlisted local host (loopback, `host.docker.internal`, `ollama`, `llm`), and redirects are refused. It refuses to start without a declared model version and training cutoff. FinBERT or a small MLX/Ollama model runs natively on the Mac behind this interface (D25/D29) instead of inside the container.
3. **Contamination:** every score row stores model, version, training cutoff, and `contaminated` (article published before the cutoff). `ContextStore.sentiment`, `sentiment_agg`, and `context_features` exclude contaminated scores unless explicitly asked.
4. **Availability:** live scores are available when scored; backfilled scores at the article's availability plus the configured latency (2 s). Sentiment is just another feature; nothing downstream can act on raw text.

## Alternatives considered

| Option | Why not |
|---|---|
| FinBERT in-process in context-data | torch/transformers in the shared lockfile and every image; the 768 MB container limit; still needs the same quarantine and cutoff handling; can run behind `local_llm` natively instead |
| API LLM scoring | Violates D26/D31 (raw news to an API model, no local quarantine); look-ahead within its training window |
| Loughran-McDonald dictionary | Licence restricts commercial use; an own small list avoids the question for a baseline |
| No scoring until a model is chosen | Leaves the sentiment features and the contamination path untested; the lexicon gives a deterministic baseline today |

## Consequences

- Positive:
  - Prompt injection cannot reach tools or change anything but one bounded number.
  - Contaminated history is excluded by default.
  - The pipeline runs end to end without a GPU or any model download.
- Negative / risks:
  - The lexicon baseline is weak; any edge must be shown against it in honest validation before adding a model (the section's "free baseline first" pitfall).
  - Scores from different models are separate records keyed by model version; switching models re-scores the backfill.
- Follow-up: native FinBERT/MLX worker plist (Phase 4 with D29 workers); measured scoring latency to replace the configured 2 s; the analyst's access to scores (section 12) stays limited to numeric features.

## Validation

- `services/context-data/ctx_tests/test_quarantine.py`: a valid reply, an injection echo and an out-of-range reply both rejected, no `tools` in the request, caching, cutoff contamination, endpoint allowlist, redirect refusal.
- `test_sources.py`: injected headline scored by content; determinism.
- `libs/py/tbcore/tests/test_context.py`: contaminated scores excluded from aggregates and the store.
