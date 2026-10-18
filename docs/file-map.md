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
| `.dockerignore` | Exists | Keeps `.secrets/`, `data/`, `backups/`, `.git`, caches, and `node_modules` out of every image build context |
| `.gitleaks.toml` | Exists | gitleaks config: default rules; allowlists only test files with fake secret-shaped values and JSON vendor fixtures under `tests/fixtures/` (SE-053) |
| `.trivyignore` | Exists | Documented exceptions to the vulnerability gate (empty) |
| `.golangci.yml` | Exists | golangci-lint v2 config shared by every Go module (standard + gosec, bodyclose, noctx, errorlint; gofmt/goimports) |
| `go.work` | Exists | Go workspace listing every Go module (ADR-0002) |
| `go.work.sum` | Exists | Checksums for workspace-level Go dependencies |
| `pyproject.toml` | Exists | uv workspace root (virtual, not a package): members, dev tools (pytest, ruff, mypy), tool config |
| `uv.lock` | Exists | Python lockfile for the whole uv workspace |
| `package.json` | Exists | pnpm workspace root (pins pnpm via `packageManager`) |
| `pnpm-workspace.yaml` | Exists | pnpm workspace members |
| `pnpm-lock.yaml` | Exists | Node lockfile for the whole pnpm workspace |

## Tooling: `tools/`, `scripts/`

