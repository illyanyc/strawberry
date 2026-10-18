# Context data

**Prefix:** `CX-`  ·  **Owners:** `context-data` (sources, stamping, scoring, publishing), `marketdata` (lake sink), `libs/py/tbcore/context` (availability rules, point-in-time reads, features, blackout), `broker-gateway` (news RPC), `strategy-runtime` (blackout in paper/live)  ·  **Related:** [data and features](data-and-features.md) (DF-), [storage](storage.md) (ST-011), [brokers](brokers.md) (BR-022), [security](security.md), [ADR-0032](../adr/0032-context-data-pipeline-and-point-in-time-rules.md), [ADR-0033](../adr/0033-quarantined-news-sentiment.md)

## Scope

Alternative and context data (section 09): news and sentiment, the economic calendar, ALFRED macro vintages, earnings schedules and results, FMP fundamentals snapshots, FINRA short interest, SEC EDGAR filing metadata, and regime labels. This domain covers when each datum becomes available, how versions are kept, how reads stay point-in-time, and how strategies block entries around events.

Market bars and quotes are in [market data](market-data.md). Physical lake rules are in [storage](storage.md). Feature definitions beyond the reference context features belong to section 10.

## Concepts and states

- **Context record**: one logical datum identified by `record_key` (see the table in CX-002). A record has one or more **versions**, each with `event_time`, `available_at`, `ingested_at`, `source`, `vintage` (macro), and a canonical JSON payload.
- **Rule time**: the vendor or official availability of a version (publication, release, acceptance, vintage date) plus a configured delay.
- **Basis** (payload field): how `available_at` was set:
  - `assumed_lead`: a schedule first seen in a backfill;
  - `timing_rule`, `release_rule`;
  - `accepted_date`;
  - `publication_schedule`, `publication_fallback`;
  - `revised_first_seen`.
- **Contaminated score**: a sentiment score for an article published before the scoring model's training cutoff.
- **Event blackout**: a window `[event - before, event + after]` in which a strategy's opening intents are dropped.

Kinds and keys:

| Kind | Source | `record_key` | `event_time` | Versions |
|---|---|---|---|---|
| `news` | Alpaca via broker-gateway (`alpaca.news`) | news ID | first publication | one (more if the vendor revises) |
| `news_sentiment` | scorer (`lexicon`, or the local model name) | `news_id|model@version` | article publication | one per model version |
| `macro` | ALFRED (`alfred`) | `SERIES|period` | period start (UTC) | one per vintage |
| `calendar` | FMP (`fmp`) | `country|event|release time` | release time | `scheduled`, then `released` |
| `earnings` | FMP | `SYMBOL|report quarter` | result availability time | `scheduled`, then `reported`; date changes |
| `fundamentals` | FMP | `SYMBOL|statement|fiscal period` | period end | one per observed content |
| `short_interest` | FINRA (`finra`) | `SYMBOL|settlement date` | settlement 16:00 New York | one (revisions observed later) |
| `filing` | SEC EDGAR (`edgar`) | accession number | conservative acceptance | one (immutable) |
| `regime` | context-data | `regime_v1|date` | SPY session close | one |

## Rules

