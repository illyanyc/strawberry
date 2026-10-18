# Data and features

**Prefix:** `DF-`  ·  **Owners:** `marketdata` (lake writer), `context-data`, `libs/py/tbcore` (query layer, features, labels)  ·  **Related:** [lifecycle](strategies-and-model-lifecycle.md), [storage](storage.md), [market data](market-data.md), D4, D27, [ADR-0020](../adr/0020-lake-layout-write-path-and-query-layer.md), [ADR-0021](../adr/0021-feature-store.md)

## Scope

Market and context data correctness, point-in-time access, corporate-action adjustment, universes, feature and label computation, and offline/online parity. Validation methodology is in [lifecycle](strategies-and-model-lifecycle.md).

## Concepts and states

- **Timestamps:** `event_time` (happened), `available_at` (knowable by us), `ingested_at` (stored). Bars carry `ts_open`, `ts_close`; available at `ts_close` + publication delay.
- **Bars:** `is_final` distinguishes closed bars from live partial bars.
- **Dataset snapshot:** immutable, ID'd extract used by a training run or backtest.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| DF-001 | Every stored market and context record MUST carry `event_time` (or `ts_open`/`ts_close`), `available_at`, `ingested_at`, `source`. | Implemented | `services/marketdata/internal/lake` (`RowFromBar` rejects bars without provenance or available before close); context rows in `StageContext` |
| DF-002 | All features, labels, joins, and backtests MUST filter on `available_at <= as_of`; joining on `event_time` is forbidden. | Implemented | `tbcore.data.Lake.bars(as_of=)`, `Lake.context(as_of=)`, `asof_join` (`<=`), `materialize` leakage assertion; `test_dfi1_property_as_of_never_returns_future_rows` (10,000 cases), `test_asof_join_fundamentals_snapshots_point_in_time` |
| DF-003 | Feature functions are pure and receive only pre-filtered frames; they MUST NOT query storage. | Implemented | `tbcore.features.core.compute` (features are expressions over the passed frame); `test_df003_features_cannot_see_rows_after_as_of` |
| DF-004 | Training, backtest, and live inference MUST use the same feature code; offline and online values MUST match within `1e-9` (or a documented tolerance). | Implemented | `tbcore.features` (`compute` shared by `materialize` and `OnlineFeatureEngine`; tolerance `1e-9 * max(1, |a|, |b|)`); `test_df004_offline_online_parity_on_recorded_week` |
| DF-005 | Prices are stored raw (unadjusted) with a corporate-actions table; adjustments are applied at read time using only actions known as of the decision time. | Implemented | `lake.RowFromBar` (raw only), `tbcore.data.apply_adjustment`; `test_df005_adjustment_golden_splits_dividends_and_announcement_timing`, `test_df005_property_adjustment_uses_only_known_actions` |
| DF-006 | Universes MUST be point-in-time and include delisted instruments for the dates they were listed. | Implemented | `refdata` (symbol spans, delisting closes memberships), `Lake.universe`, `materialize` membership filter; `TestDF006TickerChangesRecyclingAndDelisting`, `test_df006_universe_includes_delisted_while_listed` |
| DF-007 | Partial (non-final) bars MUST NOT be written to the lake as final; a later correction supersedes an earlier value with both retained for audit. | Implemented | `lake.RowFromBar` (rejects partial bars), `lake.Compact` (revisions retained); `TestDF007*` |
| DF-008 | Normalization, scaling, and feature selection MUST be rolling or fitted within the training fold only. | Specified | — |
| DF-009 | Dataset snapshots are immutable and garbage-collected only when unreferenced by runs or backtests. | Implemented | `tbcore.features.materialize` (content-addressed, temp-dir rename), `gc_snapshots`; `test_df009_snapshots_immutable_deterministic_and_gc` |
| DF-010 | Macro and other revised series MUST be stored with vintages; features use the vintage available at `as_of`. | Implemented | ALFRED vintages as context versions (`tb_context_data.sources.alfred`), `tbcore.context.ContextStore.macro`; `test_cx006_macro_as_of_returns_vintage_known_then`, `test_cx002_go_written_context_fixture_vintages_as_of` (rules in [context data](context-data.md)) |
| DF-011 | All timestamps are stored in UTC; local exchange time is derived via the exchange calendar (DST-aware). | Implemented | `libs/go/calendar`, `tbcore.data.calendar`, `tbcore.data.to_ns` (rejects naive datetimes); `TestDF011DSTAndHalfDays`, `test_naive_datetimes_rejected` |
| DF-012 | Leakage tests (shuffled-future, label-shift, timestamp assertions) MUST run in CI and cover every feature set and label. | Implemented for features and strategies; generated-code lint implemented (`tbcore.features.lint`, BT-064); labels Specified | `test_df012_future_shift_leakage`, `test_df012_shuffled_labels_carry_no_signal`, `materialize` timestamp assertion |
| DF-013 | Every feature MUST have a finite declared `lookback` (its value depends only on the current and previous `lookback − 1` bars of the instrument) and MUST be invariant to scaling the price or volume series by a constant (returns, ratios, z-scores of logs), so full-history and ring-buffer computations and any later backward adjustment agree. | Implemented | `tbcore.features.core`, `library`; parity and future-shift tests |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| DF-I1 | `bars(..., as_of=t)` never returns rows with `available_at > t`. | `test_dfi1_property_as_of_never_returns_future_rows` (10,000 cases) |
| DF-I2 | Writing the same data twice produces no duplicates. | `TestDFI2IdempotentWritesAndCompaction` |
| DF-I3 | A snapshot ID always returns identical data. | Content-addressed IDs over exact input files; `test_df009_*` |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Earnings released after the close with `event_time` during the day | Usable only from `available_at` (20:00 New York, CX-007) | DF-002, CX-007 |
| Split announced after the query date | Not applied to an as-of query before the announcement | DF-005 |
| Symbol delisted in 2022 | Present in historical universes up to delisting | DF-006 |
| DST change | US open at 14:30 UTC in winter, 13:30 UTC in summer; calendar handles it | DF-011 |
| Live gap in streaming bars | Gap repaired from REST; DQ report records it; features for affected bars marked stale | DF-007, RM-004 |

## Edge cases (storage and features)

| Scenario | Expected behavior | Rules |
|---|---|---|
| Split announced before its ex-date, query as of a date between | Not applied (the price at `as_of` is pre-split) | DF-005 |
| Bar corrected after a decision used it | `as_of` reads return the version known then; `versions="first"` returns what live saw | DF-002, DF-007 |
| Feature window straddles a split | Same value offline and online because features are scale-invariant | DF-013 |
| Zero-volume bar in the volume window | `volume_z_20` is null for the window | DF-013 |
| Naive datetime passed to the query layer | `ValueError` | DF-011 |

## Open questions

- None open; publication delays are set by MD-003.
