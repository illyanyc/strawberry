# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Every behavioral or structural change adds an entry under `[Unreleased]` in the same commit, citing business-rule IDs and ADRs where relevant. Moves and renames list `old → new` paths.

## [Unreleased]

### Added

- Agent-facing structure: root `AGENTS.md`, `.cursor/rules/` (always-applied core, documentation-discipline, and trading-safety rules; scoped rules for backend core, Python ML, frontend, infra).
- Docs skeleton: `docs/architecture/overview.md`, `docs/architecture/service-agents.md` (per-service `AGENTS.md` convention and template), `docs/file-map.md`, `docs/glossary.md`, `docs/decisions.md` (D1–D27 register, all Pending).
- Business-logic docs with initial specified rules: orders and execution (`OE-`), virtual portfolios and attribution (`VP-`), risk management (`RM-`), strategies and model lifecycle (`SM-`), data and features (`DF-`), plus a template.
- ADR process, template, and [ADR-0001](docs/adr/0001-agent-documentation-structure.md) (documentation structure and protocol).
- Project `README.md`.

### Removed

- `about.txt` (one-line placeholder description; superseded by `README.md`).
