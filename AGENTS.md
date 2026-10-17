# AGENTS.md

Start here. This file is the entry point for any coding agent (or human) working in this repo. Read it fully before your first change, then read the docs it points to for the area you touch.

## What this is

A self-hosted platform for researching, training, backtesting, deploying, and monitoring ML-driven **day and swing trading strategies for US equities**, for a single technical owner.

- Connects to **Alpaca** and **Interactive Brokers (IBKR)** for market data, account data, and orders (paper and live).
- Stores market and context data locally in a fast analytical store with a hot cache, **point-in-time correct**.
- Trains a model zoo, backtests realistically, and runs an automated optimization loop with a model registry.
- Runs each strategy in its own **virtual sub-portfolio** inside the real broker account. Strategies trade only their own positions and **never touch manual holdings**.
- Desktop and mobile UI; local first (Docker Compose), later a remote server reachable only over a private network.

**Status:** pre-code. Only the documentation and agent structure exist. Stack choices are pending a research report; see [`docs/decisions.md`](docs/decisions.md).

## System map

Defaults come from the build plan and stay `DECISION PENDING` until an ADR accepts them. Isolate every pending choice behind an interface.

| Area | Path (planned) | Responsibility | Stack |
|---|---|---|---|
| Contracts | `contracts/` | Cross-service schemas (single source of truth) | Protobuf + gRPC via `buf` (D3, DECISION PENDING) |
| API gateway | `services/api-gateway/` | Auth, sessions, authorization policy, REST/WebSocket for UI, TradingView webhook intake | Core language (D1, DECISION PENDING; default Go) |
| Broker gateway | `services/broker-gateway/` | Alpaca/IBKR adapters, sessions, order submit/cancel, execution + account streams, simulator | D1 (default Go); IBKR via IB Gateway + IBC (D18, DECISION PENDING) |
| Market data | `services/marketdata/` | Backfill, streaming, bar building, gap repair, lake + cache writes | D1 (default Go) |
| Ledger | `services/ledger/` | Real account snapshots, virtual strategy ledgers, `manual` bucket, lots, reconciliation | D1 (default Go) |
| Execution | `services/execution/` | Strategy runtime / signal intake, OMS state machine, pre-trade risk, kill switches | D1 (default Go) |
| Context data | `services/context-data/` | News, sentiment, macro, earnings, regime series | Python |
| ML | `services/ml/` | Training jobs, model zoo, inference | Python; MLflow (D8), Optuna (D9), DECISION PENDING |
| Backtest | `services/backtest/` | Vectorized + event-driven backtests, validation stats | Python (D7, DECISION PENDING) |
| Optimizer | `services/optimizer/` | Studies, search spaces, holdout discipline, champion/challenger | Python |
| Assistant | `services/assistant/` | LLM agent with constrained tools | Python; provider pluggable (D26, DECISION PENDING) |
| Monitor | `services/monitor/` | Drift, divergence, health, alerts, auto-demotion | Python |
| Shared libs | `libs/go/`, `libs/py/tbcore/` | Config, logging, bus client, calendar; **features, labels, splitters, cost models, strategy API** (shared by train, backtest, and live) | Go / Python |
| Web UI | `apps/web/` | Desktop + mobile PWA, charts, order entry, research dashboards | Next.js + Tailwind + shadcn/ui (D11, DECISION PENDING) |
| Infra | `infra/` | Compose profiles, images, Terraform, observability config, encrypted secrets | Docker Compose (D21), Prometheus/Grafana/Loki/Tempo (D22), SOPS+age (D16) |
| Storage | — | Bars/ticks: Parquet lake + DuckDB (D4); state: PostgreSQL (D5); cache: Redis/Valkey (D6); bus: NATS JetStream (D2) | all DECISION PENDING |

Data flows and the research/trading loops are in [`docs/architecture/overview.md`](docs/architecture/overview.md).

## Where things live

