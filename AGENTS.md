# AGENTS.md

Start here. This file is the entry point for any coding agent (or human) working in this repo. Read it fully before your first change, then read the docs it points to for the area you touch.

## What this is

A self-hosted platform for researching, training, backtesting, deploying, and monitoring ML-driven **day and swing trading strategies for US equities**, for a single technical owner.

- Connects to **Alpaca** and **Interactive Brokers (IBKR)** for market data, account data, and orders (paper and live).
- Stores market and context data locally in a fast analytical store with a hot cache, **point-in-time correct**.
- Trains a model zoo, backtests realistically, and runs an automated optimization loop with a model registry.
- Runs each strategy in its own **virtual sub-portfolio** inside the real broker account. Strategies trade only their own positions and **never touch manual holdings**.
- Desktop and mobile UI; local first (Docker Compose), later a remote server reachable only over a private network.

**Status:** Phase 5 (operations and full UI) in progress; Phases 0–4 merged. Stack choices are accepted from the research report; see [`docs/decisions.md`](docs/decisions.md). Build prompts live in [`docs/prompts/`](docs/prompts/00-master-prompt.md) and the owner's inputs in [`docs/project-context.md`](docs/project-context.md).

## System map

Stack choices are accepted per [`docs/decisions.md`](docs/decisions.md) (research report in [`docs/research/`](docs/research-report.md)). Keep each choice behind an interface so it can be swapped.

| Area | Path | Responsibility | Stack |
|---|---|---|---|
| Contracts | `contracts/` | Cross-service schemas (single source of truth) | Protobuf + gRPC via `buf`; REST + SSE to the UI (D3) |
| API gateway | `services/api-gateway/` | Auth, sessions, authorization policy, REST + SSE for UI, static UI, TradingView webhook intake (later) | Go (D1), go-webauthn (D15) |
| Broker gateway | `services/broker-gateway/` | Credential custody (envelope encryption), Alpaca adapter, order submit/cancel, execution + account streams, simulator | Go (D1) |
| IBKR adapter | `services/broker-gateway-ibkr/` | IBKR sessions and orders via IB Gateway + IBC: maintenance-aware supervision (daily restart, weekly 2FA), orders with `orderRef` attribution, executions, snapshots; serves `BrokerGatewayService` | Python `ib_async` (D1, D18, D41; [ADR-0023](docs/adr/0023-ibkr-adapter-gateway-and-sessions.md)) |
| Market data | `services/marketdata/` | Backfill, streaming, bar building, gap repair, lake + cache writes | Go (D1); Alpaca SIP + FMP (D20) |
| Ledger | `services/ledger/` | Real account snapshots, virtual strategy ledgers, `manual` bucket, lots, reconciliation | Go (D1); IBKR bot account + Alpaca symbol segregation (D19) |
| Execution | `services/execution/` | Strategy runtime / signal intake, OMS state machine, pre-trade risk, kill switches, routing and margin guard | Go (D1); routing per D30 |
| Context data | `services/context-data/` | Point-in-time context: FMP calendars/earnings/statement snapshots, ALFRED vintages, FINRA short interest, EDGAR filings, Alpaca news (via broker-gateway) with quarantined sentiment, regimes; published on `ctx.<kind>`, written to the lake by marketdata | Python (ADR-0032, ADR-0033) |
| Strategy runtime | `services/strategy-runtime/` | Runs `tbcore.strategy` strategies on final bars and publishes intents on `ord.intent` (never talks to brokers) | Python (D1, D44) |
| ML | `services/ml/` | Training jobs, model zoo, inference | Python; MLflow 3 (D8), Optuna 5 (D9), LightGBM (D23); native launchd workers on macOS (D29) |
| Backtest | `services/backtest/` | Backtest runs (engines in `tbcore.backtest`), parity, leakage suite, D31 promotion scorer, evaluator manifest | Python, custom two-tier on `tbcore` with the live OMS in `tb-simcore` (D7, D49, D52) |
| Optimizer | `services/optimizer/` | Studies, search spaces, holdout discipline, champion/challenger | Python; honest-optimization policy (D31) |
| Assistant | `services/assistant/` | LLM agent with constrained tools | Python, Pydantic AI + MCP; hybrid API/local LLM (D26) |
| Monitor | `services/monitor/` | Drift, divergence, health, alerts, auto-demotion | Python |
| Shared libs | `libs/go/`, `libs/py/tbcore/` | Config, logging, secrets, policy, audit, bus, clock; **features, labels, splitters, cost models, strategy API** (shared by train, backtest, and live) | Go / Python |
| Web UI | `apps/web/` | Desktop + mobile PWA, charts, order entry, research dashboards, hosted signup and billing | Vite + React 19 SPA, TanStack, Tailwind, shadcn/ui (D11); SSE (D14); PWA + Pushover (D13); Clerk when configured, else a local mock (D73) |
| Marketing site | `apps/www/` | Public pages for https://strwbrry.ai (pricing, help, legal, status) | Static HTML generator, no framework (D73, [ADR-0055](docs/adr/0055-strwbrry-hosted-product-shell.md)) |
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

Prerequisites: Docker (Colima on the Mac Mini, ADR-0013), `make`, `git`. Developing code also needs Go 1.27, Python 3.12 + `uv`, Node ≥ 22.15 + `pnpm` (versions in `tools/versions.env`).

