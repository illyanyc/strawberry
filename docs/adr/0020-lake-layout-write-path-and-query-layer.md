# ADR-0020: Lake layout, single-writer path, reference data, and the point-in-time query layer

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D38 (implements D4, D6, D27 for section 03)
- **Related:** [storage.md](../business-logic/storage.md) ST-001..ST-010, [data-and-features.md](../business-logic/data-and-features.md) DF-001..DF-013, research [06 §3](../research/06-architecture-and-stack.md), [04 §6.1](../research/04-brokers-data-ingestion.md), [ADR-0015](0015-phase0-data-services.md), spike V4 in [verify-early.md](../verify-early.md)

## Context

D4 fixed the store (Hive-partitioned Parquet + DuckDB, single writer, temp file + atomic rename, Postgres manifest, lake on host APFS). Section 03 has to make that concrete: file layout, how idempotent writes and corrections work without a database, how readers avoid partial files and duplicates, where reference data lives so Go services and Python research see the same thing, how adjustments are applied point-in-time (D27), and how fast the reads are.

## Decision

1. **Writer in Go, inside `marketdata`** (the section prompt's "module inside marketdata" option, not a separate gRPC service): pure-Go `parquet-go` (no CGO, so multi-arch images stay simple). Layout `bars/feed=<feed>/timeframe=<tf>/year=YYYY/month=MM/{part,staging}-<ULID>.parquet`, superseded versions in `bar_revisions/…`, context events in `context/kind=<kind>/…`, reference mirrors in `ref/<table>.parquet`, feature snapshots in `snapshots/<id>/`. ZSTD, schema version in file metadata, rows sorted by `(instrument_id, ts_open)`.
2. **Idempotency and corrections without a database (ST-004..ST-006).** Writes go to staging files; compaction merges a partition into one part file keyed by `(instrument_id, timeframe, ts_open, feed)`. Identical re-deliveries are dropped (a backfill rerun adds zero rows); a content change increments `revision` and moves the old version to `bar_revisions` (both retained, DF-007). Candidates are ordered by `available_at`, then `ingested_at`.
3. **Atomic visibility (ST-002).** Every file is written to a dot-prefixed temp name, fsynced, renamed, and the directory fsynced; readers glob `*.parquet` only. Old files are deleted only after the new one is durable; readers deduplicate by key and highest revision, so the transient overlap is harmless. Spike V4 verifies this across VirtioFS on the Mac.
4. **Manifest (ST-003).** `marketdata.lake_files` records every file with row count, bytes, SHA-256, and time range; `Verify` finds missing, corrupt, and untracked files.
5. **Reference data (ST-007).** Schema `refdata` (instrument master with symbol history and broker IDs over validity ranges, attributes, the XNYS calendar mirror, corporate actions per source, point-in-time universe membership) has one writer (marketdata) and a `refdata_reader` group (ledger, execution, api-gateway, broker-gateway). marketdata mirrors it to `ref/*.parquet` so Python never needs database credentials and dataset snapshots can hash exact inputs.
6. **Calendar (MD-005).** One CSV generated from `exchange_calendars` 4.13.2 (2005–2030, regular open/close, half days, 04:00/20:00 ET extended bounds) embedded in Go (`libs/go/calendar`) and shipped as Python package data; a test fails if the copies diverge.
7. **Query layer in Python on Polars** (`tbcore.data`): `bars(…, as_of, adjust, feed, versions)`, `asof_join`, `universe`, `resolve`, `history`, `context`. Polars keeps nanosecond timestamps (DuckDB reads UTC-adjusted Parquet timestamps as microseconds) and its `join_asof` handled a 9.7M × 1.9M as-of join in 0.6 s, while DuckDB's `ASOF JOIN` did not finish in 5 minutes on the 4-core reference VM. DuckDB 1.5.6 stays pinned for ad-hoc SQL over the same files.
8. **Adjustment at read time (DF-005, D27).** Raw prices are stored; `bars()` backward-adjusts with actions known as of `as_of` (`available_at <= as_of` and `ex_date <= as_of`): split factor `1/ratio` (volume × ratio), dividend factor `1 − amount / previous close`. Cross-source disagreement blocks adjustment (MD-012). Corporate actions are knowable from the declaration date, else the ex-date, never later than first fetch (MD-011).
9. **Prices are float64 in the lake.** The lake is the research store; the order path and ledgers never read it and keep decimals (OE-007). Decimal strings with ≤ 15 significant digits round-trip exactly through float64.
10. **Hot cache (D6, ST-009)** keys `bar:last`, `bar:ring` (500, newest first), `quote:last`, `feat:<set>:<id>`, `md:watermark:<tf>` with protobuf values and per-class TTLs, documented in `contracts/cache/README.md`.
11. **Data quality (ST-010)** is implemented twice (Go for backfill chunks, Python for research) and both are tested against one fixture with one expected report.

## Alternatives considered

| Option | Why not |
|---|---|
| DuckDB (CGO) as the Go writer | CGO cross-compilation for arm64 in CI; one-writer-per-file locking is what D4 avoids. |
| DuckLake now | D4 defers it until backfill and live ingestion write concurrently; a single writer with compaction covers Phase 1. |
| Readers deduplicating everything always | 2.3× slower loads; compacted partitions are unique by construction, so only staging/multi-file partitions are deduplicated. |
| DuckDB `ASOF JOIN` for `asof_join` | Did not complete on 10M rows on the reference VM; Polars was under a second with identical semantics. |
| Storing adjusted prices | Rewrites history on every split and leaks the future (research 03, D27). |
| Reference data only in Postgres | Python research would need DB credentials, and snapshots could not hash exact inputs. |

## Consequences

- Positive: idempotent writes and corrections with plain files; readers never see partial files; point-in-time reads include superseded revisions; Go and Python share the calendar and the DQ rules.
- Negative / risks: a monthly 1m partition for 3,000 symbols is ~500 MB to rewrite per compaction (compaction runs at session end); corrections are visible to readers only after compaction unless the producer sets the revision. DuckLake is the upgrade path if either hurts.
- Follow-up: run spike V4 on the Mac; re-measure performance on the Mac Mini (the VM numbers are a lower bound).

## Validation

Go: `TestDFI2IdempotentWritesAndCompaction`, `TestDF007CorrectionsKeepBothVersions`, `TestST003VerifyDetectsCorruption`, `TestST010DQFlagsInjectedDefects`, `TestRefdataPostgresRoundTrip`, `TestST009*`, `TestV4AtomicRenameNoPartialReads`. Python: `test_dfi1_property_as_of_never_returns_future_rows` (10,000 cases), `test_df005_*`, `test_reads_go_written_lake_with_revisions`, `test_dq_flags_injected_defects_same_as_go`, `test_perf_targets` (`TB_PERF=1`).
