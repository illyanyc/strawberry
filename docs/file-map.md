# File map

Every tracked file and directory, with its purpose. **Agents must update this in the same commit as any add, move, rename, or delete** (see [`.cursor/rules/documentation-discipline.mdc`](../.cursor/rules/documentation-discipline.mdc)). Service internals are listed in each service's `AGENTS.md`; this map lists the service directory and links there.

`make docs-check` enforces this map: every git-tracked file must be covered by an entry here (an exact path, or a directory entry ending in `/` that covers everything below it), and no `Planned` row may point at a path that exists. Always write full repo-relative paths in the first column.

Status: **Exists** = in the repo now. **Planned** = target layout from the build plan; create it where shown.

## Root

| Path | Status | Purpose |
|---|---|---|
| `AGENTS.md` | Exists | Agent entry point: system map, where things live, run/test, documentation protocol |
| `README.md` | Exists | Short project intro and quickstart for humans; points to `AGENTS.md` and `docs/` |
| `CHANGELOG.md` | Exists | Keep a Changelog; every behavioral or structural change gets an `[Unreleased]` entry |
| `Makefile` | Exists | Developer entry points (`make help`): tools, docs-check (more targets land with each Phase 0 section) |
| `.gitignore` | Exists | Ignores local tooling (`.tools/`), runtime secrets (`.secrets/`), backups, build output, env files |
| `.gitattributes` | Exists | LF line endings; marks generated code and lockfiles as generated |
| `.editorconfig` | Exists | Editor whitespace conventions per language |

## Tooling: `tools/`, `scripts/`

| Path | Status | Purpose |
|---|---|---|
| `tools/versions.env` | Exists | Pinned toolchain versions (Go, Python, Node, pnpm, uv, buf, protoc, protoc plugins, golangci-lint, gitleaks); single source for scripts and CI |
| `scripts/bootstrap-tools.sh` | Exists | Installs pinned tools into `.tools/bin` (gitignored); used by `make tools` and CI |
| `scripts/docs_check.py` | Exists | Documentation gate: file-map coverage and same-commit doc rules for a diff (N6) |

## Agent rules: `.cursor/rules/`

| Path | Status | Applies | Purpose |
|---|---|---|---|
| `.cursor/rules/core.mdc` | Exists | Always | Project context, decision handling, code conventions, scope |
| `.cursor/rules/documentation-discipline.mdc` | Exists | Always | Mandatory doc protocol and done checklist |
| `.cursor/rules/trading-safety.mdc` | Exists | Always | Non-negotiable trading and security rules |
| `.cursor/rules/backend-core.mdc` | Exists | Core services, `libs/go/`, `contracts/` | Order path, contracts, service conventions |
| `.cursor/rules/python-ml.mdc` | Exists | Python services, `libs/py/`, `*.py` | Shared feature library, leakage, reproducibility |
| `.cursor/rules/frontend.mdc` | Exists | `apps/web/` | UI security, trading UX, quality states |
| `.cursor/rules/infra.mdc` | Exists | `infra/`, Docker, Compose, CI, Terraform | Network, secrets, containers |

## Docs: `docs/`

| Path | Status | Purpose |
|---|---|---|
| `docs/file-map.md` | Exists | This file |
| `docs/glossary.md` | Exists | Domain terms |
| `docs/decisions.md` | Exists | Decision register (D1–D27 plus later rows): status, default, ADR link |
| `docs/architecture/overview.md` | Exists | Components, data flows, the two loops, cross-cutting design, bus subjects |
| `docs/architecture/service-agents.md` | Exists | Convention and template for per-service `AGENTS.md` |
| `docs/business-logic/README.md` | Exists | Index of domain docs, rule-ID scheme, how to write rules |
| `docs/business-logic/_template.md` | Exists | Template for a domain business-logic doc |
| `docs/business-logic/orders-and-execution.md` | Exists | `OE-` rules: intents, OMS states, submission, idempotency |
| `docs/business-logic/virtual-portfolios-and-attribution.md` | Exists | `VP-` rules: ownership, attribution IDs, lots, reconciliation |
| `docs/business-logic/risk-management.md` | Exists | `RM-` rules: pre-trade checks, limits, kill switches, PDT |
| `docs/business-logic/strategies-and-model-lifecycle.md` | Exists | `SM-` rules: modes, promotion, demotion, holdout, reproducibility |
| `docs/business-logic/data-and-features.md` | Exists | `DF-` rules: timestamps, point-in-time, adjustments, feature parity |
| `docs/business-logic/operations.md` | Exists | `OP-` rules: build, run, deploy, backup/restore, host and toolchain behavior |
| `docs/adr/README.md` | Exists | ADR process and index |
| `docs/adr/template.md` | Exists | ADR template |
| `docs/adr/0001-agent-documentation-structure.md` | Exists | ADR: this documentation structure and protocol |
| `docs/adr/0002-monorepo-layout-and-toolchain.md` | Exists | ADR: monorepo layout, workspaces, pinned toolchain, docs gate |
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
| `.github/workflows/` | CI: lint, test, leakage, contract, docs-check gates | — |

When a Planned path is created, change its status to Exists (or move it into the matching section) and add any new paths.
