# ADR-0002: Monorepo layout, workspaces, and pinned toolchain

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D28
- **Related:** master prompt "Repository layout", section 01 Stage A, [ADR-0001](0001-agent-documentation-structure.md), rules OP-001..OP-006

## Context

The platform mixes Go (core services, D1 default), Python (ML and research), and TypeScript (web UI). One owner operates it, often through coding agents that start without context. Builds must be reproducible (N5) and the docs must track the repo (N6). The host is a Mac Mini (Apple Silicon); CI runs on Linux x86_64, so tooling must work on both.

## Decision

- Single monorepo with the master prompt's layout: `contracts/`, `services/`, `libs/go/`, `libs/py/tbcore/`, `apps/web/`, `infra/`, `tests/`, `docs/`, plus `tools/` (pins, ops CLI) and `scripts/` (repo automation).
- Workspaces: Go `go.work` (one module per service, `libs/go`, generated contracts, `tools/tbctl`; each module also carries `replace` directives so `go mod tidy` works outside the workspace); Python `uv` workspace with one `uv.lock`; TypeScript `pnpm` workspace with one `pnpm-lock.yaml`.
- All tool versions pinned in `tools/versions.env`; `make tools` installs them into `.tools/bin`, never system-wide. CI uses the same script.
- `make` is the single entry point (`make help`).
- A stdlib-only Python script (`scripts/docs_check.py`) enforces the documentation protocol in CI.

## Alternatives considered

| Option | Why not |
|---|---|
| `justfile` instead of `make` | `make` ships with macOS and every CI image; no extra install. |
| Bazel / Pants / Nx | Powerful but heavy for a team of one; slows agents down. Revisit if build times hurt. |
| One Go module for everything | Simpler, but the master prompt asks for `go.work`, and per-service modules keep dependency graphs small (the gateway's WebAuthn deps never reach the broker path). |
| Tools via Homebrew / apt | Not pinned, differs between the Mac host and CI. |

## Consequences

- Positive: reproducible tool versions on Mac and Linux; small per-service dependency sets; one command surface.
- Negative / risks: `go.work` plus `replace` directives must be kept in sync when modules are added; Docker builds need the repo root as context.
- Follow-up: each new module is added to `go.work`, the uv workspace, or the pnpm workspace in the same commit, with a file-map entry.

## Validation

`make tools && make docs-check` succeeds on a clean clone on macOS arm64 and Linux x86_64; CI runs the same commands.
