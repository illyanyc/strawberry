# ADR-0005: PostgreSQL layout, least-privilege roles, and migrations

- **Status:** Accepted (provisional: default, pending research §7, §10)
- **Date:** 2026-10-17
- **Decision register:** D5
- **Related:** rules SE-032, SE-050; section 01 Stage A; [ADR-0006](0006-audit-log-hash-chain.md)

## Context

Transactional state (users, sessions, audit, credentials, later orders and ledgers) needs ACID guarantees. Section 01 asks for separate databases/roles per service with least privilege; section 02 requires that only `broker-gateway` can read credentials and that audit writers cannot modify history. The audit log is written by every service, which rules out one database per service (cross-database writes are awkward and break the single chain).

## Decision

- **PostgreSQL 17** (`postgres:17-alpine`, multi-arch). One database `platform` for service state, plus separate databases `mlflow` and `prefect` owned by their own roles.
- In `platform`, **one schema per owning area** (`audit`, `auth`, `config`, `secrets`; later `orders`, `ledger`, ...), each owned by a NOLOGIN `<schema>_owner` role. `PUBLIC` loses `CONNECT` on the database and `CREATE` on `public`.
- Services log in as `svc_<service>` (password from secret `pg_<role>_password`) and receive only explicit grants from migrations. Group roles: `audit_writer` (EXECUTE `audit.append`), `audit_reader` (SELECT).
- Migrations: SQL files embedded in `libs/go/db/migrations/<schema>/`, applied with `golang-migrate` (pgx v5 driver) **as the schema owner** (superuser connection + `SET ROLE`), one `schema_migrations` table per schema. `tbctl db init` (one-shot `db-init` container, `make migrate`) creates roles, databases, schemas, then migrates. Idempotent.
- Python services that need tables later use Alembic with the same owner-role pattern.

## Alternatives considered

| Option | Why not |
|---|---|
| One database per service | Cross-service audit chain impossible without a separate audit service; more connections and backups. |
| One shared role | Violates least privilege; any compromised service could read credentials. |
| Services migrate themselves at startup | Services would need DDL rights (owner role) at runtime. |
| Atlas | Fine tool; golang-migrate is simpler and embeds cleanly. |

## Consequences

- Positive: database-enforced isolation (tested: `TestSE032SchemaIsolation`), one backup unit, one audit chain.
- Negative / risks: all services share one Postgres instance (a noisy neighbor can hurt the order path); acceptable on one host, revisit with connection pooling/limits in Phase 2.
- Follow-up: each new schema adds a `db.Schemas` entry, migrations, role grants, and an SE-/domain rule.

## Validation

`TestSE032SchemaIsolation` and `TestSE020AuditChainEndToEnd` run against real Postgres in CI.
