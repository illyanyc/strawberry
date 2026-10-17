# Operations

**Prefix:** `OP-`  ·  **Owners:** `Makefile`, `scripts/`, `infra/`, CI (`.github/workflows/`)  ·  **Related:** [ADR-0002](../adr/0002-monorepo-layout-and-toolchain.md), [security.md](security.md) (planned)

## Scope

How the platform is built, checked, run, backed up, and restored on the owner's host (a Mac Mini running Docker; later a remote server). Covers toolchain pinning, the documentation gate, the local stack, networking exposure, backups, and host survival (reboot, power, network blips). Authentication, secrets design, and audit are in `security.md`. Trading-time behavior (freeze before restart) is in [risk-management.md](risk-management.md) and [orders-and-execution.md](orders-and-execution.md).

## Concepts and states

- **Pinned toolchain**: every tool version lives in `tools/versions.env`; `make tools` installs them into `.tools/bin` (never system-wide).
- **Docs gate**: `scripts/docs_check.py` (`make docs-check`), run in CI on every PR.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| OP-001 | Toolchain versions MUST be pinned in `tools/versions.env`; scripts and CI MUST read versions from there rather than hardcoding them. | Implemented | `tools/versions.env`, `scripts/bootstrap-tools.sh` |
| OP-002 | A change set that adds, deletes, or renames a tracked file MUST also change `docs/file-map.md` and `CHANGELOG.md`, or CI fails. | Implemented | `scripts/docs_check.py` (`diff`) |
| OP-003 | A change set that touches `contracts/` MUST also change `docs/architecture/overview.md` or add an ADR, or CI fails. | Implemented | `scripts/docs_check.py` (`diff`) |
| OP-004 | A change set that adds a file under `docs/adr/` MUST also change `docs/decisions.md`, or CI fails. | Implemented | `scripts/docs_check.py` (`diff`) |
| OP-005 | Every git-tracked file MUST be covered by an entry in `docs/file-map.md` (exact path or a covering directory entry); a `Planned` row MUST NOT point at an existing path. | Implemented | `scripts/docs_check.py` (`coverage`) |
| OP-006 | The OP-002..OP-004 diff checks MAY be bypassed only through the reviewed PR label `docs-check-skip` (sets `DOCS_CHECK_SKIP=1`); coverage (OP-005) is never skipped. | Implemented | `scripts/docs_check.py` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| OP-I1 | `docs/file-map.md` covers every tracked path. | `make docs-check` in CI |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| A PR only edits existing files' contents | Diff check passes without file-map changes; changelog is still required by review for behavior changes. | OP-002 |
| A PR renames `a.go` to `b.go` | Counts as structural; file map and changelog must change. | OP-002 |
| A new ADR without a decisions-register edit | CI fails. | OP-004 |

## Open questions

None yet.
