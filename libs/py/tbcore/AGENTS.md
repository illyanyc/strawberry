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
| `src/tbcore/context/` | Context data (section 09, ADR-0032): `availability` (per-source rule times, `stamp`, FINRA schedule CSV), `canonical` (payload JSON identical to the Go sink), `store.ContextStore` (point-in-time reads per `record_key`: `news_for`, `sentiment_agg`, `macro`, `upcoming_events`, `earnings_window`, `short_interest`, `filings`, `fundamentals`, `regime`), `schemas` (payload schemas), `news` (syndicated duplicates), `sentiment` (aggregates), `regime` (`regime_v1`), `events` (`categorize`, `EventBlackout`, `BlackoutStrategy`, `InMemoryEventSource`), `features` (`context_features`, joined by `materialize(context=...)` and the online engine) | CX-002..CX-007, CX-013..CX-018, DF-010 |
| `src/tbcore/svcauth.py` | Ed25519 service-token signer for Python gRPC clients, wire-compatible with `libs/go/svcauth` | SE-040 |
| `src/tbcore/data/calendar.py`, `xnys_sessions.csv` | XNYS sessions (same file as `libs/go/calendar`), bar opens/closes, lookups | MD-004, MD-005, DF-011 |
| `src/tbcore/dq.py` | `dq_check` (same rules as Go `internal/dq`) | ST-010 |
| `src/tbcore/features/core.py` | `@feature`, `FeatureDef`, `FeatureSet` (content hash), `compute` (the single computation path) | DF-003, DF-004, DF-013 |
| `src/tbcore/features/library.py` | 16 MVP features, `mvp_feature_set` | DF-013 |
| `src/tbcore/features/store.py` | `materialize` (snapshots, optional `context=` features), `gc_snapshots`, `OnlineFeatureEngine` (optional context store), `compare`, `parity_check` | DF-002, DF-004, DF-009, CX-018 |
| `src/tbcore/strategy/` | Strategy API (`Strategy`, `StrategyContext`, `Bar`, `Intent`, deterministic `new_ulid`), `fixed_fractional_qty`, `OpeningRangeBreakout` (Phase 2 baseline), `host.py` (`Runner`: registry hosting, bar dispatch, event blackouts, own positions, proto conversion; used by the strategy runtime and the backtester) | SM-011, SM-014, DF-002, RM-010, BT-002 |
| `src/tbcore/backtest/config.py`, `event.py`, `fills.py`, `simcore.py`, `recording.py`, `result.py` | `BacktestConfig` (hash = run identity), event-driven engine on `strategy.host.Runner` + `tb-simcore`, fill model, simcore client (builds the binary on demand, `TB_SIMCORE_BIN`), bus recordings, results and artifacts | BT-001..BT-008, BT-020..BT-024 |
| `src/tbcore/backtest/costs.py` | Cost model shared by both backtest tiers: spread regimes / EDGE estimator, square-root impact, broker commissions, dated regulatory fees, borrow, `cost_multiplier` | BT-010..BT-016 |
| `src/tbcore/validation/` | `splitters.py` (purged k-fold, CPCV with paths, walk-forward, era-wise), `stats.py` (PSR, DSR, MinTRL, MinBTL, ONC effective N, PBO/CSCV, SPA, Romano–Wolf, bootstrap CIs) | BT-030..BT-047, SM-002 |
| `tests/` | Unit, property, parity, leakage, perf (`TB_PERF=1`) tests; `lakekit.py` writes test lakes in the production layout | — |

## Run and test
- `uv run pytest libs/py/tbcore/tests`, `uv run ruff check .`, `uv run mypy` (repo root). Performance targets: `make perf`.
- Regenerate the calendar after bumping `exchange-calendars`: `make gen-calendar` (also updates the Go copy).

## Gotchas
- The event-driven backtester needs Go (to build `tb-simcore`) or `TB_SIMCORE_BIN`; a Go change under `services/execution/internal/{oms,risk,simcore}` or `services/ledger/book` rebuilds it automatically (content-hashed cache in `~/.cache/tb/simcore`).
- Keep redaction patterns in sync with the Go version; both have tests citing SE-012.
- Feature/label/strategy code must never be copied into services; import it from here.
- New features must be finite-lookback and price-scale invariant (DF-013) and appear in the parity and future-shift tests; changing a feature's code requires a version bump.
- Read timestamps with Polars, not DuckDB: DuckDB maps UTC-adjusted Parquet timestamps to microseconds, which can break strict `available_at` comparisons.
- Only marketdata writes the lake (D4); tests use `tests/lakekit.py` (`write_bars`, `write_context`, `context_row`) to build lakes in the same layout.
- Context payloads must hash identically in Go and Python (`tbcore.context.canonical`, digest pinned in `test_cx003_canonical_json_matches_go` and `TestCX003CanonicalPayloadDigest`); payload fields must be deterministic functions of vendor content (no fetch times), or every fetch becomes a new version.
- Context reads never key on `event_time`; use `ContextStore` (per `record_key`).
