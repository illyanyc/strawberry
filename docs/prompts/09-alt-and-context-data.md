---
title: "Section 09 – Alternative and Context Data"
phase: "3 (parallel with section 08)"
depends_on: [00, 01, 02, 03]
updated: 2026-10-17
---

# Section 09: Alternative and Context Data

## Role

You are a data engineer for alternative data in a quant shop. You know that most alt-data "alpha" disappears once timestamps are fixed, so you obsess over when each datum was actually available and keep every revision.

## Context

The `context-data` service ingests news and sentiment, the economic calendar and macro series, earnings dates and surprises, sector/index context, volatility regime indicators, and optional options-derived and short-interest data. Each source lands in the lake as point-in-time `ContextEvent`s and feeds features (section 10) and strategy filters (e.g., "no new positions 5 minutes before CPI"). Non-negotiable N2 is the main risk: alt data is where look-ahead leaks hide. N1 matters because news text is untrusted input (prompt injection into the assistant).

## Scope

In scope:
- Source adapters, schedules, and point-in-time storage for:
  - News headlines/bodies with symbols (default: Alpaca news API, which is sourced from Benzinga; verify).
  - Sentiment scoring of news (FinBERT baseline; optional LLM scoring).
  - Macro series with vintages (FRED/ALFRED) and the economic calendar (release times, consensus, actual).
  - Earnings calendar (date, before/after market), EPS/revenue estimates and actuals, surprises.
  - Market context: index and sector ETF returns, breadth, VIX and term structure proxies, rates.
  - Optional: options-derived signals (IV, skew, put/call), short interest, borrow rates. `INPUT PENDING (U5)`.
- Regime labels (trend vs chop, high vs low volatility) computed point-in-time.
- Event-window flags for strategies (pre-earnings, macro release windows, halts).
- Source catalog with cost, coverage, latency, and license notes.

Out of scope:
- Market bars/quotes (section 05).
- Turning context into model features (section 10 owns feature definitions; this section provides clean inputs and reference features).

## Inputs and dependencies

- Section 03: writer API, `ContextEvent` schema, as-of joins, instrument master for symbol mapping.
- Section 02: secrets for vendor keys; sanitization rules for untrusted text.
- Section 01: orchestrator schedules, optional GPU profile for sentiment models.
- `INPUT PENDING (U5)`: data budget. Default free/cheap sources only.
- `INPUT PENDING (U4)`: GPU for transformer sentiment scoring; FinBERT runs acceptably on CPU for headline volumes.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| News source | Alpaca news API (REST + websocket); Benzinga/Polygon/others if budget allows | `DECISION PENDING (research §3, §6)` |
| Sentiment model | FinBERT baseline; LLM scoring (local or API, D26) as an experiment, never as the only score | `DECISION PENDING (research §2, §3)` |
| Macro source | FRED for series, ALFRED for vintages; calendar from a free source with release times | `DECISION PENDING (research §3)` |
| Earnings source | Broker/vendor calendar; estimates from a paid source if budget allows | `DECISION PENDING (research §3, §6)` |
| Options and short interest | Off by default | `DECISION PENDING (research §3)` + U5 |
| Language | Python | High confidence |

## Interfaces and contracts to expose

- Lake datasets (via section 03 writer): `news`, `news_sentiment`, `macro_observations` (series_id, period, value, vintage_date, `available_at` = release time), `econ_calendar` (event, scheduled_time, consensus, actual, `available_at`), `earnings` (instrument_id, period, report_time, timing BMO/AMC, estimate, actual, surprise, `available_at`), `market_context`, `regimes`, optional `options_metrics`, `short_interest` (with publication date as `available_at`).
- Bus: `ctx.news` (new headlines with sentiment), `ctx.macro`, `ctx.calendar` (upcoming events), `ctx.earnings`, `ctx.regime`.
- Query helpers in `tbcore.context`: `news_for(instrument, start, end, as_of)`, `sentiment_agg(instrument, window, as_of)`, `macro(series, as_of)` (returns the vintage known at `as_of`), `upcoming_events(as_of, horizon)`, `earnings_window(instrument, as_of)`, `regime(as_of)`.
- Strategy filters API: `EventBlackout(kinds=[...], before=..., after=...)` usable in strategies and backtests.
- Source catalog file (`docs/business-logic/context-data.md` + config): source, cost, latency, history depth, license, terms of use.

