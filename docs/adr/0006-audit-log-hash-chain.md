# ADR-0006: Tamper-evident audit log as a Postgres hash chain

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D34
- **Related:** master N1; section 02 "Audit log"; rules SE-020..SE-025; [ADR-0005](0005-postgres-layout-and-migrations.md)

## Context

N1 requires an append-only, tamper-evident audit log of every order, risk decision, config change, login, and promotion, with a verifier (`make audit-verify`). Writers include several services; ordering must be total.

## Decision

- Table `audit.events` with a gapless `seq`, the `AuditEvent` contract fields, `prev_hash`, and `hash`.
- The chain is computed **inside Postgres** by `audit.append(...)` (`SECURITY DEFINER`, owned by `audit_owner`), serialized with a transaction-scoped advisory lock. Writers hold only `EXECUTE` on it; triggers reject `UPDATE`, `DELETE`, and `TRUNCATE` even for the owner.
- Canonical encoding: length-prefixed fields (`"<bytes>:" value ";"`) in a fixed order with a version tag `tb.audit.v1`; `hash_n = SHA-256(hash_{n-1} || canonical)`. Details are canonical JSON (sorted keys) stored as text, redacted before storage.
- `libs/go/audit.Verify` recomputes the chain independently in Go (`tbctl audit verify` / `make audit-verify`).
- The audit head (seq, hash) is written into every backup manifest (SE-024), so a rewritten history no longer matches the off-host copy.

## Alternatives considered

| Option | Why not |
|---|---|
| Hash computed by each writer in Go | Writers would need SELECT on the head and INSERT on the table; concurrent writers race; history rewrite needs only table access. |
| Separate audit service over the bus | Extra moving part; durability then depends on the bus; can be added later behind the same function. |
| External ledger (QLDB, transparency log) | Cloud dependency; overkill for one owner. Periodic anchoring to off-host backups gives most of the value. |
| JSONB canonicalization | jsonb text output is normalized by Postgres; reproducing it byte-for-byte in Go is fragile. |

## Consequences

- Positive: writers cannot rewrite history; a superuser can, but verification then fails at the first altered row.
- Negative / risks: the advisory lock serializes all audit writes (fine at this volume; revisit if order flow makes it a bottleneck).
- Follow-up: anchoring to off-host storage in Stage B; order-path events in Phase 2.

## Validation

`TestSE020AuditChainEndToEnd` (append, verify, direct-write denial, superuser tamper → verify fails at the right seq); CI smoke test runs `make audit-verify`.