Defaults are configuration (`TB_CTX_*` in context-data, mirrored by `tbcore.context.availability.AvailabilityConfig`).

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| CX-001 | Every context version MUST carry `kind`, `source`, `record_key`, `event_time`, and `available_at`. The sink MUST terminate (never retry) an event missing any of them. | Implemented | `services/marketdata/internal/lake/context.go` (`ContextRowsFromEvent`), `internal/ctxsink`; `TestCX001InvalidContextEventsRejected`, `TestCX003SinkPersistsContextFromBus` |
| CX-002 | Point-in-time reads MUST return, per `(record_key, instrument_id)`, the version with the latest `available_at <= as_of` (ties: later `ingested_at`). They MUST NOT key on `event_time`; rows written before `record_key` existed fall back to it. | Implemented | `tbcore.context.ContextStore.latest`, `tbcore.data.Lake.context`; `test_cx002_go_written_context_fixture_vintages_as_of`, `test_cx006_macro_as_of_returns_vintage_known_then`, `TestCX002OneRowPerInstrumentAndCanonicalPayload` |
| CX-003 | Context events reach the lake only through marketdata (D4). Producers MUST set `Nats-Msg-Id` to the version hash. Compaction MUST drop a row whose payload equals the previous kept version of the same record (ordered by `available_at`) and MUST keep every content change (A, B, A stays three versions). Payload JSON MUST be canonical and byte-identical between Go and Python. | Implemented | `lake.CompactContext`, `ctxsink.Sink`, `tbcore.context.canonical`, `records.Out.msg_id`; `TestCX003CompactionKeepsChangesDropsRepeats`, `TestCX003CanonicalPayloadDigest`, `test_cx003_canonical_json_matches_go` |
| CX-004 | Snapshot stamping:<br>• A record seen for the first time MUST take its rule times.<br>• Unchanged content MUST NOT create a version.<br>• Changed vendor-restated content (FMP calendars, estimates, statements, news) MUST be stamped at the fetch time, never earlier than its rule time.<br>• Authoritative histories (ALFRED vintages, EDGAR filings, sentiment, regimes) keep their rule times. | Implemented | `tbcore.context.availability.stamp`, `tb_context_data.records.Versioner`; `test_cx004_stamp_*`, `test_cx008_fundamentals_first_seen_accepted_date_restatement_at_fetch`, `test_cx006_alfred_vintages_authoritative_times`, `test_gate3_context_jobs_land_point_in_time_and_rerun_adds_nothing` |
| CX-005 | FINRA short interest MUST be available at its publication date, never its settlement date:<br>• Default: 23:59 New York (`TB_CTX_FINRA_PUBLISH_TIME`), taken from the official schedule in `tbcore/context/finra_short_interest_schedule.csv`.<br>• Outside the schedule: settlement + 8 XNYS sessions (`TB_CTX_FINRA_FALLBACK_SESSIONS`); the published schedule uses 7.<br>• A record already revised when first seen MUST be stamped at the fetch time.<br>• Settlement dates are the 15th and the month end, each moved back to the previous session. | Implemented | `tbcore.context.availability.finra_*`, `tb_context_data.sources.finra`; `test_cx005_*` |
| CX-006 | Macro and calendar:<br>• An ALFRED vintage is available at the series' official release time (New York) on its `realtime_start`; unknown series at 23:59 (`TB_CTX_MACRO_DEFAULT_RELEASE_TIME`).<br>• Every vintage is stored (DF-010).<br>• An economic-calendar actual and its surprise (actual − consensus) are available at the release time + 60 s (`TB_CTX_ECON_ACTUAL_DELAY_S`).<br>• A first-seen schedule or consensus is assumed public 7 days earlier (`TB_CTX_ECON_SCHEDULE_LEAD_DAYS`). | Implemented | `availability.macro_available_at`, `econ_*`; `sources.alfred`, `sources.fmp.economic_records`; `test_cx006_*` |
| CX-007 | Earnings:<br>• Results (actuals, surprise) MUST be available at the report date's timing time in New York: BMO 09:30, DMH 16:00, AMC 20:00, and unknown timing treated as AMC (`TB_CTX_EARNINGS_*_TIME`). An AMC surprise is therefore never visible during that day's regular session.<br>• A first-seen report date is assumed public 14 days earlier (`TB_CTX_EARNINGS_SCHEDULE_LEAD_DAYS`).<br>• A date change is a new version stamped at the fetch time. | Implemented | `availability.earnings_*`, `ContextStore.earnings_window`, `sources.fmp.earnings_records`; `test_cx007_amc_surprise_not_visible_during_report_day_session`, `test_cx007_bmo_surprise_visible_at_open`, `test_cx007_rescheduled_report_is_a_new_version`, `test_cx007_fmp_*` |
| CX-008 | FMP statements MUST be placed by `acceptedDate` (New York) + 300 s (`TB_CTX_FUNDAMENTALS_DELAY_S`) on first sight. Rows without `acceptedDate` MUST be skipped. Restatements follow CX-004. | Implemented | `availability.fundamentals_available_at`, `sources.fmp.statement_records`; `test_cx008_*` |
| CX-009 | EDGAR filings:<br>• Available at the submissions API `acceptanceDateTime` parsed as labeled (UTC) + 300 s (`TB_CTX_EDGAR_DELAY_S`); the API value is observed 4–5 h later than the true acceptance, so this is conservative.<br>• Only metadata is stored.<br>• The source MUST NOT run without `TB_CTX_SEC_USER_AGENT` (SEC fair-access policy). | Implemented | `availability.edgar_available_at`, `sources.edgar`, `app.build_sources`; `test_cx009_*`, `test_disabled_sources_without_credentials` |
| CX-010 | News:<br>• Available at first publication (`created_at`) + 60 s (`TB_CTX_NEWS_DELAY_S`). An article received within 300 s of publication (`TB_CTX_NEWS_LIVE_WINDOW_SECONDS`) MUST NOT be available before it was received. `updated_at` MUST NOT be used for availability.<br>• Tickers resolve to `instrument_id` as of publication.<br>• Bodies are stored only when `TB_CTX_NEWS_STORE_BODY=true` (licensing).<br>• News is fetched only through broker-gateway (SE-031). | Implemented | `availability.news_available_at`, `sources.news.NewsPipeline`; `test_cx010_*`, `TestCX010NewsFixtureDecodesAndOmitsBodyUnlessRequested` |
| CX-011 | News and filing text is untrusted and MUST be stored only after sanitization:<br>• markup, scripts, ANSI, and control/bidi characters removed;<br>• NFKC normalization;<br>• limits of headline 512, summary 2,000, body 20,000 characters;<br>• `untrusted: true` on the record.<br>It is never executed and never passed to the privileged assistant (section 12). | Implemented | `tb_context_data.sanitize.clean`; `test_cx010_to_cx013_news_pipeline_backfill` |
| CX-012 | Each sentiment score MUST store the model name, version, scorer kind, training cutoff, input hash, and `contaminated`. Live scores are available when scored; backfilled scores at the article's availability + 2 s (`TB_CTX_SENTIMENT_LATENCY_S`). A fixed model version MUST return the same score for the same input. | Implemented | `sentiment.QuarantinedScorer`, `sources.news`; `test_cx012_*`, `test_cx014_valid_reply_schema_checked_no_tools_and_cached` |
| CX-013 | Syndicated duplicates (the same headline normalized, or token Jaccard ≥ 0.8, for the same instrument within 24 h) MUST be marked `duplicate_of` the first article. Counts and means use originals only. | Implemented | `tbcore.context.news`; `test_cx013_syndicated_duplicates_detected`, `test_cx010_to_cx013_news_pipeline_backfill` |
| CX-014 | Scoring runs only through the quarantine:<br>• sanitized, delimited input;<br>• no tools;<br>• an allowlisted local endpoint with no redirects;<br>• schema-validated output with extra keys rejected and no retry with a modified prompt;<br>• every call logged.<br>A local model MUST declare its version and training cutoff. Scores of articles published before the cutoff are `contaminated` and MUST be excluded from features and aggregates unless explicitly requested. | Implemented | `tb_context_data.sentiment`, `ContextStore.sentiment`, `context_features`; `test_cx014_*`, `test_cx012_sentiment_aggregate_point_in_time_and_contamination_excluded` |
| CX-015 | Economic events MUST be categorized (CPI, PPI, FOMC, NFP, UNEMPLOYMENT, PCE, GDP, RETAIL_SALES, JOBLESS_CLAIMS, ISM) by `tbcore.context.events.categorize` for blackouts and features. | Implemented | `tbcore.context.events`; `test_cx015_categorize` |
| CX-016 | `EventBlackout(kinds, before, after, min_impact, countries)` MUST drop a strategy's opening intents while a matching event known at the decision time (`available_at <= now`) is within the window. Closing intents are never blocked. Missing impact counts as high. The same `BlackoutStrategy` wrapper runs in backtests (lake-backed source) and paper/live (strategy-runtime, from the lake plus `ctx.calendar`/`ctx.earnings`; registry key `runtime.blackout`). | Implemented | `tbcore.context.events`, `services/strategy-runtime/.../runner.py`; `test_cx016_event_blackout_blocks_entries_around_cpi_in_backtest`, `test_cx016_blackout_is_point_in_time_and_never_blocks_closes`, `test_cx016_event_blackout_blocks_cpi_entries_in_paper` |
| CX-017 | Regime labels (`regime_v1`) MUST use only data available at each row's time:<br>• inputs: SPY daily bars, plus VIX/VIX3M joined backward as of the bar's availability;<br>• rolling windows look back only;<br>• vol percentile over 252 sessions;<br>• 20-day efficiency ratio with the 200-day mean for trend;<br>• stress when VIX/VIX3M > 1 or the VIX percentile ≥ 0.9;<br>• rows without enough history are `unknown`.<br>Any rule change bumps the version. | Implemented | `tbcore.context.regime`, `tb_context_data.app.regime_records`; `test_cx017_regimes_use_only_past_data`, `test_regime_rows_from_lake_bars_and_vix` |
| CX-018 | Context features (`tbcore.context.features`, version 1) MUST be computed by one function for `materialize(context=...)` and the online engine, using the row's `available_at` as the decision time. Snapshot IDs MUST include the context spec hash and input files. | Implemented | `context_features`, `tbcore.features.store`; `test_cx018_context_features_in_materialize_and_future_rows_ignored`, `test_cx007_amc_surprise_not_visible_during_report_day_session` |
| CX-019 | Freshness:<br>• A job without a successful run for `every × TB_CTX_STALE_FACTOR` (default 3) MUST report `stale` on `sys.health.ctx.<job>` and `tb_context_source_stale`, and raise one warning alert (`ctx-stale-<job>`, runbook `context-data.md`).<br>• One symbol's failure MUST NOT fail a job unless every symbol fails.<br>• A source not deployed (for example no Alpaca data account behind broker-gateway) reports `unavailable` without alerting.<br>• A source without credentials is not scheduled and is listed as disabled. | Implemented | `tb_context_data.jobs.Engine`, `app.per_symbol`, `app.build_sources`; `test_cx019_stale_source_alerts_once_and_recovers`, `test_unavailable_source_reports_without_alerting`, `test_disabled_sources_without_credentials`; alert `ContextSourceStale` |

