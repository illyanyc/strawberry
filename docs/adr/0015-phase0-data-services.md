# ADR-0015: Phase 0 data services: lake on host APFS, Valkey 9, MLflow with filesystem artifacts, Prefect 3

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D4, D6, D8, D10
- **Related:** research [09 §D4](../research/09-decisions.md#d4), [§D6](../research/09-decisions.md#d6), [§D8](../research/09-decisions.md#d8), [§D10](../research/09-decisions.md#d10); [ADR-0013](0013-mac-mini-host-colima-native-ml.md)

## Context

Phase 0 provisions the services later sections build on. Research settled the choices and added mandatory constraints: the lake lives on host APFS (readable by native ML workers), Valkey instead of Redis (licensing), and **no MinIO** (community edition unmaintained) for MLflow artifacts.

## Decision

- **Lake (D4)**: `data/lake` on the host, bind-mounted read-write only into `marketdata` (the single writer, running as the owner's UID). Parquet + DuckDB 1.5.x and the Postgres manifest arrive in Phase 1 (section 03).
- **Cache (D6)**: Valkey 9 (`valkey/valkey:9-alpine`), ACL file from secrets (`default` user disabled), AOF persistence, 256 MB LRU; internal network only. Optional until Phase 1 uses it.
- **Tracking (D8)**: MLflow 3 server (own image), Postgres database `mlflow` (role `mlflow`), artifacts on the host filesystem (`data/mlflow-artifacts`, served by `--serve-artifacts`, backed up by `make backup`). RustFS only if S3 semantics become necessary.
- **Orchestration (D10)**: Prefect 3 server (official image, non-root, analytics off), Postgres database `prefect`. Workers arrive with Phase 3/4 jobs.
- MLflow, Prefect, and the assistant are in the `ml` profile (not started by default).

## Alternatives considered

| Option | Why not |
|---|---|
| MinIO for artifacts | Community edition unmaintained (research D8). |
| Redis 8 | AGPL/RSAL/SSPL licensing; Valkey is BSD. |
| Lake in a Docker volume | Native macOS workers could not read it without copying. |
| Dagster / Airflow | Heavier idle footprint; Prefect acquired Dagster (2026-07). |

## Consequences

- Positive: licence-clean, small footprint, one backup path (`make backup` covers Postgres, lake, artifacts).
- Negative / risks: atomic rename across virtiofs must be verified in Phase 1 (research follow-up).

## Validation

`make up PROFILES=core,observability,ml` brings MLflow and Prefect to healthy; backup drill restores the lake byte-for-byte.
