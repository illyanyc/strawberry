# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Every behavioral or structural change adds an entry under `[Unreleased]` in the same commit, citing business-rule IDs and ADRs where relevant. Moves and renames list `old → new` paths.

## [Unreleased]

### Added

- Agent-facing structure: root `AGENTS.md`, `.cursor/rules/` (always-applied core, documentation-discipline, and trading-safety rules; scoped rules for backend core, Python ML, frontend, infra).
- Docs skeleton: `docs/architecture/overview.md`, `docs/architecture/service-agents.md` (per-service `AGENTS.md` convention and template), `docs/file-map.md`, `docs/glossary.md`, `docs/decisions.md` (D1–D27 register, all Pending).
- Business-logic docs with initial specified rules: orders and execution (`OE-`), virtual portfolios and attribution (`VP-`), risk management (`RM-`), strategies and model lifecycle (`SM-`), data and features (`DF-`), plus a template.
- ADR process, template, and [ADR-0001](docs/adr/0001-agent-documentation-structure.md) (documentation structure and protocol).
- Project `README.md`.
- Repo tooling: `Makefile` (`make help`, `tools`, `docs-check`), pinned toolchain in `tools/versions.env`, `scripts/bootstrap-tools.sh` (installs buf, protoc, protoc plugins, golangci-lint, gitleaks into `.tools/bin`), `.gitignore`, `.gitattributes`, `.editorconfig` ([ADR-0002](docs/adr/0002-monorepo-layout-and-toolchain.md), D32, OP-001).
- Documentation gate `scripts/docs_check.py`: file-map coverage and same-commit doc rules (OP-002..OP-006).
- `docs/business-logic/operations.md` (`OP-` rules).
- Contracts (`contracts/`): Protobuf packages `tb.common/marketdata/context/features/signals/orders/ledger/ml/alerts/audit/broker.v1` with the master prompt's minimum fields; `buf` lint/breaking; local-plugin codegen to Go (`contracts/gen/go`), Python (`tbcontracts`), TypeScript (`@strawberry/contracts`); round-trip tests in all three languages ([ADR-0003](docs/adr/0003-contracts-protobuf-buf.md), D3 provisional).
- `make deps`, `make gen`, `make gen-check` (fails on stale generated code), `make lint-proto`, `make lint`, `make test` targets.
- Workspaces: `go.work`, uv workspace (`pyproject.toml`, `uv.lock`), pnpm workspace (`package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`).
- `docs/decisions.md` gains a **Provisional** status for defaults adopted ahead of the research report.
- Shared Go library `libs/go`: typed config, JSON logging with secret redaction (SE-012), file-mounted secrets (SE-010), ops listener with `/healthz` `/readyz` `/metrics` and HTTP metrics (OP-010), OpenTelemetry tracing, service template, Ed25519 service tokens with gRPC interceptors (SE-040, [ADR-0007](docs/adr/0007-service-to-service-auth.md)), the single authorization policy with step-up rules (SE-001..SE-003, SE-014), audit writer and chain verifier (SE-020, SE-022, [ADR-0006](docs/adr/0006-audit-log-hash-chain.md)), Postgres bootstrap and embedded migrations for schemas `audit`, `auth`, `config`, `secrets` with least-privilege roles (SE-032, SE-050, [ADR-0005](docs/adr/0005-postgres-layout-and-migrations.md)), JetStream stream definitions (OP-030, [ADR-0008](docs/adr/0008-event-bus-nats-jetstream.md)), NTP clock-skew checker (OP-020).
- Shared Python library `libs/py/tbcore`: service settings, ops app, JSON logging with redaction, secrets.
- `docs/business-logic/security.md` (`SE-` rules); ADR-0004 (D1), ADR-0005 (D5), ADR-0008 (D2) provisional; new decisions D33 (service auth) and D34 (audit design).
- Imported the build prompts (`docs/prompts/`), owner inputs (`docs/project-context.md`), research brief, and the research report (`docs/research-report.md`, `docs/research/`) so decisions and ADRs can cite them from the repo.
- `services/api-gateway` (Go): first-run setup with a one-time token (`setup-token` subcommand), passkey registration and discoverable login (go-webauthn), TOTP enrollment and fallback login with replay protection, 10 single-use recovery codes, reduced-assurance TOTP/recovery sessions, server-side sessions (`__Host-` cookies, idle/absolute timeouts, list/revoke), CSRF origin + double-submit checks, passkey/any-factor step-up via `policy.Authorize`, per-IP rate limiting and account/IP lockout with `alert.security`, strict security headers, closed settings list with step-up for dangerous enables (settings write and audit in one transaction), audit list/verify API, write-only broker-credential endpoints proxied to broker-gateway over gRPC with service tokens, SSE `trading-status` stream, SPA file server (SE-003..SE-014, SE-021, SE-023, SE-025, SE-041; [ADR-0009](docs/adr/0009-owner-authentication.md), D15).
- Tests: route inventory (SE-004), CSRF and headers, and an end-to-end Postgres test driving real WebAuthn ceremonies through a software authenticator.
- `services/broker-gateway` (Go): envelope-encrypted broker credentials (per-record AES-256-GCM data key, AAD bound to account + environment, KEK ring with `rewrap-keys` rotation), write-only gRPC `CredentialService` restricted to `api-gateway` by service token, credential writes audited in the same transaction, clock-skew metric (SE-030..SE-033, SE-040; [ADR-0011](docs/adr/0011-secrets-management.md), D16). Tests cover the paper→live relabel attack, KEK rotation, caller allowlist, and secret redaction on the error path.
- `apps/web`: static Vite + React 19 SPA (TanStack Router/Query, Tailwind 4, shadcn/ui-style primitives) with first-run setup, passkey/TOTP/recovery login, dashboard, security settings (passkeys, authenticator app with QR, recovery codes, sessions), write-only broker credential forms, platform settings with step-up, audit log viewer with chain verification, global step-up dialog, SSE trading-status bar with staleness indicator, PAPER/LIVE banner, PWA manifest; unit tests for the API client and banner (UI-001..UI-008; [ADR-0012](docs/adr/0012-web-ui-spa-sse-pwa.md), D11/D13/D14). New business-logic domain `docs/business-logic/ui.md`.
- `contracts/openapi/gateway.yaml` (REST + SSE API); `audit.AppendWith` for transactional audit appends.
- `.golangci.yml` shared lint config; `make test-integration` (`scripts/test-integration.sh`) runs Postgres-backed tests in a throwaway container.

