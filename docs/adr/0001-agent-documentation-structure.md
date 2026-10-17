# ADR-0001: Agent documentation structure and protocol

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** n/a (process decision)
- **Related:** `AGENTS.md`, `.cursor/rules/documentation-discipline.mdc`

## Context

The platform will be built and maintained mostly by coding agents working in separate sessions, often in parallel, with no shared memory beyond the repo. It moves real money, so business rules (ownership of positions, risk limits, point-in-time data access) must be explicit, reviewable, and testable rather than implied by code. Without a fixed structure, agents rediscover context, duplicate logic, and let docs drift from code.

## Decision

1. **Entry point:** root `AGENTS.md` (what the system is, system map, where things live, run/test, protocol). Each service/app/lib/infra directory gets its own `AGENTS.md` per `docs/architecture/service-agents.md`.
2. **Auto-loaded rules** in `.cursor/rules/`: three always-applied rules (core, documentation discipline, trading safety) and glob-scoped rules per area (backend core, Python ML, frontend, infra).
3. **Docs layout:** `docs/file-map.md` (every path), `docs/architecture/`, `docs/business-logic/` (one doc per domain with rule IDs, invariants, edge cases), `docs/adr/` + `docs/decisions.md`, `docs/glossary.md`, root `CHANGELOG.md`.
4. **Same-commit protocol:** any change that adds/moves/removes files or changes behavior updates the file map, the relevant business-logic doc, and the changelog in the same commit; new decisions get an ADR; new terms go in the glossary.
5. **Rule IDs:** business rules have stable IDs (`OE-`, `VP-`, `RM-`, `SM-`, `DF-`) cited by enforcing code and tests. Business-logic docs are the spec; disagreement between code and docs is a bug.
6. **`docs/decisions.md`** is the register the build prompts reference; ADRs hold the reasoning.

## Alternatives considered

| Option | Why not |
|---|---|
| Docs only in a wiki or the Project store | Not versioned with code; agents in the repo cannot rely on them being current |
| Code comments and docstrings as the spec | Rules scatter across services; no single place to review safety rules |
| Generated docs only (from code) | Captures what code does, not what it must do |
| One large `ARCHITECTURE.md` | Grows unbounded; poor fit for scoped rules and parallel edits |

## Consequences

- Positive: any agent can orient from `AGENTS.md`; safety rules are reviewable and traceable to tests; parallel agents edit separate domain docs.
- Negative: every change carries documentation overhead; docs can still drift if the protocol is skipped.
- Follow-up: add a `make docs-check` CI gate (file map covers all tracked paths; changed code paths require a changelog entry; rule IDs cited in code exist in docs).

## Validation

Reviews reject commits that miss the done checklist. Revisit if the docs-check gate shows persistent drift or the overhead blocks delivery.