## Functional requirements

- [ ] Every record stores `event_time`, `available_at`, `ingested_at`, `source`, and, where relevant, `vintage`. Historical backfills set `available_at` conservatively (publication time plus a measured or configured delay; never the event period date).
- [ ] News: dedupe syndicated duplicates (same story across sources), map tickers to `instrument_id` as of the publication date, store headline and (where licensed) body; mark text as untrusted.
- [ ] Sentiment: `available_at` of a score = scoring time (live) or publication time + scoring latency (backfill); store model name and version with each score; aggregate features (count, mean, decay-weighted sentiment, novelty) computed point-in-time.
- [ ] LLM scoring path (optional): sanitized input (strip markup, length-limit), structured JSON output validated against a schema, no tool access, prompts and outputs logged; results treated as just another feature.
- [ ] Macro: store all vintages; queries return the value known as of the decision time; release times in UTC from the official schedule.
- [ ] Economic calendar: scheduled events with importance; actuals and surprises (actual − consensus) recorded at release time.
- [ ] Earnings: dates with BMO/AMC timing; date changes recorded as new versions; surprises available only after the report time.
- [ ] Market context: SPY/QQQ/IWM, sector ETFs, VIX proxies, rates; derived breadth where computable from the universe.
- [ ] Regimes: point-in-time regime labels computed only from past data (e.g., rolling realized vol percentiles, trend strength), versioned.
- [ ] Data quality: freshness checks per source, coverage reports, alerting on source outages (section 13).
- [ ] Respect vendor licenses and rate limits; source catalog lists them.

## Non-functional requirements

- **Latency**: live news to `ctx.news` with sentiment under 2 s p95 (FinBERT on CPU for headlines); macro actuals within 10 s of availability from the source.
- **Correctness**: point-in-time queries tested with known revised macro series and rescheduled earnings.
- **Security**: news and any external text are untrusted; never executed, never passed to the assistant without the sanitization and isolation rules in section 12; vendor keys through secrets.
- **Tests**: vintage tests (as-of returns the earlier vintage), earnings timing tests (AMC report not visible during that day's session), dedupe tests, sentiment determinism tests (fixed model version ⇒ same score).

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/context-data.md` (sources, availability rules, catalog), `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] News, sentiment, macro (with vintages), calendar, earnings, market context, and regimes are backfilled for the dev universe and period, and stream live.
- [ ] `macro(series, as_of)` returns the vintage known at `as_of` for a revised series (e.g., nonfarm payrolls) in tests.
- [ ] An AMC earnings surprise is not visible to a backtest decision during that day's regular session.
- [ ] `EventBlackout` blocks entries around a CPI release in a backtest and in paper.
- [ ] Source catalog documents cost, latency, history, and license for every enabled source.

## Pitfalls to avoid

- Using the latest revised macro values for historical decisions.
- Timestamping news by its "event date" or by a vendor's later-updated timestamp instead of first publication/receipt.
- Assuming earnings "date" means the information was available at the open of that date.
- Backfilled sentiment scores computed with a model trained on data after the backtest period (model leakage); record the sentiment model's training cutoff and exclude periods before it from honest evaluation or use time-appropriate models.
- Treating news text as trusted input to an LLM with tools.
- Paying for alt data before the free baseline proves value in honest validation.
- Short interest and similar datasets published with a lag: `available_at` is the publication date, not the settlement date.