| Path | Status | Purpose |
|---|---|---|
| `tools/versions.env` | Exists | Pinned toolchain versions (Go, Python, Node, pnpm, uv, buf, protoc, protoc plugins, golangci-lint, gitleaks); single source for scripts and CI |
| `tools/tbctl/` | Exists | Go operator CLI (runs in the ops image): `secrets init`, `db init`, `db checksum`, `bus init`, `audit verify`, `audit head` |
| `scripts/bootstrap-tools.sh` | Exists | Installs pinned tools into `.tools/bin` (gitignored); used by `make tools` and CI |
| `scripts/docs_check.py` | Exists | Documentation gate: file-map coverage and same-commit doc rules for a diff (N6) |
| `scripts/test-integration.sh` | Exists | Runs Go tests with a throwaway Postgres (`make test-integration`) |
| `scripts/smoke.sh` | Exists | Smoke test of a running stack (OP-061): readiness, auth gate, headers, ports, e2e flow + leak canary, Prometheus targets, audit chain |
| `scripts/port-check.sh` | Exists | Fails on any non-loopback published port (OP-041, SE-051) |
| `scripts/check-arm64.sh` | Exists | Verifies every pinned image has a linux/arm64 variant (OP-043) |
| `scripts/gen_calendar.py` | Exists | Generates the shared XNYS session table (Go + Python copies) from `exchange_calendars`; `--check` fails on drift (MD-005) |
| `scripts/spike_v4_reader.py` | Exists | Spike V4 native macOS reader for `make spike-v4` |
| `scripts/ibkr-contract.sh` | Exists | Runs the shared Go broker contract suite (BR-001) against the IBKR adapter served over its fake gateway (`make test-ibkr-contract`, CI python job) |
| `scripts/backup-drill.sh` | Exists | Backup → wipe → fresh stack → restore → verify drill (OP-051) |

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
| `docs/project-context.md` | Exists | Owner's confirmed inputs (accounts, data sources, Mac Mini host, remote access); overrides the master prompt's U1–U8 placeholders |
| `docs/prompts/` | Exists | Build prompts: `00-master-prompt.md` (goals, non-negotiables N1–N6, contracts, phases and gates) and section prompts `01`–`15` |
| `docs/research-prompt.md` | Exists | The research brief that produced the research report |
| `docs/research-report.md` | Exists | Deep research report (executive summary, decisions that differ from defaults); index for `docs/research/` |
| `docs/research/` | Exists | Research sections `01`–`10`; `09-decisions.md` resolves D1–D27 and proposes D28–D31 (accepted by the owner) |
| `docs/glossary.md` | Exists | Domain terms |
| `docs/decisions.md` | Exists | Decision register D1–D34: status (Accepted (research) / Accepted), chosen option, ADR or research link |
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
| `docs/business-logic/ui.md` | Exists | `UI-` rules: page inventory, PAPER/LIVE banner, step-up UX, write-only credentials, staleness, CSP-safe frontend |
| `docs/business-logic/storage.md` | Exists | `ST-` rules: lake layout, atomic writes, manifest, idempotent compaction, revisions, reader dedupe, refdata mirrors, retention, cache keys, data-quality checks |
| `docs/business-logic/market-data.md` | Exists | `MD-` rules: bar alignment, calendar, corporate-action availability and cross-source agreement (more with section 05) |
| `docs/verify-early.md` | Exists | Verify-early spike register V1–V9: method, result, evidence, default until resolved |
| `docs/business-logic/brokers-ibkr.md` | Exists | `IB-` rules: IBKR adapter (gateway login and custody, session supervision across daily/weekly resets, orderRef attribution and echo check, status and error-code tables, executions, pacing, spike V1) |
| `docs/business-logic/brokers.md` | Exists | `BR-` rules: broker interface, capabilities, error model, order-state mapping per broker, attribution validation, session lifecycle, resync, rate limits, live gating, market-data fan-out |
| `docs/business-logic/security.md` | Exists | `SE-` rules: policy, step-up, sessions, CSRF, secrets, credential encryption, redaction, audit, DB privileges |
| `docs/adr/README.md` | Exists | ADR process and index |
| `docs/adr/template.md` | Exists | ADR template |
| `docs/adr/0001-agent-documentation-structure.md` | Exists | ADR: this documentation structure and protocol |
| `docs/adr/0002-monorepo-layout-and-toolchain.md` | Exists | ADR (D32): monorepo layout, workspaces, pinned toolchain, docs gate |
| `docs/adr/0003-contracts-protobuf-buf.md` | Exists | ADR (D3): Protobuf contracts, buf, local-plugin codegen, committed generated code |
| `docs/adr/0004-core-language-split.md` | Exists | ADR (D1; IBKR adapter in Python): Go core services, Python ML/research |
| `docs/adr/0005-postgres-layout-and-migrations.md` | Exists | ADR (D5): one `platform` DB, schema per area, `svc_*` roles, owner-role migrations |
| `docs/adr/0006-audit-log-hash-chain.md` | Exists | ADR (D34): audit hash chain computed in Postgres, verified in Go |
| `docs/adr/0007-service-to-service-auth.md` | Exists | ADR (D33): Ed25519 service tokens with per-method allowlists |
| `docs/adr/0009-owner-authentication.md` | Exists | ADR (D15): passkeys + TOTP + recovery codes, setup token, sessions, CSRF, step-up, lockout |
| `docs/adr/0011-secrets-management.md` | Exists | ADR (D16): `/run/secrets` files, SOPS + age at rest, envelope-encrypted broker credentials, KEK rotation |
| `docs/adr/0012-web-ui-spa-sse-pwa.md` | Exists | ADR (D11, D13, D14): static Vite + React SPA, SSE, PWA + Pushover |
| `docs/adr/0013-mac-mini-host-colima-native-ml.md` | Exists | ADR (D21, D25, D28, D29): Mac Mini + Compose on Colima at boot, native launchd ML workers, no GPU profile |
| `docs/adr/0014-observability-stack.md` | Exists | ADR (D22, D13): Prometheus, VictoriaLogs, Alloy, Grafana, alert rules, Pushover |
| `docs/adr/0015-phase0-data-services.md` | Exists | ADR (D4, D6, D8, D10): lake on host APFS, Valkey 9, MLflow with filesystem artifacts, Prefect 3 |
| `docs/adr/0016-remote-access.md` | Exists | ADR (D17): host `tailscaled` + `tailscale serve`, optional Cloudflare Tunnel + Access |
| `docs/adr/0017-ci-and-supply-chain.md` | Exists | ADR (D35): GitHub Actions gates, SHA pinning, gitleaks, Trivy, SBOM, multi-arch, Dependabot |
| `docs/adr/0010-account-routing-and-day-trade-policy.md` | Exists | ADR (D30): horizon-based account routing, PDT guard off by default, intraday-margin guard, halt on margin events |
| `docs/adr/0020-lake-layout-write-path-and-query-layer.md` | Exists | ADR (D38): lake layout, single-writer path, revisions, manifest, refdata mirrors, calendar, Polars query layer, read-time adjustment |
| `docs/adr/0021-feature-store.md` | Exists | ADR (D39): thin feature store, single compute path, finite lookback, scale invariance, snapshots |
| `docs/adr/0022-market-data-ingestion.md` | Exists | ADR (D40): Alpaca via broker-gateway, finality window, resumable backfill, FMP `/stable`, reconciliation and quarantine |
| `docs/adr/0019-alpaca-adapter-and-simulator.md` | Exists | ADR (D37): hand-written Alpaca adapter, in-process simulator, single market-data socket, credential-free test fake |
| `docs/adr/0023-ibkr-adapter-gateway-and-sessions.md` | Exists | ADR (D41, implements D18): pinned IB Gateway, per-environment `ib_async` adapter, credential custody, network isolation, maintenance-aware supervision, V1 interim |
| `docs/adr/0018-broker-gateway-interface.md` | Exists | ADR (D36): one gRPC broker interface for every adapter, typed error model, canonical execution IDs, outbox-backed events |
| `docs/adr/0008-event-bus-nats-jetstream.md` | Exists | ADR (D2): NATS JetStream, `sync_interval: always`, stream layout, outbox |
| `docs/runbooks/` | Exists | Mac Mini host, backup/restore, service down, clock skew, disk full, secrets and keys, security incidents, IBKR weekly 2FA / manual VNC login / gateway outages (`ibkr-weekly-2fa.md`), broker outage, market data (stale, slow, gaps, reconciliation breaches, corporate-action disputes) (later: kill switch, ledger reconciliation break) |
| `docs/threat-model.md` | Exists | Assets, actors, entry points, STRIDE table mapped to SE-/OP- rules, residual risks |

