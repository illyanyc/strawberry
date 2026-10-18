# ml

Training jobs, runs, diagnostics, ablations, model registration, and batch prediction (section 10). Native launchd worker on macOS (D29), container on Linux (`ml-workers` profile).

**Status:** Phase 4  ·  **Language/stack:** Python 3.12 on `tbcore.ml` (LightGBM, NumPy, Polars), gRPC (`tbcore.rpc`), Postgres (`research` schema), MLflow REST mirror ([ADR-0035](../../docs/adr/0035-ml-and-optimizer-contracts-and-analyst-tool-apis.md), [ADR-0036](../../docs/adr/0036-ml-training-library-model-zoo-and-diagnostics.md), [ADR-0037](../../docs/adr/0037-ml-worker-run-store-and-tracking.md))

## Responsibilities
- Owns: `MLService`, the job queue and cost guard, run persistence (artifacts + `research.docs` + MLflow mirror), diagnostics reads, ablations, model versions, `ml.run.completed`.
- Does not own: training/feature/label/model code (`tbcore.ml`), trial accounting and lineages (optimizer), the holdout (optimizer's `HoldoutService`), the analyst (assistant).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9090` | `MLService` (allowlist: api-gateway, optimizer; analyst tools also assistant) | `tb.ml.v1` (`ml_service.proto`) |
| out | gRPC | `OptimizerService.GetLineage`/`RecordTrial`, `HoldoutService.CheckDataAccess` | `tb.optimizer.v1` |
| out | bus | `ml.run.completed` (stream `RESEARCH`) | `tb.ml.v1.MLRunCompleted` |
| out | HTTP | MLflow REST (`TB_ML_MLFLOW_URL`), burst hook (`TB_ML_BURST_URL`) | ADR-0037 |
| in | HTTP (internal) | ops endpoints `:8081` | OP-010 |

## Business rules enforced here
- ML-011 (submit validation), ML-024 (analyst tagging, budget, ledger), ML-025 (ablations), ML-030 (queue), ML-031 (cost guard), ML-032 (devices, burst hook) → `src/tb_ml/core.py`, `devices.py`
- ML-013, ML-022 (run store, MLflow mirror, event) → `tbcore/ml/runs.py`, `core.py`
- EX-030 (actor from the service token) → `src/tb_ml/grpc_api.py` via `tbcore.ml.caller`
- Tests: `tests/test_ml_service.py`

## Layout
| Path | Purpose |
|---|---|
| `src/tb_ml/__main__.py` | Service (`python -m tb_ml`) and the `train` CLI |
| `src/tb_ml/core.py` | `MLCore`: validation, estimate, queue, runs, diagnostics, ablations, registry, prediction |
| `src/tb_ml/grpc_api.py` | `MLService` adapter and allowlist |
| `src/tb_ml/link.py` | Optimizer/holdout client (`GrpcLink`, `NoLink`) |
| `src/tb_ml/devices.py` | Device selection, `BurstHook` |
| `tests/` | Service tests |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_ML_GRPC_ADDR` | `0.0.0.0:9090` | gRPC listen address |
| `TB_ML_ARTIFACTS_DIR` | `data/ml-artifacts` | Run artifacts and dataset snapshots |
| `TB_LAKE_DIR` | — | Lake root for `data_source=lake` |
| `TB_DB_USER` | — | `svc_ml`; unset = in-memory store and audit (dev only) |
| `TB_ML_MLFLOW_URL` | — | MLflow mirror (`http://mlflow:5000` with the `ml` profile) |
| `TB_OPTIMIZER_ADDR`, `TB_HOLDOUT_ADDR` | — | Lineages, trial ledger, holdout boundary |
| `TB_NATS_URL`, `TB_NATS_USER` | —, `ml` | Bus for `ml.run.completed` |
| `TB_ML_JOB_BUDGET_CPU_SECONDS` | `3600` | Cost guard (ML-031) |
| `TB_ML_WORKERS` | `1` | Worker threads |
| `TB_ML_BURST_URL` | — | Cloud GPU burst hook (off by default, ML-032); token from secret `ml_burst_token` |

## Run and test
- Run: `make up PROFILES=core,ml,ml-workers` (Linux) or the launchd worker on macOS; one-off: `uv run python -m tb_ml train --config cfg.json`.
- Test: `uv run pytest services/ml/tests libs/py/tbcore/tests/test_ml.py`.

## Operational notes
Queued jobs live in memory; a restart drops them (resubmit). MLflow is a mirror: runs are complete without it. Synthetic data sources (`synthetic:<preset>`) are for dev and tests; their candidates are never live-eligible (EX-052).

## Gotchas
- Shared trading logic (features, labels, models, evaluation) belongs in `libs/py/tbcore`, never here.
- Never trust `CallerContext.actor` from a request: `tbcore.ml.caller.resolve` derives it from the token.
- On macOS this service runs natively under launchd (D29); see `infra/macos/`.
