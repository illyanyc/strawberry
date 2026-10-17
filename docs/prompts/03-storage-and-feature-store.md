---
title: "Section 03 – Storage and Feature Store"
phase: "1"
depends_on: [00, 01, 02]
updated: 2026-10-17
---

# Section 03: Storage and Feature Store

## Role

You are a data engineer specializing in time-series and financial data. You think in bitemporal terms (when something happened vs when we knew it), you are paranoid about look-ahead leakage, and you choose storage layouts that make the correct query the easy query.

## Context

The platform needs three kinds of storage: an **analytical store** for large volumes of bars, ticks, and context data used by research and backtesting; a **transactional store** for orders, ledgers, users, and audit (owned by other sections, provisioned here); and a **hot cache / online store** for the latest bars, quotes, and live feature vectors. On top of these sits a **feature store**: one feature definition library that produces identical values offline (for training and backtests) and online (for live inference). Non-negotiable N2 (no look-ahead bias) is this section's main responsibility.

## Scope

In scope:
- Analytical lake layout, schemas, partitioning, compaction, retention, and query layer.
- Instrument master (internal `instrument_id`, symbol history, broker IDs, listing/delisting dates).
- Exchange calendar service (sessions, half days, holidays, early closes).
- Corporate action storage and as-of adjustment functions.
- Point-in-time query API (as-of joins on `available_at`).
- Hot cache schema and TTLs.
- Feature store: feature definitions, versioning, offline materialization, online computation, dataset snapshots.
- Data quality checks.
- Postgres schemas for shared reference data (other sections own their own schemas).