## Contracts: `contracts/`

Internal layout and conventions: [`contracts/AGENTS.md`](../contracts/AGENTS.md).

| Path | Status | Purpose |
|---|---|---|
| `contracts/AGENTS.md` | Exists | Agent notes: layout, conventions, regeneration, gotchas |
| `contracts/buf.yaml` | Exists | buf module, lint (STANDARD) and breaking (FILE) config |
| `contracts/buf.gen.yaml` | Exists | Local-plugin codegen to Go, Python, TypeScript |
| `contracts/proto/` | Exists | Protobuf schemas `tb.<domain>.v1`: common, marketdata, context, features, signals, orders, ledger, ml, alerts, audit, broker (credentials RPC, `BrokerGatewayService`, `BrokerMarketDataService`) |
| `contracts/gen/go/` | Exists | Generated Go module (messages + gRPC) plus hand-written `go.mod` and `contracts_test.go` |
| `contracts/gen/python/` | Exists | Generated Python package `tbcontracts` (`tb.*_pb2` + `.pyi`) plus hand-written `pyproject.toml` |
| `contracts/gen/ts/` | Exists | Generated TypeScript package `@strawberry/contracts` plus hand-written `package.json`, `tsconfig.json`, `test/` |
| `contracts/tests/` | Exists | Python contract round-trip tests |
| `contracts/jsonschema/` | Planned | JSON Schemas generated from proto for gateway/UI validation (Phase 1) |
| `contracts/cache/` | Exists | `README.md`: hot-cache key layout, value types, TTLs (ST-009) |
| `contracts/openapi/` | Exists | `gateway.yaml`: gateway REST + SSE API (OpenAPI 3.1), including the public allowlist |

## Shared libraries: `libs/`

| Path | Status | Purpose |
|---|---|---|
| `libs/go/` | Exists | Go module: config, logging/redaction, secrets, ops, telemetry, svc template, svcauth, policy, audit, db (+ embedded migrations), bus, clock, resilience (backoff, priority rate limiter, circuit breaker), outbox (transactional outbox + relay), calendar (embedded XNYS sessions), cache (Valkey key layout), webauthntest (software passkey authenticator for tests). Internal layout in [`libs/go/AGENTS.md`](../libs/go/AGENTS.md) |
| `libs/py/tbcore/` | Exists | Python package: service template, JSON logging with redaction, secrets, lake query layer (`tbcore.data`: point-in-time bars, adjustments, as-of joins, universes, calendar), data quality (`tbcore.dq`), feature store (`tbcore.features`) (later: labels, strategy API). Layout in [`libs/py/tbcore/AGENTS.md`](../libs/py/tbcore/AGENTS.md) |

## Services: `services/`

