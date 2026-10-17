# Storage

**Prefix:** `ST-`  ·  **Owners:** `marketdata` (lake writer, refdata writer), `libs/go/cache`, `libs/py/tbcore/data` (readers)  ·  **Related:** [data and features](data-and-features.md) (DF-), [market data](market-data.md) (MD-), [ADR-0020](../adr/0020-lake-layout-write-path-and-query-layer.md), D4, D6

## Scope

How market and context data is physically stored and read: the Parquet lake (layout, single writer, atomic writes, compaction, revisions, manifest, retention), reference-data tables and their lake mirrors, the hot cache, and data-quality checks. Point-in-time semantics (what a read may return) are in [data and features](data-and-features.md).

## Concepts and states

- **Natural key** of a bar: `(instrument_id, timeframe, ts_open, feed)`.
- **Revision:** 0 for the first version of a key; each content change increments it.
- **Staging file** (`staging-<ULID>.parquet`): new rows visible to readers immediately. **Part file** (`part-<ULID>.parquet`): compacted, one row per key. **Revisions dataset** (`bar_revisions/`): superseded versions.
- **Manifest:** `marketdata.lake_files`, one row per file the writer created.

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| ST-001 | Lake files are ZSTD Parquet with the schema version in file metadata (`tb.schema_version`), rows sorted by `(instrument_id, ts_open)`, under `bars/feed=…/timeframe=…/year=…/month=…/`, `bar_revisions/…`, `context/kind=…/year=…/month=…/`, `ref/`, `snapshots/`. | Implemented | `services/marketdata/internal/lake` |
| ST-002 | Every file is written to a dot-prefixed temporary name, fsynced, atomically renamed, and its directory fsynced; readers only match `*.parquet` without a leading dot. Old files are removed only after their replacement is durable. | Implemented | `lake.writeAtomic`; `TestV4AtomicRenameNoPartialReads` (spike V4) |
| ST-003 | Every file the writer creates is recorded in the manifest with row count, bytes, SHA-256, and time range; removal sets `removed_at`. `Verify` reports missing, corrupt, and untracked files. | Implemented | `lake.PGManifest`, `lake.Verify`; `TestST003VerifyDetectsCorruption` |
| ST-004 | Writes are idempotent on the natural key: compaction keeps one row per key at its latest revision and drops staged rows identical in content to the stored version, so re-writing the same bars adds zero rows. | Implemented | `lake.Compact`; `TestDFI2IdempotentWritesAndCompaction` |
| ST-005 | A staged row whose content differs from the stored version becomes revision n+1; the previous version is appended to `bar_revisions`. A correction's `available_at` is never earlier than the version it corrects. | Implemented | `lake.Compact`; `TestDF007CorrectionsKeepBothVersions` |
| ST-006 | Readers deduplicate by natural key: highest revision first, then earliest `available_at`. Partitions containing exactly one part file are unique by construction and are not deduplicated (performance). | Implemented | `tbcore.data.Lake._select`, `Lake.ReadBars`; `test_reads_go_written_lake_with_revisions` |
| ST-007 | Reference data (instruments, symbol history, broker IDs, attributes, corporate actions, universe membership) is written only by marketdata to schema `refdata` and mirrored to `ref/<table>.parquet` by atomic replacement; other services read `refdata` through `refdata_reader`. | Implemented | `services/marketdata/internal/refdata`; `TestRefdataPostgresRoundTrip`, `TestSE032SchemaIsolation` |
| ST-008 | Bars are retained indefinitely. Quote/trade partitions (`date=YYYY-MM-DD`) are deleted after `TB_LAKE_TICK_RETENTION` (default 90 days). | Implemented | `lake.PurgeOlderThan`; `TestST008RetentionNeverPurgesBars` |
| ST-009 | Hot-cache keys and TTLs: `bar:last:{tf}:{id}` (7 d), `bar:ring:{tf}:{id}` (500 newest-first, 7 d), `quote:last:{id}` (1 h), `feat:{set}:{id}` (24 h), `md:watermark:{tf}` (hash per feed, no TTL); values are protobuf. `bar:last` never moves backwards in time; a correction of the newest bar replaces the ring head. Staleness is judged from `available_at`, never from key presence. | Implemented | `libs/go/cache`; `TestST009BarKeysRingAndCorrections`, `TestST009LatestBarReadLatency` |
| ST-010 | Data-quality checks on bars: duplicates, OHLC consistency, non-positive prices, negative volume, timestamps off the session grid, non-monotonic input, volume spikes (> 20× trailing 20-bar median), and gaps (runs of missing expected bars per session vs the calendar). Go and Python implementations MUST agree on `tests/fixtures/dq`. | Implemented | `services/marketdata/internal/dq`, `tbcore.dq`; `TestST010DQFlagsInjectedDefects`, `test_dq_flags_injected_defects_same_as_go` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| ST-I1 | After compaction, each partition has one part file and each natural key appears once in it. | `TestDFI2IdempotentWritesAndCompaction` |
| ST-I2 | No reader ever observes a partially written file. | Spike V4 harness (Linux CI; Mac via `make spike-v4`) |
| ST-I3 | Manifest checksums match the files on disk. | `marketdata lake verify` (`lake.Verify`) |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Backfill rerun over stored bars | Staged rows identical → dropped at compaction; zero rows added | ST-004 |
| Nightly re-pull changes a close | Revision 1 stored, revision 0 kept in `bar_revisions` | ST-005, DF-007 |
| Crash between writing a new part file and deleting the old one | Both visible; readers and the next compaction deduplicate by key and revision | ST-002, ST-006 |
| Reader lists a file that compaction deletes before it opens it | File-not-found, retried on the next listing; never partial data | ST-002 |
| Correction arrives with an earlier `available_at` than the stored version | Clamped to the stored version's `available_at` | ST-005 |
| Bars older than the cache's newest bar | `bar:last` unchanged | ST-009 |

## Open questions

- Spike V4 on macOS VirtioFS (owner run, `make spike-v4`).
- DuckLake migration trigger (concurrent writers or compaction cost at 3,000 symbols).
