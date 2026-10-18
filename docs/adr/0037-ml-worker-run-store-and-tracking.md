# ADR-0037: ML worker, run store, research and holdout schemas, and MLflow as a mirror

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D55 (refines D8, D10, D25, D29)
- **Related:** ML-013, ML-022, ML-024, ML-025, ML-030..ML-033, SE-033, SE-050, EX-I1, EX-I2; ADR-0013, ADR-0015, ADR-0035, ADR-0036

## Context

Phase 3 left "MLflow logging and Postgres summaries" open. Section 10 needs training jobs with progress, a cost guard, device selection for native macOS workers with a cloud GPU hook, runs stored with checksums, and the diagnostics the analyst and UI read. Section 11 needs an append-only trial ledger and a holdout store nothing on the analyst path can read. D10 names Prefect 3 as the orchestrator; Prefect's server runs in the `ml` profile, but no flows exist yet.

## Decision

1. **The ml worker** (`services/ml`, `python -m tb_ml`) serves `MLService` (ADR-0035) from `MLCore`: validation at submit (schema, timeframe, feature set, device, holdout boundary via the lineage and `HoldoutService.CheckDataAccess`), a cost estimate with an owner-confirmed budget override (analyst jobs over budget refused), an in-process priority queue (`retrain` > owner > analyst) with `TB_ML_WORKERS` threads, cancel between folds, progress streaming, ablations, model registration, and batch prediction. Analyst jobs need a lineage at autonomy ≥ 1 with budget left and are written to the lineage ledger through `OptimizerService.RecordTrial`.
2. **Run store:** artifacts on the filesystem under `TB_ML_ARTIFACTS_DIR/runs/<run_id>/` (restic-backed like MLflow artifacts, D8) with a SHA-256 checksum on the model bundle; the `TrainingRun`, `DiagnosticsReport`, importance by method, dataset profile, jobs, and model versions as protobuf documents in Postgres `research.docs` (`tbcore.docstore`). The document store has no delete, and the schema rejects deletes and truncates.
3. **MLflow is a mirror** fed through its REST API (`tbcore.ml.tracking.MlflowTracker`: experiment per lineage, params, metrics, tags, artifacts through `--serve-artifacts`). A mirror failure is noted on the run and never fails it. No MLflow client library is added.
4. **Schemas and roles:** `research` (owner `research_owner`; `svc_ml`, `svc_optimizer` SELECT/INSERT/UPDATE) and `holdout` (owner `holdout_owner`; `svc_holdout` only). The optimizer process opens the holdout store with the `svc_holdout` role; no other login role has any holdout grant (SE-033).
5. **Orchestration:** jobs run on the worker's own queue in Phase 4. A Prefect flow adapter behind the same submit interface is Planned; D10 stays the target for multi-step scheduled flows (retrains, nightly studies). This defers, not replaces, D10.
6. **Devices (D25, D29):** `auto` → CPU for linear and tree families; MPS/MLX only for families that declare them (none in Tiers 1–2); `cloud_gpu` only through `TB_ML_BURST_URL` (bearer token from the `ml_burst_token` secret; body = job ID, config, snapshot ID). Without a hook, or for a family without GPU support, the job is refused before it is queued.
7. **Bus:** `ml.run.completed` and `backtest.completed` in a new durable `RESEARCH` stream (`ml.>`, `backtest.>`, `optimizer.>`); NATS users `ml` and `optimizer`; both services get Ed25519 service keys (`tbctl secrets init`).

## Alternatives considered

| Option | Why not |
|---|---|
| MLflow as the system of record | Its schema is not ours to constrain (no append-only guarantee, no holdout separation); the analyst would need MLflow read access |
| Prefect flows for every training job now | Adds the Prefect client, a worker pool, and deployment plumbing for jobs that are single function calls; the queue is behind an interface so flows can replace it |
| One table per record type | Ten tables for records that are read by ID or lineage; the contract message already defines the shape |
| Holdout records in `research` with column grants | One mistaken grant away from the analyst path; a separate schema and role make the isolation testable (SE-033) |

## Consequences

- Positive: runs are reproducible from stored config, snapshot, commit, lockfile hash, seed, and threads; MLflow outages never lose a run; the holdout is isolated at the database level.
- Negative / risks: the in-process queue is lost on restart (queued jobs must be resubmitted; running jobs are marked failed on the next start, Planned); artifact files and Postgres rows are written separately (a crash between them leaves an artifact directory without a row, which is harmless).
- Follow-up: existing installs run `make secrets` (new `pg_svc_ml/optimizer/holdout_password`, `nats_ml/optimizer_password`, `svc_ml/optimizer_ed25519`) and `make migrate`.

## Validation

`services/ml/tests/test_ml_service.py` (validation, cost guard, devices and burst hook, queue order, stored run with diagnostics, card, checksum and event, analyst tagging and budgets, ablation reuse, registration and prediction, MLflow REST calls, actor forced to analyst over gRPC); `libs/py/tbcore/tests/test_docstore.py` (memory and Postgres store, append-only, audit sink); `TestSE032SchemaIsolation` (holdout and research grants, append-only).
