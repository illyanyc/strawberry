# ADR-0032: Context data through the bus into the lake, with record keys, snapshot stamping, and per-source availability rules

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D50 (implements D20 for context sources, D4 single writer; settles the section 09 source decisions for news, macro, calendars, earnings, short interest)
- **Related:** CX-001..CX-019, DF-002, DF-010, ST-011, BR-022, SE-031, SE-040, [ADR-0020](0020-lake-layout-write-path-and-query-layer.md), [ADR-0021](0021-feature-store.md), [ADR-0022](0022-market-data-ingestion.md), [ADR-0033](0033-quarantined-news-sentiment.md), research 02 §2 (bitemporal stamping, vendor availability fields), research 04 §4–§5 (FMP gotchas, free sources), section 09 prompt

## Context

Section 09 must ingest FMP fundamentals and calendars, ALFRED vintages, FINRA short interest, SEC EDGAR filings, and Alpaca news, plus regimes, all point-in-time and joinable as of decision time (Gate 3). Constraints:

- The lake has one writer, marketdata (D4, ST-007); context-data is Python.
- Alpaca credentials are decrypted only in broker-gateway (SE-031), so news cannot be fetched directly.
- Vendors restate in place (FMP fundamentals and calendars), publish with lags (FINRA: publication ≠ settlement), or carry official vintages (ALFRED). Historical backfills cannot observe when something was first public.
- The existing `ContextRow` had no stable identity per logical record, so the reader keyed versions by `(instrument, event_time)`, which collides for, for example, two releases at 08:30.
- No API keys exist yet; tests must use recorded fixtures.

## Decision

1. **Write path.** context-data publishes `tb.context.v1.ContextEvent` on `ctx.<kind>` (stream `CONTEXT`, `Nats-Msg-Id` = hash of the version, 10-minute duplicate window). marketdata runs a batching durable consumer (`ctxsink`) that stages rows with `StageContext`, acking only after the rows are durable, and compacts context partitions on a timer (`TB_MD_CONTEXT_COMPACT_EVERY`, default 10 min). The lake keeps one writer.
2. **Record identity.** `ContextEvent.record_key` (new field) identifies the logical record: news ID, `series|period`, accession, `symbol|statement|period`, `symbol|settlement`, `country|event|time`, `symbol|report quarter`, `version|date`. Readers keep, per `(record_key, instrument)`, the version with the latest `available_at <= as_of`.
3. **Compaction semantics.** Rows are ordered per record by `available_at`. A row whose payload equals the previous kept version is dropped, but every content change is kept. So A, B, A stays three versions and a re-run adds nothing. Payloads are canonical JSON (Go `encoding/json` of the Struct). context-data hashes byte-identically (`tbcore.context.canonical`), so its change detection agrees with compaction.
4. **Snapshot stamping (CX-004).** When a record is first seen, its versions take their *rule* times (vendor or official time plus a delay). A later change to vendor-restated data is stamped at fetch time, never before the rule. Authoritative histories (ALFRED vintages, immutable filings) keep their official times even when fetched late.
5. **Availability rules** live in `tbcore.context.availability` as pure, configurable functions:
   - **News:** first publication + 60 s; live receipt is never earlier than reception.
   - **Calendar:** actual at release + 60 s; a first-seen schedule is assumed public 7 days before.
   - **Earnings:** results at BMO 09:30, DMH 16:00, AMC and unknown 20:00 New York on the report date; a first-seen date is assumed public 14 days before.
   - **Fundamentals:** FMP `acceptedDate` (New York) + 5 min.
   - **EDGAR:** API `acceptanceDateTime` as labeled + 5 min (conservative: the API value runs about 4–5 h late versus the header).
   - **FINRA:** the official publication date at 23:59 New York, falling back to settlement + 8 sessions outside the seeded schedule.
   - **ALFRED:** the vintage date at the series' release time, or the end of the day for unknown series.
