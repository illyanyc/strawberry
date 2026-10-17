---
title: "Section 05 – Market Data Ingestion and Streaming"
phase: "1"
depends_on: [00, 01, 03, 04]
updated: 2026-10-17
---

# Section 05: Market Data Ingestion and Streaming

## Role

You are a market-data engineer. You know that the hard parts of market data are not downloading it but making it complete, correct, timely, and honest about when each value became known.

## Context

The `marketdata` service backfills historical bars and streams live bars, quotes, and trades for the configured universe. It writes to the analytical lake through section 03's writer, keeps the hot cache current, and publishes to the event bus for the feature engine, strategies, monitoring, and the UI. Sources are the brokers (via section 04's market-data interface) and optional vendors. Non-negotiables N2 (timestamps and availability) and N5 (stale data means no new risk) apply.

## Scope

In scope:
- Historical backfill jobs (bars at 1m, 5m, 15m, daily; optional trades/quotes) with resumable progress.
- Live streaming of bars, quotes, and trades; subscription management for the active universe.
- Bar building/resampling (higher timeframes from 1m; optional bars from trades).
- Gap detection and repair, late and corrected data handling, deduplication.
- Feed freshness and quality metrics; stale-data signals for execution.
- Corporate action and instrument reference data ingestion from brokers/vendors (written to section 03's tables).
- Pluggable vendor adapters (Polygon, Databento, Tiingo, etc.) behind one interface.

Out of scope:
- News, macro, earnings, and sentiment (section 09).
- Storage engine internals (section 03).
- Broker session management (section 04).

## Inputs and dependencies

- Section 03: writer API, instrument master, calendar, cache key conventions.
- Section 04: `GetHistoricalBars`, `StreamBars/Quotes/Trades`, `GetInstrumentInfo`.
- Section 01: orchestrator for scheduled backfills; NATS streams.
- `INPUT PENDING (U3)`: universe. Default dev list of ~100 liquid symbols plus SPY, QQQ, IWM, sector ETFs, and VIX-related proxies.
- `INPUT PENDING (U5)`: data budget. Default Alpaca IEX (free) for live, which covers only a fraction of consolidated volume; flag in UI and metadata that IEX bars differ from SIP bars. Paid SIP or a vendor is a config switch.
- `INPUT PENDING (U8)`: latency target. Default bar-close-to-bus p99 under 1.5 s with broker bar streams.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Primary historical source | Alpaca historical API (raw, unadjusted, consolidated where plan allows); IBKR for cross-checks; vendor for deep or survivorship-free history if budget allows | `DECISION PENDING (research §6)` (D20) |
| Live bar source | Broker bar stream (Alpaca minute bars); build higher timeframes locally | `DECISION PENDING (research §6)` |
| Survivorship-free history | Requires a vendor that includes delisted symbols (e.g., Norgate, Databento, Polygon). Default: Alpaca where available, with a documented gap | `DECISION PENDING (research §4, §6)` |
| Language | Go (D1) | `DECISION PENDING (research §7)` |
| Trades/quotes storage | Off by default; enable per universe subset | Depends on U5 and research §3 |

## Interfaces and contracts to expose

- Bus: `md.bar.<tf>.<instrument_id>` (final bars only, with `is_final=true`), `md.bar.partial.<tf>.<instrument_id>` (optional live partial bars for the UI, never for strategies), `md.quote.*`, `md.trade.*`, `md.status` (feed connectivity, per-feed watermark), `alert.*` for gaps and staleness.
- Cache: updates `bar:last`, `bar:ring`, `quote:last`, `md:watermark` keys from section 03.
- gRPC `MarketDataService`: `Backfill(request{instruments, timeframes, start, end, source}) -> job_id`, `GetBackfillStatus(job_id)`, `SetUniverse(name)`, `GetFeedStatus() -> [FeedStatus{feed, connected, last_message_at, lag_ms, symbols_stale[]}]`, `IsFresh(instrument_id, timeframe, max_age) -> bool` (used by execution's pre-trade checks).
- Vendor adapter interface: `historical_bars`, `stream_bars`, `corporate_actions`, `instrument_list(include_delisted)`, `capabilities`.
- Metrics: messages/s per feed, bar-close-to-publish latency histogram, gaps detected/repaired, stale symbols, reconnect count, backfill progress.

## Functional requirements

### Backfill
- [ ] Backfill jobs are resumable, chunked by symbol and date range, rate-limit aware (per source), and idempotent (rerun produces no duplicates).
- [ ] Request raw (unadjusted) bars; ingest corporate actions separately (D27).
- [ ] For each bar, set `available_at = ts_close + publication_delay(source, timeframe)` for historical data; the default delay is configurable per source and must be at least the observed live delay so backtests are not more optimistic than live.
- [ ] Include delisted instruments where the source allows; record per-source coverage gaps in a metadata table.
- [ ] Validate each chunk with `dq_check` (section 03); write a backfill report (rows, gaps vs calendar, anomalies).
- [ ] Nightly incremental job pulls the latest session, repairs gaps, and replaces provisional live bars with source-final bars (retaining both versions per section 03's correction policy).

### Streaming
- [ ] Subscribe to the active universe over one multiplexed connection per feed (section 04 enforces broker connection limits).
- [ ] Publish final bars only after the bar interval closes and either the broker's final bar arrives or a configured grace period elapses (documented rule; bars built from trades must handle late trades).
- [ ] Build 5m, 15m, 30m, 1h, and daily bars from 1m bars with session-aware alignment (half days, no bars across session boundaries, extended-hours bars flagged with a `session` field).
- [ ] Record `available_at` as the actual local receive time of the final bar (live) so live timing is measured, not assumed.
- [ ] Heartbeat and staleness detection per feed and per symbol (no update for N intervals during market hours ⇒ stale); publish status and expose `IsFresh`.
- [ ] Reconnect with backoff; after reconnect, backfill the missed interval from REST and publish repaired bars flagged `repaired=true`.
- [ ] Late or corrected bars after publication emit a correction event; strategies ignore corrections for already-made decisions (they are logged for live-vs-backtest analysis).
- [ ] Universe changes add/remove subscriptions without restarting.

### Reference data
- [ ] Sync tradable instruments and attributes (tradable, shortable, easy-to-borrow, fractionable) daily from each broker into the instrument master with validity ranges.
- [ ] Ingest corporate actions (splits, dividends, symbol changes, delistings) daily; emit `CorporateAction` events to the bus for the ledger (section 06).

## Non-functional requirements

- **Performance**: handle at least 3,000 symbols of 1m bars and 500 symbols of quotes on the reference machine without lag growth; bar-close-to-bus p99 within the U8 target.
- **Reliability**: no silent gaps; every gap is detected and either repaired or reported; backpressure (bounded queues) with drop policy only for partial bars/quotes destined for the UI, never for final bars.
- **Correctness**: bar alignment and session handling tested across DST changes, half days, and holidays.
- **Security**: no credentials in this service (it calls section 04); vendor API keys via the secrets interface.
- **Tests**: replay tests from recorded broker streams (including disconnects and duplicates); resampling golden tests; DST and half-day calendar tests; backfill idempotency test; load test at target symbol counts.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/market-data.md` (bar finality, availability, gap repair), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Backfill of the dev universe (1m, 5m, 15m, daily; default two years or source maximum) completes with a report; rerun adds zero rows.
- [ ] Live session: final bars for all subscribed symbols arrive on the bus and in the cache within the latency target; metrics recorded in Grafana.
- [ ] Killing the stream connection for 3 minutes mid-session results in automatic reconnect, gap repair, and `repaired` bars; no duplicates in the lake.
- [ ] Resampled 5m/15m bars equal source 5m/15m bars (where available) within documented tolerance on a sample of sessions; differences are explained (feed, late trades).
- [ ] `IsFresh` returns false for a symbol whose feed is stalled, and execution can consume it (stub test until section 07 lands).
- [ ] IEX vs SIP feed is recorded on every bar and shown in data views.

## Pitfalls to avoid

- Publishing partial bars to strategies; this is a classic look-ahead and live/backtest mismatch.
- Assuming historical bars were available exactly at bar close; live bars arrive later, and backtests must use realistic availability.
- Mixing feeds (IEX live, SIP history) without recording it; models trained on SIP volume behave differently on IEX volume.
- Silent gaps after reconnects, or repaired bars overwriting the version that live decisions actually used.
- Building daily bars from extended-hours data inconsistently with the source's daily bars.
- Hitting rate/pacing limits during backfill and getting throttled during market hours; schedule heavy backfills off-hours and reserve capacity for live operations.
- Adjusting prices in the ingestion layer.