| Command | Does |
|---|---|
| `make bootstrap` | First run: data dirs, generate `.secrets/` (`make secrets`), build images |
| `make up` / `make down` / `make clean` | Start (profiles `core,observability`; `PROFILES=...` to change; `NATIVE_ML=1` for native macOS workers) / stop / stop and delete volumes |
| `make setup-token` | One-time token for first-run owner setup at http://localhost:8080 |
| `make tv-secret NAME=…` | Print the TradingView webhook secret of strategy `tv-NAME` on the terminal (never logged or shown in the UI; runbook `tradingview-webhook.md`) |
| `make ps`, `make logs SERVICE=x` | Status and logs; Grafana at http://localhost:3000 |
| `make tools`, `make deps` | Pinned dev tools into `.tools/bin`; Go/uv/pnpm dependencies |
| `make gen` / `make gen-check` | Regenerate contracts / fail if generated code is stale |
| `make lint` | buf lint + breaking, golangci-lint, ruff + mypy + point-in-time feature lint, tsc + eslint, docs gate |
| `make test` | Go (`-race`), Python, and web unit tests |
| `make test-integration` | Go tests including Postgres-backed integration tests (throwaway container) |
| `make paper-ibkr` | D30 swing routing through the real IBKR adapter over its fake gateway (GTC protective stop, ledger booking) |
| `make paper-gate` | Phase 2 gate: five simulated ORB sessions through the Python runtime, OMS, simulators, and ledger (writes `paper-gate-evidence.json` and the bus recording `paper-gate.tbrec`) |
| `make live-gate` | Section 15 gate on the paper harness: live mode refused without the go-live flow, analyst denied, a paper account rejects a live-mode order (simulators only) |
| `make research-gate` | Gate 4 (sections 10, 11): owner training of the MVP zoo, a pre-registered nested study in a MinBTL cycle, the holdout service, promotion gates, and champion vs challenger over real ml/optimizer/holdout gRPC with service tokens (writes `research-gate-evidence.json`) |
| `make monitoring-gate` | Gate 5 (section 13): the Python monitor contains injected feature drift, live-vs-backtest divergence on recorded sessions, and a drawdown beyond 1.5× the backtest p95 through execution's `DemoteStrategy` with service tokens (paper → halted, live → paper), manual untouched (writes `monitoring-gate-evidence.json`) |
| `make parity-gate` | Gate 3 parity: replays `paper-gate.tbrec` through the event-driven backtester with recorded and modeled fills (writes `parity-report-*.json`; run after `make paper-gate`) |
| `cd tests/paper && go test ./...` | Phase 2 Gate 2 scenarios on the in-process paper harness (simulators, ledger, execution, embedded NATS) |
| `make smoke` | Smoke test of the running stack (readiness, auth gate, ports, e2e passkey flow with leak canary, Prometheus, audit chain) |
| `make e2e` / `make e2e-ui` | Go API e2e / Playwright browser e2e against a fresh running stack (Phase 0–5 journeys, desktop and phone viewports; screenshots in `apps/web/test-results/screens`) |
| `make migrate` | Roles, databases, schemas, migrations |
| `make audit-verify` | Recompute the audit hash chain |
| `make backup` / `make restore BACKUP=...` / `make backup-drill` | Encrypted backup / verified restore / full drill |
| `make secrets-encrypt` / `make secrets-decrypt` | SOPS + age bundle of `.secrets/` |
| `make up PROFILES=core,observability,ibkr` | Adds the paper IB Gateway and IBKR adapter (`ibkr-live` for live; setup in [`docs/runbooks/ibkr-weekly-2fa.md`](docs/runbooks/ibkr-weekly-2fa.md)) |
| `make ibkr-vnc` / `make ibkr-vnc-stop` | On-demand loopback VNC to IB Gateway for a manual login |
| `make test-ibkr-contract` | Shared Go broker contract suite against the IBKR adapter over its fake gateway |
| `make test-ibkr-paper` | Opt-in tests and spike V1 against a real IB Gateway paper session |
| `make port-check` / `make check-arm64` | Loopback-only ports / arm64 availability of every image |
| `make docs-check` | File-map coverage and same-commit documentation rules |
| `pnpm --filter @strawberry/www test` | Build the Strwbrry marketing site and check titles, index rules, and the operator address |
| `make gen-calendar` | Regenerate the shared XNYS session table (Go + Python) from `exchange_calendars` |
| `make ctx-backfill SOURCE=… START=… END=…` | Context-data backfill of one source in the running stack (idempotent) |
| `make test-ctx-live` | Opt-in context-data tests against real APIs (FINRA, EDGAR; FRED/FMP with keys) |
| `make spike-v6` | Verify-early V6: FMP earnings report timing in `/stable` (needs `.secrets/fmp_api_key`) |
| `make perf` | Storage performance targets (lake load, as-of join) on this machine |
| `make simcore` | Build `tb-simcore` (live OMS, gate, and ledger core for the event-driven backtester) into `.tools/bin`; native workers set `TB_SIMCORE_BIN` |
| `make perf-backtest` | Backtest performance targets: event-driven 1 year × 100 symbols × 5m < 60 s, vectorized 1,000 parameter sets < 5 min |
| `make spike-v4` | Verify-early V4: atomic rename across the lake bind mount (Mac Mini) |

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