| Path | Status | Purpose |
|---|---|---|
| `services/api-gateway/` | Exists | Go: owner auth (passkeys, TOTP, recovery), sessions, CSRF, step-up, settings, audit API, credential write-through, SSE status stream, static UI. Layout in [`services/api-gateway/AGENTS.md`](../services/api-gateway/AGENTS.md) |
| `services/broker-gateway/` | Exists | Go: broker-credential custody (envelope encryption, KEK ring, write-only gRPC `CredentialService`); `BrokerGatewayService` + `BrokerMarketDataService` with the Alpaca adapter, simulator, session engine, outbox-backed events, market-data hub, and the shared contract suite. Layout in [`services/broker-gateway/AGENTS.md`](../services/broker-gateway/AGENTS.md) |
| `services/marketdata/` | Exists | Go: single lake writer (Parquet staging/compaction/revisions, manifest), reference data (`refdata`) and its daily sync, data-quality checks, resumable backfill, live bar pipeline (finality, resampling, gap repair, bus/cache/lake sinks), FMP client, cross-source reconciliation, scheduler, `MarketDataService`, spike harnesses (V2, V3, V4). [`AGENTS.md`](../services/marketdata/AGENTS.md) |
| `services/broker-gateway-ibkr/` | Exists | Python (`ib_async`): IBKR adapter serving `BrokerGatewayService` per environment; session supervision across IB Gateway daily/weekly resets, orders with attribution in `orderRef`, executions + commissions, snapshots, pacing; fake gateways, recorded-format fixtures, opt-in paper suite. Layout in [`services/broker-gateway-ibkr/AGENTS.md`](../services/broker-gateway-ibkr/AGENTS.md) |
| `services/execution/` | Exists (placeholder) | Go: ops surface + NATS + clock-skew check; `tb_trading_enabled=0` until Phase 2. [`AGENTS.md`](../services/execution/AGENTS.md) |
| `services/ledger/` | Exists (placeholder) | Go: ops surface + NATS; ledgers in Phase 2. [`AGENTS.md`](../services/ledger/AGENTS.md) |
| `services/context-data/` | Exists (placeholder) | Python on `tbcore`: ops surface; context data in Phase 3 |
| `services/monitor/` | Exists (placeholder) | Python on `tbcore`: ops surface; monitoring in Phases 2/5 |
| `services/assistant/` | Exists (placeholder) | Python on `tbcore`: ops surface; assistant in Phase 4 |
| `services/ml/` | Exists (placeholder) | Python on `tbcore`: ops surface; native launchd worker on macOS (D29), Phase 4 |
| `services/backtest/` | Exists (placeholder) | Python on `tbcore`: ops surface; backtester in Phase 3 |
| `services/optimizer/` | Exists (placeholder) | Python on `tbcore`: ops surface; optimizer in Phase 4 |

## Apps: `apps/`

| Path | Status | Purpose |
|---|---|---|
| `apps/web/` | Exists | Vite + React 19 SPA: login/setup, dashboard, security, broker connections, platform settings, audit log; step-up dialog; SSE status. Layout in [`apps/web/AGENTS.md`](../apps/web/AGENTS.md) |

## CI: `.github/`

| Path | Status | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | Exists | CI: docs gate, contracts freshness + buf, Go lint/tests with Postgres, Python, web, gitleaks + Trivy fs, multi-arch image builds with Trivy scan and SBOM, arm64 check, stack smoke test (bootstrap, smoke, alert, backup drill, browser e2e) |
| `.github/dependabot.yml` | Exists | Weekly update PRs for Actions, Go modules, uv, npm, Dockerfiles, Compose images |

## Tests: `tests/`

| Path | Status | Purpose |
|---|---|---|
| `tests/python/` | Exists | Cross-package Python tests (placeholder services' ops surface) |
| `tests/e2e/` | Exists | Go end-to-end test against the running stack: passkey setup/login, step-up, credential storage with leak canary, SSE, audit (`make e2e`, used by the smoke test) |
| `tests/fixtures/` | Exists | Fixtures (no account data): `alpaca/` (bars, corporate actions, data-stream messages; README explains provenance), `lake/` (Go-written sample lake with a revision and a staged file, read by Python tests), `dq/` (injected-defect bars + expected report shared by Go and Python), `fmp/` (`/stable` response shapes for the FMP client tests) |

## Infra: `infra/` (agent notes in `infra/AGENTS.md`)

| Path | Status | Purpose |
|---|---|---|
| `infra/AGENTS.md` | Exists | Agent notes: layout, rules, run/test, configuration, gotchas |
| `infra/compose/` | Exists | `docker-compose.yml` (profiles core, observability, ml, ml-workers, ops, remote-cloudflare, ibkr, ibkr-live, ibkr-vnc), native-ML overlay, `nats/nats.conf`, `env.example` |
| `infra/docker/` | Exists | Go (service/gateway targets), Python (incl. `broker-gateway-ibkr`), ops, and MLflow images; MLflow entrypoint |
| `infra/ops/` | Exists | `backup.sh`, `restore.sh`, `secrets-bundle.sh` (run in the ops image) |
| `infra/observability/` | Exists | Prometheus scrape config and alert rules (incl. `rules/ibkr.yml`), Alertmanager routing, Alloy config, Grafana provisioning and the "Stack overview" and "Market data and brokers" dashboards; market-data/broker alert rules (`prometheus/rules/marketdata-brokers.yml`) |
| `infra/macos/` | Exists | LaunchDaemon templates (Colima at boot, stack auto-start, native ML worker), `install.sh`, `start-stack.sh` |
| `infra/secrets/` | Exists | SOPS-encrypted bundles only (`.gitkeep` until the owner runs `make secrets-encrypt`) |
| `infra/terraform/` | Planned | Remote server provisioning (Phase 6) |

## Planned code

None outside the sections above (Planned rows live in their section).

When a Planned path is created, change its status to Exists (or move it into the matching section) and add any new paths.
