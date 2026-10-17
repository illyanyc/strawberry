# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Every behavioral or structural change adds an entry under `[Unreleased]` in the same commit, citing business-rule IDs and ADRs where relevant. Moves and renames list `old → new` paths.

## [Unreleased]

### Added

- Agent-facing structure: root `AGENTS.md`, `.cursor/rules/` (always-applied core, documentation-discipline, and trading-safety rules; scoped rules for backend core, Python ML, frontend, infra).
- Docs skeleton: `docs/architecture/overview.md`, `docs/architecture/service-agents.md` (per-service `AGENTS.md` convention and template), `docs/file-map.md`, `docs/glossary.md`, `docs/decisions.md` (D1–D27 register, all Pending).
- Business-logic docs with initial specified rules: orders and execution (`OE-`), virtual portfolios and attribution (`VP-`), risk management (`RM-`), strategies and model lifecycle (`SM-`), data and features (`DF-`), plus a template.
- ADR process, template, and [ADR-0001](docs/adr/0001-agent-documentation-structure.md) (documentation structure and protocol).
- Project `README.md`.
- Repo tooling: `Makefile` (`make help`, `tools`, `docs-check`), pinned toolchain in `tools/versions.env`, `scripts/bootstrap-tools.sh` (installs buf, protoc, protoc plugins, golangci-lint, gitleaks into `.tools/bin`), `.gitignore`, `.gitattributes`, `.editorconfig` ([ADR-0002](docs/adr/0002-monorepo-layout-and-toolchain.md), D28, OP-001).
- Documentation gate `scripts/docs_check.py`: file-map coverage and same-commit doc rules (OP-002..OP-006).
- `docs/business-logic/operations.md` (`OP-` rules).
- Contracts (`contracts/`): Protobuf packages `tb.common/marketdata/context/features/signals/orders/ledger/ml/alerts/audit/broker.v1` with the master prompt's minimum fields; `buf` lint/breaking; local-plugin codegen to Go (`contracts/gen/go`), Python (`tbcontracts`), TypeScript (`@strawberry/contracts`); round-trip tests in all three languages ([ADR-0003](docs/adr/0003-contracts-protobuf-buf.md), D3 provisional).
- `make deps`, `make gen`, `make gen-check` (fails on stale generated code), `make lint-proto`, `make lint`, `make test` targets.
- Workspaces: `go.work`, uv workspace (`pyproject.toml`, `uv.lock`), pnpm workspace (`package.json`, `pnpm-workspace.yaml`, `pnpm-lock.yaml`).
- `docs/decisions.md` gains a **Provisional** status for defaults adopted ahead of the research report.

### Changed

- `docs/file-map.md` now uses full repo-relative paths in every row so `make docs-check` can verify coverage.

### Removed

- `about.txt` (one-line placeholder description; superseded by `README.md`).