| You need | Go to |
|---|---|
| Every file and directory, and what it is for | [`docs/file-map.md`](docs/file-map.md) |
| Architecture, services, data flow | [`docs/architecture/overview.md`](docs/architecture/overview.md) |
| Business rules, invariants, edge cases (by domain) | [`docs/business-logic/`](docs/business-logic/README.md) |
| Stack and design decisions (register) | [`docs/decisions.md`](docs/decisions.md) |
| Architecture Decision Records | [`docs/adr/`](docs/adr/README.md) |
| Domain terms | [`docs/glossary.md`](docs/glossary.md) |
| What changed and when | [`CHANGELOG.md`](CHANGELOG.md) |
| Per-service agent notes | `<service>/AGENTS.md` (convention: [`docs/architecture/service-agents.md`](docs/architecture/service-agents.md)) |
| Agent rules (auto-loaded by Cursor) | [`.cursor/rules/`](.cursor/rules/) |

When you work inside a directory that has its own `AGENTS.md`, read it too. The nearest `AGENTS.md` adds detail; it never overrides this file or the trading-safety rules.

## How to run and test

Not yet available. Planned entry points (placeholders until Phase 0 lands; update this section when they exist):

| Command | Does |
|---|---|
| `make up` / `make down` | Start/stop the local stack (Compose profiles: `core`, `ml`, `gpu`, `observability`, `dev`) |
| `make test` | All unit, contract, leakage, and integration tests |
| `make lint` | Linters and formatters for every language |
| `make gen` | Regenerate code from `contracts/` |
| `make migrate` | Apply database migrations |
| `make docs-check` | Verify the file map covers every tracked path (planned CI gate) |

## Non-negotiables (summary)

Full text: [`.cursor/rules/trading-safety.mdc`](.cursor/rules/trading-safety.mdc). Violating any of these fails review.

1. **Own positions only.** A strategy trades only its virtual portfolio's quantity. It never sells, closes, nets against, or flattens manual holdings or another strategy's positions.
2. **Paper before live.** Every strategy starts in `paper`. Live requires the global flag, an audited per-strategy promotion with step-up auth, and a capital cap.
3. **No look-ahead.** Everything filters on `available_at <= decision_time`; fills are simulated no earlier than the next tradable moment; one shared feature code path for train, backtest, and live.
4. **Risk check before every order.** No order reaches a broker without a recorded pre-trade risk decision.
5. **Secrets never in code, logs, traces, prompts, fixtures, or the browser.**
6. **Fail closed.** Stale data, disconnects, unknown order state, or failed reconciliation stop new risk and alert.

## Documentation protocol (mandatory)

The docs are the system's memory. Agents work across many sessions and cannot rely on chat history, so **every change records its files and business logic in the same commit as the code**. Full rule and checklist: [`.cursor/rules/documentation-discipline.mdc`](.cursor/rules/documentation-discipline.mdc).

| If your change... | You must, in the same commit |
|---|---|
| Adds, moves, renames, or removes a file or directory | Update [`docs/file-map.md`](docs/file-map.md) |
| Adds or changes behavior, a rule, a limit, a state, or an edge case | Update the relevant [`docs/business-logic/`](docs/business-logic/README.md) doc (rule IDs, invariants, edge cases) |
| Does anything user-visible, behavioral, or structural | Add an entry under `## [Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md) |
| Makes or changes an architecture/stack/design decision | Add an ADR in [`docs/adr/`](docs/adr/README.md) and update [`docs/decisions.md`](docs/decisions.md) |
| Introduces a domain term | Add it to [`docs/glossary.md`](docs/glossary.md) |
| Creates a service or changes how one is built, run, or tested | Create or update that service's `AGENTS.md` |

**Never leave a business rule only in code.** Each rule has an ID in its business-logic doc (for example `VP-003`); code and tests that enforce it cite that ID. If code and docs disagree, treat it as a bug: stop, decide which is correct, and fix both in one commit. Never weaken a safety rule in the docs to match code.

## Working conventions

- Branch per task; small commits; each commit leaves code and docs consistent.
- Contracts first: change `contracts/`, regenerate, then implement.
- Tests are part of done. Order-path code needs table-driven or property-based tests for every rejection path.
- Money and quantity are decimals or fixed-point integers, never floats, in the order path and ledger.
- Timestamps are UTC. Market and context records carry `event_time`, `available_at`, `ingested_at`.
- Do not implement another area's scope; add the smallest contract-honoring stub and note it in the PR.
- If an acceptance criterion cannot be met, stop and report why rather than weakening the test.
