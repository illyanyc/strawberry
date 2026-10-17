# Data and features

**Prefix:** `DF-`  ·  **Owners:** `marketdata`, `context-data`, `libs/py/tbcore` (features, labels), storage writer  ·  **Related:** [lifecycle](strategies-and-model-lifecycle.md), D4, D27

## Scope

Market and context data correctness, point-in-time access, corporate-action adjustment, universes, feature and label computation, and offline/online parity. Validation methodology is in [lifecycle](strategies-and-model-lifecycle.md).

## Concepts and states

- **Timestamps:** `event_time` (happened), `available_at` (knowable by us), `ingested_at` (stored). Bars carry `ts_open`, `ts_close`; available at `ts_close` + publication delay.
- **Bars:** `is_final` distinguishes closed bars from live partial bars.
- **Dataset snapshot:** immutable, ID'd extract used by a training run or backtest.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| DF-001 | Every stored market and context record MUST carry `event_time` (or `ts_open`/`ts_close`), `available_at`, `ingested_at`, `source`. | Specified | — |
| DF-002 | All features, labels, joins, and backtests MUST filter on `available_at <= as_of`; joining on `event_time` is forbidden. | Specified | — |
| DF-003 | Feature functions are pure and receive only pre-filtered frames; they MUST NOT query storage. | Specified | — |
| DF-004 | Training, backtest, and live inference MUST use the same feature code; offline and online values MUST match within `1e-9` (or a documented tolerance). | Specified | — |
| DF-005 | Prices are stored raw (unadjusted) with a corporate-actions table; adjustments are applied at read time using only actions known as of the decision time. | Specified | — |
| DF-006 | Universes MUST be point-in-time and include delisted instruments for the dates they were listed. | Specified | — |
| DF-007 | Partial (non-final) bars MUST NOT be written to the lake as final; a later correction supersedes an earlier value with both retained for audit. | Specified | — |
| DF-008 | Normalization, scaling, and feature selection MUST be rolling or fitted within the training fold only. | Specified | — |
| DF-009 | Dataset snapshots are immutable and garbage-collected only when unreferenced by runs or backtests. | Specified | — |
| DF-010 | Macro and other revised series MUST be stored with vintages; features use the vintage available at `as_of`. | Specified | — |
| DF-011 | All timestamps are stored in UTC; local exchange time is derived via the exchange calendar (DST-aware). | Specified | — |
| DF-012 | Leakage tests (shuffled-future, label-shift, timestamp assertions) MUST run in CI and cover every feature set and label. | Specified | — |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| DF-I1 | `bars(..., as_of=t)` never returns rows with `available_at > t`. | Property test (≥ 10k random cases) |
| DF-I2 | Writing the same data twice produces no duplicates. | Idempotent-write test |
| DF-I3 | A snapshot ID always returns identical data. | Snapshot hash check |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Earnings released after the close with `event_time` during the day | Usable only from `available_at` (after close) | DF-002 |
| Split announced after the query date | Not applied to an as-of query before the announcement | DF-005 |
| Symbol delisted in 2022 | Present in historical universes up to delisting | DF-006 |
| DST change | US open at 14:30 UTC in winter, 13:30 UTC in summer; calendar handles it | DF-011 |
| Live gap in streaming bars | Gap repaired from REST; DQ report records it; features for affected bars marked stale | DF-007, RM-004 |

## Open questions

- Store choice and writer topology (D4), vendors and feeds (D20, INPUT PENDING U5).
- Publication delay defaults per feed and source (research §6).
