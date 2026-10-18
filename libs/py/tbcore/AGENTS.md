# libs/py/tbcore

Shared Python library. It holds the code that MUST be shared by training, backtesting, and live inference (rules DF-004, SM-011): the lake query layer, the calendar, data quality, and the feature store; later labels, splitters, cost models, the strategy API, and metrics. Also the Python service template.

**Status:** In progress (Phase 1: data + features)  ·  **Language/stack:** Python 3.12, Polars 1.44 (pinned 1.x), DuckDB 1.5.6, PyArrow, FastAPI, pydantic v2 ([ADR-0004](../../../docs/adr/0004-core-language-split.md), [ADR-0020](../../../docs/adr/0020-lake-layout-write-path-and-query-layer.md), [ADR-0021](../../../docs/adr/0021-feature-store.md))

## Layout
| Path | Purpose | Rules |
|---|---|---|
| `src/tbcore/service.py` | `ServiceSettings` (`TB_*` env), `create_ops_app` (`/healthz`, `/readyz`, `/metrics`), `run()` for placeholder services | OP-010 |
| `src/tbcore/logging.py`, `redact.py` | JSON formatter with `service`, `correlation_id`, redaction (mirrors Go) | SE-012 |
| `src/tbcore/secrets.py` | `get_secret(name)` from mounted files | SE-010 |
| `src/tbcore/data/lake.py` | `Lake`: `bars(instruments, timeframe, start, end, as_of, adjust, feed, versions)`, `corporate_actions`, `universe`, `resolve`, `history`, `context`, `ref`; `apply_adjustment`; `asof_join` | DF-002, DF-005, DF-006, DF-007, ST-006, MD-012 |
| `src/tbcore/data/calendar.py`, `xnys_sessions.csv` | XNYS sessions (same file as `libs/go/calendar`), bar opens/closes, lookups | MD-004, MD-005, DF-011 |
| `src/tbcore/dq.py` | `dq_check` (same rules as Go `internal/dq`) | ST-010 |
| `src/tbcore/features/core.py` | `@feature`, `FeatureDef`, `FeatureSet` (content hash), `compute` (the single computation path) | DF-003, DF-004, DF-013 |
| `src/tbcore/features/library.py` | 16 MVP features, `mvp_feature_set` | DF-013 |
| `src/tbcore/features/store.py` | `materialize` (snapshots), `gc_snapshots`, `OnlineFeatureEngine`, `compare`, `parity_check` | DF-002, DF-004, DF-009 |
| `src/tbcore/strategy/` | Strategy API (`Strategy`, `StrategyContext`, `Bar`, `Intent`, deterministic `new_ulid`), `fixed_fractional_qty`, `OpeningRangeBreakout` (Phase 2 baseline) | SM-011, SM-014, DF-002, RM-010 |
| `tests/` | Unit, property, parity, leakage, perf (`TB_PERF=1`) tests; `lakekit.py` writes test lakes in the production layout | — |

## Run and test
- `uv run pytest libs/py/tbcore/tests`, `uv run ruff check .`, `uv run mypy` (repo root). Performance targets: `make perf`.
- Regenerate the calendar after bumping `exchange-calendars`: `make gen-calendar` (also updates the Go copy).

## Gotchas
- Keep redaction patterns in sync with the Go version; both have tests citing SE-012.
- Feature/label/strategy code must never be copied into services; import it from here.
- New features must be finite-lookback and price-scale invariant (DF-013) and appear in the parity and future-shift tests; changing a feature's code requires a version bump.
- Read timestamps with Polars, not DuckDB: DuckDB maps UTC-adjusted Parquet timestamps to microseconds, which can break strict `available_at` comparisons.
- Only marketdata writes the lake (D4); tests use `tests/lakekit.py` to build lakes in the same layout.
