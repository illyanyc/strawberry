# Market data

**Prefix:** `MD-`  ·  **Owners:** `marketdata` (backfill, streaming, bar building, gap repair, reconciliation, reference-data sync), `broker-gateway` (vendor connections)  ·  **Related:** [storage](storage.md) (ST-), [data and features](data-and-features.md) (DF-), [brokers](brokers.md) (BR-015), D20, D27

## Scope

Ingestion of bars, quotes, trades, corporate actions, and instrument reference data from Alpaca (through broker-gateway) and FMP: finality and availability timestamps, session alignment, gap detection and repair, rate limits, source health, and cross-source reconciliation. Storage mechanics are in [storage](storage.md); point-in-time read semantics in [data and features](data-and-features.md).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| MD-004 | Bars are aligned to the session start and never cross a session boundary; a half day's last bar ends at the early close; extended-hours bars carry `session = pre/post` and are never merged with regular bars. Daily bars have `ts_open` = regular open and `ts_close` = regular close. | Implemented (calendar) | `libs/go/calendar` (`BarOpens`, `BarClose`), `tbcore.data.calendar`; `TestDF011DSTAndHalfDays` |
| MD-005 | Every service uses the XNYS session table generated from `exchange_calendars` (`scripts/gen_calendar.py`), embedded in Go and shipped in tbcore, mirrored to `refdata.sessions`; the Go and Python copies MUST be identical. | Implemented | `libs/go/calendar`, `tbcore.data.calendar`, `refdata.MirrorCalendar`; `test_calendar_matches_go_copy_and_handles_dst_half_days`, `test_calendar_is_fresh` |
| MD-011 | A corporate action's `available_at` is its vendor declaration time when known, otherwise 00:00 New York on its ex-date, and never later than when the platform first fetched it; re-fetching keeps the earliest value. | Implemented | `services/marketdata/internal/refdata` (`AvailableAt`, `UpsertAction`); `TestMD011CorporateActionAvailability` |
| MD-012 | When sources disagree on a corporate action (type, ex-date, or ratio), the action is marked `disagreed` and adjusted reads for that instrument fail (`AdjustmentBlockedError`) until resolved; raw reads still work. | Implemented (read side) | `tbcore.data.lake.apply_adjustment`; `test_md012_disagreement_blocks_adjustment` |

## Open questions

- Publication delay defaults per feed (observed live delay): section 05.
