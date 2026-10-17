# File map

Every tracked file and directory, with its purpose. **Agents must update this in the same commit as any add, move, rename, or delete** (see [`.cursor/rules/documentation-discipline.mdc`](../.cursor/rules/documentation-discipline.mdc)). Service internals are listed in each service's `AGENTS.md`; this map lists the service directory and links there.

Status: **Exists** = in the repo now. **Planned** = target layout from the build plan; create it where shown.

## Root

| Path | Status | Purpose |
|---|---|---|
| `AGENTS.md` | Exists | Agent entry point: system map, where things live, run/test, documentation protocol |
| `README.md` | Exists | Short project intro for humans; points to `AGENTS.md` and `docs/` |
| `CHANGELOG.md` | Exists | Keep a Changelog; every behavioral or structural change gets an `[Unreleased]` entry |
| `Makefile` or `justfile` | Planned | `up`, `down`, `test`, `lint`, `gen`, `migrate`, `docs-check` |

## Agent rules: `.cursor/rules/`

| Path | Status | Applies | Purpose |
|---|---|---|---|
| `core.mdc` | Exists | Always | Project context, decision handling, code conventions, scope |
| `documentation-discipline.mdc` | Exists | Always | Mandatory doc protocol and done checklist |
| `trading-safety.mdc` | Exists | Always | Non-negotiable trading and security rules |
| `backend-core.mdc` | Exists | Core services, `libs/go/`, `contracts/` | Order path, contracts, service conventions |
| `python-ml.mdc` | Exists | Python services, `libs/py/`, `*.py` | Shared feature library, leakage, reproducibility |
| `frontend.mdc` | Exists | `apps/web/` | UI security, trading UX, quality states |
| `infra.mdc` | Exists | `infra/`, Docker, Compose, CI, Terraform | Network, secrets, containers |

## Docs: `docs/`

| Path | Status | Purpose |
|---|---|---|
| `docs/file-map.md` | Exists | This file |
| `docs/glossary.md` | Exists | Domain terms |
| `docs/decisions.md` | Exists | Decision register (D1–D27): status, default, ADR link |
| `docs/architecture/overview.md` | Exists | Components, data flows, the two loops, cross-cutting design, bus subjects |
| `docs/architecture/service-agents.md` | Exists | Convention and template for per-service `AGENTS.md` |
| `docs/business-logic/README.md` | Exists | Index of domain docs, rule-ID scheme, how to write rules |
| `docs/business-logic/_template.md` | Exists | Template for a domain business-logic doc |
| `docs/business-logic/orders-and-execution.md` | Exists | `OE-` rules: intents, OMS states, submission, idempotency |
| `docs/business-logic/virtual-portfolios-and-attribution.md` | Exists | `VP-` rules: ownership, attribution IDs, lots, reconciliation |
| `docs/business-logic/risk-management.md` | Exists | `RM-` rules: pre-trade checks, limits, kill switches, PDT |
| `docs/business-logic/strategies-and-model-lifecycle.md` | Exists | `SM-` rules: modes, promotion, demotion, holdout, reproducibility |
| `docs/business-logic/data-and-features.md` | Exists | `DF-` rules: timestamps, point-in-time, adjustments, feature parity |
| `docs/adr/README.md` | Exists | ADR process and index |
| `docs/adr/template.md` | Exists | ADR template |
| `docs/adr/0001-agent-documentation-structure.md` | Exists | ADR: this documentation structure and protocol |
| `docs/runbooks/` | Planned | Kill switch, broker outage, reconciliation break, restore |
| `docs/threat-model.md` | Planned | Assets, actors, STRIDE, mitigations |

## Code (all Planned)

| Path | Purpose | Agent notes |
|---|---|---|
| `contracts/proto/` | Protobuf schemas: marketdata, orders, ledger, signals, ml, alerts, audit | `contracts/AGENTS.md` |
| `contracts/jsonschema/`, `contracts/openapi/` | Generated schemas for UI and Python; gateway REST API | — |
| `services/api-gateway/` | Auth, sessions, REST/WebSocket, TradingView webhook intake | `AGENTS.md` in dir |
| `services/broker-gateway/` | Alpaca/IBKR adapters, simulator, sessions | `AGENTS.md` in dir |
| `services/marketdata/` | Backfill, streaming, bar building, lake/cache writes | `AGENTS.md` in dir |
| `services/ledger/` | Real + virtual portfolios, lots, reconciliation | `AGENTS.md` in dir |
| `services/execution/` | OMS, pre-trade risk, kill switches, strategy runtime | `AGENTS.md` in dir |
| `services/context-data/` | News, sentiment, macro, earnings, regime | `AGENTS.md` in dir |
| `services/ml/` | Training and inference entrypoints | `AGENTS.md` in dir |
| `services/backtest/` | Vectorized and event-driven backtests, validation | `AGENTS.md` in dir |
| `services/optimizer/` | Studies, holdout discipline, champion/challenger | `AGENTS.md` in dir |
| `services/assistant/` | LLM agent with constrained tools | `AGENTS.md` in dir |
| `services/monitor/` | Drift, divergence, alerts, auto-demotion | `AGENTS.md` in dir |
| `libs/go/` | Shared Go: config, logging, bus client, auth middleware, clock, calendar | `AGENTS.md` in dir |
| `libs/py/tbcore/` | Shared Python: instruments, calendar, features, labels, splitters, cost models, strategy API, metrics | `AGENTS.md` in dir |
| `apps/web/` | Web UI, desktop + mobile PWA | `AGENTS.md` in dir |
| `infra/compose/` | `docker-compose.yml` and profiles | `infra/AGENTS.md` |
| `infra/docker/` | Hardened base images | `infra/AGENTS.md` |
| `infra/terraform/` | Remote server provisioning | `infra/AGENTS.md` |
| `infra/observability/` | Prometheus rules, Grafana dashboards, Loki/Tempo config | `infra/AGENTS.md` |
| `infra/secrets/` | SOPS-encrypted files only | `infra/AGENTS.md` |
| `tests/e2e/` | Full-stack scenarios against the paper broker simulator | — |
| `tests/fixtures/` | Recorded market data and broker messages (no real account data) | — |
| CI config (`.github/workflows/` or equivalent) | Lint, test, leakage, contract, docs-check gates | — |

When a Planned path is created, change its status to Exists (or move it into the matching section) and add any new paths.
