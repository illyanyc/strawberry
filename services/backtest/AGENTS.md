# backtest

Backtest runs, the D31 promotion scorer, and the evaluator boundary (section 08, [ADR-0031](../../docs/adr/0031-two-tier-backtester-and-simcore.md), [ADR-0034](../../docs/adr/0034-evaluator-isolation-scorer-and-leakage-suite.md)). The engines themselves live in `libs/py/tbcore` (`tbcore.backtest`, `tbcore.validation`, `tbcore.parity`); this service runs them and holds the scorer, which must stay outside `tbcore`.

**Status:** Phase 3  ·  **Language/stack:** Python 3.12 on `tbcore`; event-driven runs need `tb-simcore` (Go) ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: the CLI for backtest runs, parity checks, and the leakage suite; the scorer (`tb_backtest.scorer`, BT-080); the evaluator manifest (`evaluator_manifest.json`, BT-081); the ops surface.
- Does not own: engine, cost, fill, splitter, or statistics code (`tbcore`), the trial ledger and studies (optimizer, section 11), the analyst sandbox (section 12).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | HTTP (internal) | ops endpoints `:8081` (no arguments) | OP-010 |
| in | CLI | `python -m tb_backtest run|parity|leakage|candidates` | `BacktestConfig` JSON, bus recordings (`tb.backtest.v1.RecordedMessage`) |
| out | files | run artifacts (`config.json`, `*.parquet`, `summary.json`, `report.json`, `tearsheet.html`, `run.binpb`) under `--out/<config hash>` | `tb.ml.v1.BacktestRun` |
| in/out | files | analyst candidate jobs in the shared `analyst-jobs` volume: reads `request.json` + `candidate.py`, writes `inputs.npz`, `ready`, `verdict.json` (AN-071, AN-072, BT-082) | ADR-0043 |
| out | Python API | `score(CandidateEvidence) -> Verdict`, `analyst_view(Verdict) -> AnalystView` | section 11 calls it; the analyst sees only `AnalystView` |

## Business rules enforced here
- BT-080 → `src/tb_backtest/scorer/gate.py` (`tests/test_scorer.py`)
- BT-081 → `evaluator_manifest.json` (`tests/python/test_evaluator_isolation.py`)
- BT-082, AN-072 → `src/tb_backtest/candidates.py` (`tests/python/test_analyst_sandbox.py`)
- BT-015, BT-065, BT-070, BT-071 through the CLI (`tests/test_cli.py`)

## Layout
| Path | Purpose |
|---|---|
| `src/tb_backtest/__main__.py` | Ops service (no arguments) and the `run`, `parity`, `leakage` commands |
| `src/tb_backtest/candidates.py` | Evaluator-side checks of analyst candidate features: synthetic inputs, PIT lint, determinism, future shift (`candidates watch|step JOBS_DIR`; Compose service `candidate-checker`) |
| `src/tb_backtest/scorer/` | D31 promotion gate (`GatePolicy`, `score`, `analyst_view`) |
| `evaluator_manifest.json` | Evaluator paths and modules the analyst may not see, write, or import |
| `tests/` | Scorer and CLI tests |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_SIMCORE_BIN` | — | Path of `tb-simcore` (`make simcore` builds `.tools/bin/tb-simcore`); without it the binary is built on demand with Go |
| `TB_SERVICE` | `backtest` | Set by the entrypoint |

## Run and test
- Run: `uv run python -m tb_backtest run --config cfg.json --bars bars.parquet --out runs/ --stress` (or `--lake ROOT`); parity: `uv run python -m tb_backtest parity paper-gate.tbrec --config tests/fixtures/parity/orb-paper-gate.config.json --mode model`.
- Candidate checks: `uv run python -m tb_backtest candidates step /path/to/jobs` (one pass) or `watch` (service).
- Test: `uv run pytest services/backtest/tests tests/python/test_evaluator_isolation.py tests/python/test_analyst_sandbox.py`; Gate 3 parity: `make paper-gate && make parity-gate`; performance: `make perf-backtest`.

## Operational notes
On the Mac Mini, backtests run as native workers (D29) with `TB_SIMCORE_BIN` set; the Compose image does not include `tb-simcore` yet (Planned with the Prefect batch flow, section 11).

## Gotchas
- Never move the scorer into `tbcore`, and never let assistant code import a manifest module; the isolation test fails the build.
- A change under `services/execution/internal/{oms,risk,simcore}` or `services/ledger/book` changes backtest behavior (same code as live, BT-003).