## Invariants

| ID | Invariant | Checked by |
|---|---|---|
| CX-I1 | No context read with `as_of = t` returns a version with `available_at > t`. | `ContextStore.versions` filter; future-shift case in `test_cx018_*`; `test_cx014_store_excludes_contaminated_sentiment` (not yet scored) |
| CX-I2 | Re-ingesting the same vendor data adds no versions. | `test_gate3_context_jobs_land_point_in_time_and_rerun_adds_nothing`, `TestCX003CompactionKeepsChangesDropsRepeats` |
| CX-I3 | A context feature value is the same whether computed offline or online for the same decision time. | Single function (`context_features`) in both paths (DF-004) |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| Nonfarm payrolls revised twice | `macro("PAYEMS", as_of)` returns the first release until 08:30 on the revision day, then the revision | CX-006, CX-002 |
| AAPL reports AMC at 16:30; decision at 15:45 the same day | No surprise visible; `days_to_earnings` < 1; visible from 20:00 | CX-007 |
| Timing unknown (FMP gives none until spike V6) | Treated as AMC | CX-007 |
| Earnings date moves from the 28th to the 29th | New version stamped when observed; earlier decisions still see the 28th | CX-007, CX-004 |
| FMP restates last quarter's net income | New version at fetch time; history before then keeps the original snapshot | CX-008, CX-004 |
| Backfill of statements fetched years after acceptance | Placed at `acceptedDate`, but values may include later restatements (`basis: accepted_date`); prefer a PIT vendor for honest fundamentals research | CX-008 |
| Short interest settled 04-15, published 04-24 | Invisible on 04-20; visible from 04-24 23:59 New York | CX-005 |
| FINRA returns an empty body for a date with no data | Treated as zero rows | CX-005 |
| EDGAR Form 4 accepted 18:30 New York | API says 02:30Z next day; available 02:35Z (conservative) | CX-009 |
| Syndicated copy of a story two minutes later | Stored with `duplicate_of`; scored but excluded from counts | CX-013 |
| Headline contains "IGNORE ALL PREVIOUS INSTRUCTIONS…" | Stored sanitized; the lexicon scores the content; a local model's free-text reply is rejected by the schema | CX-011, CX-014 |
| Local model trained through 2025-06; article from 2024 | Score stored with `contaminated: true`; excluded from features | CX-014 |
| CPI at 08:30, ORB breakout decided at 10:01, blackout after 120 min | Entry dropped; the next non-event day trades normally | CX-016 |
| CPI schedule published after the decision | Not used (no look-ahead); entry allowed | CX-016 |
| broker-gateway runs only the simulator | News reports `unavailable`, retried hourly, no alert | CX-019 |
| marketdata down for a day | Events wait in stream `CONTEXT` (7 days) and are written on restart | CX-003 |

