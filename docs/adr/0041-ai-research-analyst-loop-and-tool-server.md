# ADR-0041: AI Research Analyst: deterministic Centaur loop, tool server, and journal

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D59 (implements the section 12 part of D31; with D60 refines D26)
- **Related:** AN-001..AN-004, AN-010..AN-013, AN-020..AN-023, AN-030..AN-032, AN-040..AN-042, AN-060..AN-063; EX-020..EX-036 (ADR-0035); BT-080/BT-081 (ADR-0034); research [03 §2–§4](../research/03-optimization-and-ai-agent.md)

## Context

Section 12 asks for an analyst that reads every training run, backtest, study, and live result, writes an evidence-cited diagnosis and a ranked plan, and launches the next round within an autonomy level, while never seeing the holdout or the scorer, never promoting to live, and never touching the order path. Research 03 found that LLM agents overfit validation sets, reward-hack visible scorers, track optimization state poorly, and leak the future through pretraining; classical optimizers beat them on fixed spaces, and the Centaur hybrid (optimizer state shared with an LLM) wins. Local models on the Mac may have weak tool calling. Sections 10/11 (ADR-0035) expose one RPC per analyst tool, resolve the actor from the service token, and let the optimizer own the lineage autonomy level and the permission table.

Open choices in section 12: agent framework (Pydantic AI or a minimal custom loop), default autonomy, plateau rule.

## Decision

1. **Centaur split.** Optuna (section 11) does all numerical search; the LLM only diagnoses, proposes hypotheses, search spaces, and config diffs. The analyst never computes statistics: `digest.py` copies values from reports into an evidence table under canonical names.
2. **Deterministic orchestrator, LLM as a pure function.** A plain-Python loop (`loop.py`) owns triggers, rounds, stop rules, budgets, and every tool call. The planner returns one strict Pydantic schema; it never calls tools itself. This keeps control flow auditable, works with local models whose tool calling is unreliable, and makes the round replayable from the journal. We do not adopt Pydantic AI's agent loop (D26 named it): its value is typed tools and outputs, which our schemas and tool server already provide, and an agent-driven tool loop would put the LLM in control of when tools run. MCP exposure of the tool registry stays Planned.
3. **Tool server as the first wall.** Every read and write goes through `ToolServer.call`: unknown or permanently forbidden names (orders, kill switches, limits, allocations, modes, holdout, credentials, scorer, raising autonomy, opening cycles) are denied and audited; the level is read from the optimizer before each call and tools above it are denied (owner-approved level-1 launches may run at level 0); budgets are checked before writes; `holdout*` keys are stripped; gate reasons and thresholds are cleared again (defense in depth for EX-035); results are sanitized and size-limited. The optimizer and ml services enforce the same rules server-side (EX-031, EX-036, ML-024), the holdout service never accepts the assistant (EX-012), and the network keeps the assistant off the `backend` network.
4. **Autonomy.** The optimizer's `Lineage.autonomy_level` is the source of truth (EX-033). Raising goes through api-gateway with an owner passkey step-up; the assistant only lowers (self-limit, monitoring requests). Default level 0. The assistant keeps its own loop budget (trials per round, CPU hours per day, concurrent jobs, rounds, wall clock; LLM spend caps in D60) and pause flag.
5. **Stop rules (deterministic).** Plateau: K = 3 consecutive continuation rounds whose best deflated Sharpe probability improves by less than ε = 0.05. No edge: the diagnosis says so, or ≥ 20 lineage trials with DSR < 0.5 and PBO ≥ 0.5 (forced even if the LLM disagrees). Also: cycle budget spent, `significance_reachable = false` (EX-023) or DSR < 0.1 with more than half the cycle used, two consecutive all-failed rounds (and drop to level 0), pause, round budget. Every stop writes a summary and a recommendation and publishes `analyst.escalation`.
6. **Evidence and validation.** A finding survives only if every cited `(report_id, metric, value)` matches the round's evidence table (relative tolerance 1e-6). Actions must name a known tool for their target; config diffs are JSON merge patches applied to the unmasked current config, parsed back into the proto, and validated by the owning service (`EstimateTrainingJob`) or against the family's JSON Schema (study spaces). The orchestrator recomputes the rank score: expected gain per (1 + compute hours) per (1 + trials), ×1.5 for simplifying actions and ×0.5 for complexity-adding ones when overfitting is present.
7. **Journal.** Postgres schema `analyst`: rounds (protobuf payload plus indexed columns, written before any launch), loop state, handled trigger IDs, LLM call log (masked prompt and response, hash, tokens, cost), tool call log (input/output hashes, outcome), code proposals, conversations. Every round and tool call is also an audit event (actor `analyst`).
8. **Continuations.** Launches are tagged with the round ID (ML-024); the loop maps job and study IDs to the round and runs a continuation round when the last one completes. External triggers that arrive while launches are pending wait (one open round per lineage); at most three lineages run loops at once.

## Alternatives considered

| Option | Why not |
|---|---|
| Pydantic AI agent with tool calling | The LLM would decide when tools run; weaker auditability and replay; local models' tool calling is unreliable; our strict schemas already give typed outputs |
| LangGraph | Graph checkpoints are not needed: the loop is restart-safe from the journal; adds a framework without a matching gain |
| LLM proposes trials directly (LLM as optimizer) | Classical optimizers beat LLMs on fixed spaces (research 03 §2.3); the LLM proposes spaces and hypotheses instead |
| Autonomy level stored by the assistant | The optimizer enforces transitions; a second copy could disagree, and a compromised assistant could raise its own copy |
| Let the LLM decide plateau and no-edge | These are honesty guardrails (D31); they must hold even when the model is wrong or manipulated |

## Consequences

- Positive: every analyst decision is reproducible from the journal and the call logs; guardrails are code, tested independently of model quality; the same pipeline runs on an API model, a local model, or the deterministic fake.
- Negative / risks: the fixed evidence collection may miss a report a model would have asked for (mitigated by including report findings and the full digest); toy-world evaluation measures the pipeline, not a real model's judgment (run the suite against a configured provider for that).
- Follow-up: Prefect durable execution (Planned), MCP tool exposure (Planned), richer coder roles (ADR-0043).

## Validation

`services/assistant/tests/test_loop.py` (level 1 three-plus rounds with shadow deployment and plateau stop, no-edge on noise with all trials counted, level 0 approvals, level 2 promotion limits, forbidden tools at every level, holdout and scorer isolation, masking, budgets, cycle spend, adversarial planner, injection, live feedback, idempotency, self-limiting), the evaluation suite (AN-081), and the Gate 4 analyst-loop evidence against the real section 10/11 services.