6. **Sources.**
   - FMP `/stable` for the economic calendar, earnings calendar, and statements (own rate budget, default 50/min).
   - ALFRED `series/observations` with `output_type=1` (all vintages in one request).
   - FINRA Query API `consolidatedShortInterest` (public, no key).
   - SEC submissions API (contact `User-Agent` required, off until set).
   - Alpaca news through a new `BrokerMarketDataService.GetNews` RPC served by broker-gateway (allowlisted to `context-data`, which signs Python service tokens via `tbcore.svcauth`). Live news is polled every 15 s; the news WebSocket is Planned.
7. **Shared logic in `tbcore.context`:**
   - `ContextStore`: the point-in-time reads `news_for`, `sentiment_agg`, `macro`, `upcoming_events`, `earnings_window`, `regime`, `short_interest`, `filings`, and `fundamentals`.
   - Syndicated-duplicate detection.
   - Sentiment aggregates.
   - `compute_regimes` (`regime_v1`).
   - `EventBlackout` and `BlackoutStrategy`, used by backtests and by strategy-runtime fed from the lake plus `ctx.calendar`/`ctx.earnings`.
   - `context_features`, joined in `materialize(context=...)` and the online engine, so one code path serves training, backtests, and live (DF-004).
8. **Regimes** are computed by context-data from lake SPY daily bars and ALFRED VIX/VIX3M and stored as versioned rows. Index and sector ETF returns stay bar features (no separate `market_context` kind).

## Alternatives considered

| Option | Why not |
|---|---|
| context-data writes `context/` directly (second writer, disjoint directory) | Breaks D4/ST-007 without an ADR to move to DuckLake; two manifests; marketdata already has the writer, manifest, and verify |
| gRPC write API on marketdata | A new synchronous coupling; the bus already exists (`CONTEXT` stream), gives durability across marketdata restarts, and other consumers (strategy-runtime blackout) can subscribe |
| Key versions by `(instrument, event_time)` | Collides for simultaneous releases and cannot follow a rescheduled earnings date |
| Stamp every fetch at fetch time | Makes backfilled history unusable (everything "known" today); vendor and official times are the research's prescribed source (research 02 §2) |
| Stamp every fetch at vendor time | Leaks restatements and estimate drift into the past |
| Alpaca news directly from context-data | Would put the Alpaca credential in a second service (SE-031) |
| Poll FMP real-time or WebSocket for calendars | Entitlement unverified (V6); not depended on |

## Consequences

- Positive:
  - Every context datum is point-in-time and joinable as of decision time through one reader.
  - Re-runs are idempotent at both the stream (message ID) and compaction (content) levels.
  - Source outages surface as stale health and alerts (CX-019).
- Negative / risks:
  - Backfilled history embeds assumptions, which are marked in each payload's `basis` field:
    - schedule lead times;
    - FMP statements first fetched long after acceptance may already include restatements;
    - FINRA records already revised when first seen are stamped at fetch time.
  - EDGAR acceptance is conservative by about 4–5 h.
  - Live news latency is bounded by the poll interval (the section's 2 s p95 target needs the WebSocket, Planned).
  - The `CONTEXT` stream keeps 7 days, so strategy-runtime seeds schedules from the lake.
- Follow-up: news WebSocket through the market-data hub; EDGAR older-filings pagination; `options_metrics` (off, U5); DuckLake if more writers appear.

## Validation

- Go: `TestCX001*`, `TestCX002*`, `TestCX003*` (compaction, canonical digest, sink over embedded JetStream), `TestCX010NewsFixture*`.
- Python:
  - `libs/py/tbcore/tests/test_context.py`: vintages as of, the AMC surprise hidden during the session, FINRA publication, EDGAR offset, dedupe, contamination, regimes without look-ahead, `EventBlackout` in a backtest, context features in `materialize` with a future-shift check.
  - `services/context-data/ctx_tests`: recorded FINRA/EDGAR fixtures, documented-shape FMP/ALFRED fixtures, end-to-end jobs → lake → queries, idempotent re-run, staleness alert, the gRPC news client with a verified token.
  - `services/strategy-runtime/tests` (`EventBlackout` in paper).
- Opt-in real-API tests (`-m ctx_live`) pass against FINRA and EDGAR. Revisit if the SEC fixes the acceptance label (then shrink the delay), if spike V6 shows FMP report times, or if a second lake writer is needed.
