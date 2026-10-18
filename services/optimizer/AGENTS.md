# optimizer

Studies, research lineages with one trial ledger, research cycles, gate reports, the registry and champion/challenger, the analyst permission table, retraining, and the sealed holdout service (section 11).

**Status:** Phase 4  ·  **Language/stack:** Python 3.12 on `tbcore.ml`, Optuna 5, the backtest scorer (`tb_backtest.scorer`), gRPC (`tbcore.rpc`), Postgres (`research` and `holdout` schemas) ([ADR-0035](../../docs/adr/0035-ml-and-optimizer-contracts-and-analyst-tool-apis.md), [ADR-0038](../../docs/adr/0038-nested-studies-trial-ledger-and-research-cycles.md), [ADR-0039](../../docs/adr/0039-sealed-holdout-service.md), [ADR-0040](../../docs/adr/0040-registry-promotion-gates-and-analyst-permissions.md))

## Responsibilities
- Owns: `OptimizerService`, `HoldoutService`, nested Optuna studies, the trial ledger and lineage statistics, MinBTL cycles, pre-registration checks, gate reports, registry transitions and evidence packs, research deployments, retraining policies, `optimizer.study.completed`, `backtest.completed`, `ml.registry.stage_changed`.
- Does not own: training and evaluation code (`tbcore.ml`, `tbcore.backtest`), the scorer thresholds (`services/backtest`), single training jobs (ml), the analyst's reasoning (assistant), the live switch (section 15).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | gRPC `:9091` | `OptimizerService` (api-gateway; analyst tools also assistant; `RecordTrial` ml; `DemoteCandidate` monitor) | `tb.optimizer.v1` (`optimizer.proto`) |
| in | gRPC `:9092` (audience `holdout`) | `HoldoutService` (api-gateway; `CheckDataAccess` also ml, optimizer; never assistant) | `holdout.proto` |
| out | bus | `optimizer.study.completed`, `backtest.completed`, `ml.registry.stage_changed`, `optimizer.retrain_requested.<lineage>`, `alert.*` | `tb.optimizer.v1`, `tb.ml.v1`, `tb.alerts.v1` |
| out | file | research deployments (`TB_OPT_DEPLOY_REGISTRY`) read by strategy-runtime | strategy registry entry format |
| in | HTTP (internal) | ops endpoints `:8081` | OP-010 |

## Business rules enforced here
- EX-001..EX-006 (nested studies) → `src/tb_optimizer/nested.py`
- EX-020..EX-026 (ledger, statistics, cycles, pre-registration) → `core.py`, `stats.py`
- EX-010..EX-015 (holdout) → `holdout.py`
- EX-024, EX-051 (gate reports, event-driven evidence, leakage check) → `gates.py`
- EX-030..EX-036, EX-050..EX-057 (permissions, limits, transitions, evidence packs, deployments) → `registry.py`, `core.py`
- EX-040, EX-041 (retraining) → `core.py` (`trigger_retrain`, `tick`)
- Tests: `tests/test_optimizer.py`

## Layout
| Path | Purpose |
|---|---|
| `src/tb_optimizer/__main__.py` | Service: both gRPC servers, retrain ticker, ops |
| `src/tb_optimizer/core.py` | `OptimizerCore` |
| `src/tb_optimizer/nested.py` | Nested evaluation with Optuna sub-studies |
| `src/tb_optimizer/stats.py` | Lineage statistics, MinBTL budget |
| `src/tb_optimizer/gates.py` | Gate reports and the analyst view |
| `src/tb_optimizer/registry.py` | Permission function, registry entries, evidence packs |
| `src/tb_optimizer/holdout.py` | Holdout service core |
| `src/tb_optimizer/grpc_api.py` | gRPC adapters and allowlists |
| `tests/` | Service tests |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_OPT_GRPC_ADDR` | `0.0.0.0:9091` | `HoldoutService` listens on the next port |
| `TB_DB_USER` / `TB_HOLDOUT_DB_USER` | — | `svc_optimizer` (research) and `svc_holdout` (holdout); unset = in-memory (dev only) |
| `TB_ML_ARTIFACTS_DIR` | `data/ml-artifacts` | Shared with ml: runs, trial series, candidates, evidence packs |
| `TB_HOLDOUT_MONTHS` | `12` | Default holdout length for lake lineages (EX-010) |
| `TB_OPT_DEPLOY_REGISTRY` | — | Research deployment file for strategy-runtime |
| `TB_OPT_OPTUNA_STORAGE` | — | Optional Optuna RDB URL (dashboard); the ledger is the system of record |
| `TB_OPT_PAPER_ACCOUNT_INTRADAY` / `_SWING` | `sim-paper` | Broker accounts in deployment entries |
| `TB_GRADUATION_MIN_SESSIONS` | `20` | EX-053 |
| `TB_OPT_GLOBAL_ANALYST_TRIALS` | `0` (off) | Global analyst trial cap (EX-036) |
| `TB_NATS_URL`, `TB_NATS_USER` | —, `optimizer` | Bus |

## Run and test
- Run: `make up PROFILES=core,ml,ml-workers` (Linux) or the launchd worker on macOS.
- Test: `uv run pytest services/optimizer/tests` (about a minute: several small nested studies on synthetic data).

## Operational notes
Studies run on worker threads of this process; a restart abandons running studies (their trials stay in the ledger). Lineages start at autonomy level 0; the owner opens a research cycle before any study can start.

## Gotchas
- Never report a best inner metric as expected performance; the outer estimate and the lineage DSR are what gates read.
- The holdout store must stay on `svc_holdout`; never give the research role a holdout grant.
- `gates.py` imports the scorer: the optimizer is on the evaluator side of BT-081, and the assistant must never import this package.