## Source catalog

Cost, latency, history, and licence for every enabled source. The data budget is `ASSUMED (U5)`: free sources plus the existing FMP plan.

| Source | Datasets | Cost | Latency to platform | History | Licence / terms | Needs |
|---|---|---|---|---|---|---|
| Alpaca news (Benzinga) via broker-gateway | `news`, `news_sentiment` | Included with Alpaca market data | Poll every 15 s (`TB_CTX_NEWS_POLL_SECONDS`); WebSocket Planned | Since 2015 | Alpaca market-data terms; Benzinga content: headlines/summaries for own use; bodies off by default | Alpaca data account in broker-gateway |
| FMP `/stable` | `calendar`, `earnings`, `fundamentals` | Existing plan (Starter assumed; Premium for quarterly fundamentals) | Calendar 30 min, earnings 1 h, statements 6 h | 5 years (Starter), 30+ (Premium) | Personal plan: no display to other users without a licence (research 04 §4.3) | `fmp_api_key`; shares the plan with marketdata (keep the sum of `TB_FMP_RATE_PER_MIN` + `TB_CTX_FMP_RATE_PER_MIN` ≤ 80 % of the plan) |
| FRED/ALFRED | `macro` (incl. VIXCLS, VXVCLS) | Free | Hourly; vintages at official release times | Full vintage history | FRED terms of use (attribution; some series third-party copyrighted) | `fred_api_key` (free registration) |
| FINRA Query API | `short_interest` | Free (public dataset, no key) | Every 6 h; data twice a month | Since 2020+ via API | FINRA data terms (non-commercial use of public data) | None |
| SEC EDGAR submissions | `filing` | Free | Every 15 min | Latest 1,000 filings per company (older pages Planned) | SEC fair access: ≤ 10 req/s, descriptive User-Agent with contact | `TB_CTX_SEC_USER_AGENT` |
| Local scoring model (optional) | `news_sentiment` | Free (runs on the Mac) | Seconds | n/a | Model licence (e.g. FinBERT Apache-2.0); declared training cutoff | `TB_CTX_SENTIMENT_SCORER=local_llm`, `TB_CTX_LLM_*` |
| Options-derived signals, borrow rates | — | Paid (ThetaData from $40/mo) | — | — | — | Off: `INPUT PENDING (U5)` |

## Open questions

- Spike V6: whether FMP `/stable` returns report timing (`TB_CTX_FMP_REPORT_TIMES`) — tool `make spike-v6` needs the owner's key.
- News WebSocket through the market-data hub to meet the 2 s p95 target (Planned).
- Measured scoring latency to replace the configured 2 s.