### Changed

- Risk rules (D30, [ADR-0010](docs/adr/0010-account-routing-and-day-trade-policy.md)): RM-008 PDT guard becomes a per-account `off|legacy` setting, **off by default** (PDT rule retired 2026-06-04), replacing the master prompt's U1 placeholder; new RM-011 horizon-based account routing (intraday → Alpaca, swing → IBKR bot account), RM-012 intraday-margin guard, RM-013 halt on IMD / margin call. Glossary: PDT, intraday-margin guard, routing table.
- Decision register: every research decision D1–D31 is now **Accepted (research)** with its chosen option and research link (`docs/decisions.md`). Provisional ADR-0003 (D3), ADR-0004 (D1: IBKR adapter becomes a Python service), ADR-0005 (D5), and ADR-0008 (D2: `sync_interval: always`, Postgres outbox) gain research-outcome sections; ADR-0007 (D33) is accepted. `AGENTS.md` system map, `.cursor/rules/` (backend, frontend, python-ml, infra), the architecture overview, and VP model text now state the accepted stack (Vite SPA, SSE, Colima on the Mac Mini, native ML workers, VictoriaLogs + Alloy, no MinIO, Valkey 9).
- Repo-local decision IDs renumbered to avoid the research report's new D28–D31: monorepo/toolchain D28 → D32, service-to-service auth D29 → D33, audit log design D30 → D34 (ADR-0002, ADR-0007, ADR-0006).
- `docs/file-map.md` now uses full repo-relative paths in every row so `make docs-check` can verify coverage.

### Removed

- `about.txt` (one-line placeholder description; superseded by `README.md`).
