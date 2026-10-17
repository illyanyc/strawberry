# AGENTS.md

Start here. This file is the entry point for any coding agent (or human) working in this repo. Read it fully before your first change, then read the docs it points to for the area you touch.

## What this is

A self-hosted platform for researching, training, backtesting, deploying, and monitoring ML-driven **day and swing trading strategies for US equities**, for a single technical owner.

- Connects to **Alpaca** and **Interactive Brokers (IBKR)** for market data, account data, and orders (paper and live).
- Stores market and context data locally in a fast analytical store with a hot cache, **point-in-time correct**.
- Trains a model zoo, backtests realistically, and runs an automated optimization loop with a model registry.
- Runs each strategy in its own **virtual sub-portfolio** inside the real broker account. Strategies trade only their own positions and **never touch manual holdings**.
- Desktop and mobile UI; local first (Docker Compose), later a remote server reachable only over a private network.

**Status:** Phase 0 (foundation) in progress. Stack choices are accepted from the research report; see [`docs/decisions.md`](docs/decisions.md). Build prompts live in [`docs/prompts/`](docs/prompts/00-master-prompt.md) and the owner's inputs in [`docs/project-context.md`](docs/project-context.md).

## System map

Stack choices are accepted per [`docs/decisions.md`](docs/decisions.md) (research report in [`docs/research/`](docs/research-report.md)). Keep each choice behind an interface so it can be swapped.

| Area | Path | Responsibility | Stack |
|---|---|---|---|
| Contracts | `contracts/` | Cross-service schemas (single source of truth) | Protobuf + gRPC via `buf`; REST + SSE to the UI (D3) |
| API gateway | `services/api-gateway/` | Auth, sessions, authorization policy, REST + SSE for UI, static UI, TradingView webhook intake (later) | Go (D1), go-webauthn (D15) |
| Broker gateway | `services/broker-gateway/` | Credential custody (envelope encryption), Alpaca adapter, order submit/cancel, execution + account streams, simulator | Go (D1) |
| IBKR adapter | `services/broker-gateway-ibkr/` (Planned, Phase 1) | IBKR sessions and orders via IB Gateway + IBC | Python `ib_async` (D1, D18) |
| Market data | `services/marketdata/` | Backfill, streaming, bar building, gap repair, lake + cache writes | Go (D1); Alpaca SIP + FMP (D20) |
| Ledger | `services/ledger/` | Real account snapshots, virtual strategy ledgers, `manual` bucket, lots, reconciliation | Go (D1); IBKR bot account + Alpaca symbol segregation (D19) |
| Execution | `services/execution/` | Strategy runtime / signal intake, OMS state machine, pre-trade risk, kill switches, routing and margin guard | Go (D1); routing per D30 |
| Context data | `services/context-data/` | News, sentiment, macro, earnings, regime series | Python |
| ML | `services/ml/` | Training jobs, model zoo, inference | Python; MLflow 3 (D8), Optuna 5 (D9), LightGBM (D23); native launchd workers on macOS (D29) |
| Backtest | `services/backtest/` | Vectorized + event-driven backtests, validation stats | Python, custom two-tier on `tbcore` (D7) |
| Optimizer | `services/optimizer/` | Studies, search spaces, holdout discipline, champion/challenger | Python; honest-optimization policy (D31) |
| Assistant | `services/assistant/` | LLM agent with constrained tools | Python, Pydantic AI + MCP; hybrid API/local LLM (D26) |
| Monitor | `services/monitor/` | Drift, divergence, health, alerts, auto-demotion | Python |
| Shared libs | `libs/go/`, `libs/py/tbcore/` | Config, logging, secrets, policy, audit, bus, clock; **features, labels, splitters, cost models, strategy API** (shared by train, backtest, and live) | Go / Python |
| Web UI | `apps/web/` | Desktop + mobile PWA, charts, order entry, research dashboards | Vite + React 19 SPA, TanStack, Tailwind, shadcn/ui (D11); SSE (D14); PWA + Pushover (D13) |
| Infra | `infra/` | Compose profiles, images, macOS host setup, observability config, encrypted secrets | Docker Compose on Mac Mini via Colima (D21, D28); Grafana/Prometheus/VictoriaLogs/Alloy (D22); SOPS + age (D16); Tailscale + optional Cloudflare Access (D17) |
| Storage | — | Bars/ticks: Parquet lake + DuckDB on host APFS (D4); state: PostgreSQL (D5); cache: Valkey 9 (D6); bus: NATS JetStream (D2); artifacts: filesystem (D8) | |

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