Out of scope:
- Fetching data from brokers or vendors (sections 05 and 09 write through this section's writer API).
- ML labels and model training (section 10), though label generation uses the point-in-time API.
- Order, ledger, and audit schemas (sections 06, 07, 02).

## Inputs and dependencies

- Section 01: Postgres, Redis/Valkey, MinIO, volumes, backups.
- Section 02: DB roles and secrets.
- Master contracts: `Bar`, `Quote`, `Trade`, `CorporateAction`, `ContextEvent`, `FeatureVector`.
- `INPUT PENDING (U2)`: asset classes. Default stocks + ETFs; schema has `asset_class`.
- `INPUT PENDING (U3)`: universe size drives storage sizing. Default ~100 symbols in dev; design for 3,000 symbols × 1m bars × 10+ years.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Analytical store | Parquet files (Hive-partitioned by `dataset/timeframe/year/month`, sorted by `instrument_id, ts`) queried with DuckDB; ZSTD compression | `DECISION PENDING (research §7)` (D4) |
| Write path | One **writer service** owns all lake writes (append small files to a staging area, periodic compaction into partition files); readers open DuckDB read-only over Parquet. No shared `.duckdb` file with multiple writers. | Follows D4 |
| Alternative if research prefers a server DB | QuestDB or ClickHouse for hot ticks/bars with Parquet export for research | `DECISION PENDING (research §7)` |
| Hot cache | Redis/Valkey | `DECISION PENDING (research §7)` (D6) |
| Feature store implementation | Custom thin layer in `libs/py/tbcore.features` (definitions as Python functions over Polars/DuckDB frames) with offline = DuckDB/Polars, online = incremental computation + Redis; Feast only if it clearly fits | `DECISION PENDING (research §3)` |
| Adjustment policy | Store raw unadjusted prices; apply split/dividend adjustments at read time as of the query's decision date | D27, high confidence |
| Macro data vintages | Store every vintage (ALFRED-style) with `available_at` = release time | High confidence |

## Interfaces and contracts to expose

- **Lake writer API** (gRPC, service `storage-writer` or a module inside `marketdata`): `WriteBars(stream Bar)`, `WriteQuotes`, `WriteTrades`, `WriteCorporateActions`, `WriteContextEvents`. Idempotent on natural keys (`instrument_id, timeframe, ts_open, feed` for bars). Returns watermarks per partition.
- **Query library** (`tbcore.data`, Python; thin Go reader if needed):
  - `bars(instruments, timeframe, start, end, as_of=None, adjust="split+div"|"split"|"raw", feed=None) -> polars.DataFrame`. When `as_of` is set, only rows with `available_at <= as_of` are returned and adjustments use only corporate actions known as of `as_of`.
  - `asof_join(left, right, on="instrument_id", left_time="decision_time", right_time="available_at", tolerance=None)` (DuckDB `ASOF JOIN`).
  - `universe(name, as_of) -> list[instrument_id]` using point-in-time membership and listing/delisting dates.
  - `calendar.sessions(start, end)`, `calendar.is_open(ts)`, `calendar.next_open(ts)`, `calendar.bar_close_times(timeframe, date)`.
  - `instruments.resolve(symbol, as_of)`, `instruments.history(instrument_id)`.
- **Feature store API** (`tbcore.features`):
  - `@feature(name, version, inputs, lookback, timeframe)` decorator; each feature declares its dependencies and maximum lookback.
  - `FeatureSet(name, version, features=[...])` with a content hash.
  - `materialize(feature_set, universe, start, end) -> DatasetSnapshot` (offline); a snapshot has an immutable `snapshot_id` = hash of feature set version, data watermarks, universe, and time range; stored as Parquet + manifest.
  - `OnlineFeatureEngine(feature_set).update(bar|event) -> FeatureVector` for live use; writes latest vectors to Redis `feat:{feature_set}:{instrument_id}`.
  - `parity_check(feature_set, instruments, window)` recomputes offline features for a recent live window and compares them to stored online vectors.
- **Hot cache keys** (documented in `contracts/`): `bar:last:{tf}:{instrument_id}`, `bar:ring:{tf}:{instrument_id}` (last N bars), `quote:last:{instrument_id}`, `feat:{feature_set}:{instrument_id}`, `md:watermark:{tf}`. TTLs set per key class.
- **Data quality report**: `dq_check(dataset, range) -> DQReport` (gaps vs calendar, duplicates, OHLC consistency, zero/negative prices, volume spikes, stale feeds, timestamp monotonicity).

## Functional requirements

- [ ] Define Parquet schemas (Arrow types) for bars, quotes, trades, corporate actions, context events, and feature snapshots; schema version stored in file metadata.
- [ ] Every row has `event_time` (or `ts_open`/`ts_close`), `available_at`, `ingested_at`, `source`, `feed`. Bars also have `is_final` (live partial bars are never written to the lake as final).
- [ ] Writer service: batch small writes, write to `staging/`, compact into sorted partition files on a schedule and at session end; atomic rename so readers never see partial files; dedupe on natural keys with deterministic conflict policy (prefer final over partial, preferred feed over secondary, later correction over earlier with both retained in an audit partition).
- [ ] Instrument master in Postgres: `instrument_id`, symbol history with validity ranges, exchange, asset class, listing/delisting dates, broker IDs (Alpaca asset ID, IBKR `conId`), shortable/easy-to-borrow snapshots over time.
- [ ] Exchange calendar for NYSE/NASDAQ with holidays, half days, and extended-hours sessions (default: use a maintained library such as `exchange_calendars` or `pandas_market_calendars`, mirrored into a table so Go services share it).
- [ ] Corporate actions table and adjustment functions; tests with known splits (e.g., a 4:1 and a 10:1 split) and dividends; as-of adjustment test where the split is announced after the query date.
- [ ] Point-in-time universe: membership snapshots with validity ranges; delisted instruments retained and queryable.
- [ ] Feature store with at least these MVP feature groups implemented as reference (section 10 extends): returns over N bars, rolling volatility, RSI, ATR, VWAP distance, volume z-score, time-of-day/day-of-week encodings, gap from prior close, market context (SPY return, sector ETF return), cross-sectional rank of returns within the universe.
- [ ] Feature computations are pure functions of data with `available_at <= as_of`; the framework enforces this by passing only filtered frames to feature functions (features cannot query storage themselves).
- [ ] Online engine maintains rolling state per instrument and produces bit-for-bit (or within float tolerance 1e-9) identical values to offline materialization for the same inputs.
- [ ] Dataset snapshots are immutable and garbage-collected only when unreferenced by MLflow runs or backtests.
- [ ] Retention: tick/quote data retention configurable (default 90 days hot, then compressed archive or delete), bars kept indefinitely.
- [ ] Backups integrate with section 01 (lake snapshot + Postgres dump).

## Non-functional requirements

- **Performance** (targets on dev hardware, revise with U4): load one year of 1m bars for 100 symbols into a Polars frame in under 3 s; as-of join of 10M rows under 10 s; latest-bar cache read p99 under 1 ms; online feature update under 5 ms per bar per instrument for the MVP feature set.
- **Correctness**: no feature or label can be computed from data with `available_at > as_of` (enforced, not by convention).
- **Concurrency**: one writer, many readers; readers never block the writer; research notebooks and backtests read concurrently with live ingestion.
- **Security**: lake bucket and Postgres roles least-privilege; read-only role for research and assistant.
- **Tests**: property-based tests for as-of joins (random event times, assert no future rows); golden-file tests for adjustments; offline/online parity tests; DQ tests on fixtures with injected gaps and duplicates.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/data-and-features.md` (point-in-time rules, adjustments, feature definitions), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Writing the same bars twice produces no duplicates; compaction preserves row counts and sort order (tested).
- [ ] `bars(..., as_of=t)` never returns rows with `available_at > t`, and uses only corporate actions known at `t` (property test with 10k random cases).
- [ ] Delisted symbols appear in `universe(name, as_of)` for dates when they were listed.
- [ ] Offline vs online feature parity on a recorded live day: max absolute difference ≤ 1e-9 (or documented tolerance) for every feature.
- [ ] `dq_check` flags injected gaps, duplicates, and bad OHLC rows in fixtures.
- [ ] Performance targets met on the reference machine and recorded in the PR.

## Pitfalls to avoid

- Storing only adjusted prices; historical adjusted data silently changes after every new split or dividend and leaks future information.
- Labeling bars by close time in one place and open time in another. Pick one convention (store both `ts_open` and `ts_close`) and test it.
- Using `event_time` instead of `available_at` for joins; earnings released after the close, news timestamps, and macro revisions all leak this way.
- Using today's index constituents for historical universes (survivorship bias).
- Multiple processes writing the same DuckDB database file; DuckDB supports one writer process at a time.
- Thousands of tiny Parquet files from streaming writes; compact them.
- Computing features with full-sample statistics (normalizing with the mean/std of the whole history); normalization must be rolling or fitted within the training fold only.
- Floating-point drift between Polars (offline) and incremental (online) implementations; use the same code path or test parity explicitly.
- Timezone bugs: store UTC, convert only at display; DST shifts the US open from 14:30 to 13:30 UTC.
