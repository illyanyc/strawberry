# context-data

Point-in-time alternative and context data (section 09). Covers FMP economic calendar, earnings, and statement snapshots; ALFRED macro vintages; FINRA short interest; SEC EDGAR filing metadata; and Alpaca news with quarantined sentiment, plus regime labels. Everything is published as versions on `ctx.<kind>`, and marketdata writes them to the lake.

**Status:** Phase 3 built (all sources behind config; real APIs need the owner's keys)  ·  **Language/stack:** Python 3.12 on `tbcore`, httpx, nats-py, grpcio ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1; [ADR-0032](../../docs/adr/0032-context-data-pipeline-and-point-in-time-rules.md), D50; [ADR-0033](../../docs/adr/0033-quarantined-news-sentiment.md), D51)

## Responsibilities
- Owns: source adapters and schedules, snapshot stamping (CX-004), untrusted-text sanitization (CX-011), sentiment scoring through the quarantine (CX-014), regime rows (CX-017), source freshness and health (CX-019), the source catalog in [context-data.md](../../docs/business-logic/context-data.md).
- Does not own:
  - the lake (marketdata writes it from the bus, D4);
  - availability rules, reads, features, and blackouts (`libs/py/tbcore/context`);
  - Alpaca credentials (broker-gateway serves `GetNews`).

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| out | NATS | `ctx.<kind>` (stream `CONTEXT`, `Nats-Msg-Id` = version hash): `news`, `news_sentiment`, `macro`, `calendar`, `earnings`, `fundamentals`, `short_interest`, `filing`, `regime` | `tb.context.v1.ContextEvent`, CX-001..CX-003 |
| out | NATS | `sys.health.ctx.<job>` (`tb.marketdata.v1.SourceHealth`), `alert.warning` (`ctx-stale-<job>`) | CX-019 |
| out | gRPC | broker-gateway `BrokerMarketDataService.GetNews` (service token, audience `broker-gateway`) | BR-022 |
| out | HTTPS | FMP `/stable`, FRED/ALFRED, FINRA Query API, SEC `data.sec.gov` + `www.sec.gov` | CX-005..CX-009 |
| out | HTTP (local) | optional quarantined model, Ollama-compatible `/api/chat` on an allowlisted host | CX-014 |
| in | filesystem | lake (read-only): `ref/` for ticker resolution and universes, stored versions for stamping, SPY daily bars for regimes | CX-002, CX-004 |
| in | HTTP (internal) | ops endpoints `:8081` (readiness: bus) | OP-010 |

## Business rules enforced here
- `CX-004` → `records.Versioner`; `CX-005` → `sources/finra.py`; `CX-006` → `sources/alfred.py`, `sources/fmp.py` (`economic_records`); `CX-007`, `CX-008` → `sources/fmp.py`; `CX-009` → `sources/edgar.py`, `app.build_sources`.
- `CX-010`..`CX-013` → `sources/news.py`, `sanitize.py`; `CX-012`, `CX-014` → `sentiment.py`; `CX-017` → `app.regime_records`; `CX-019` → `jobs.Engine`, `app.per_symbol`.

## Layout
| Path | Purpose |
|---|---|
| `pyproject.toml` | uv workspace member `context-data` |
| `src/tb_context_data/__main__.py` | Entrypoint: `serve` (default), `backfill SOURCE START END`, `spike v6` |
| `src/tb_context_data/config.py` | `Settings` (`TB_*`, `TB_CTX_*`); `availability()` builds the `AvailabilityConfig` |
| `src/tb_context_data/app.py` | Wiring: sources by available credentials, jobs, engine, regime rows, backfill |
| `src/tb_context_data/jobs.py` | Job engine: schedules, stamping, publishing, freshness, health, metrics |
| `src/tb_context_data/records.py` | `Record`/`Version` histories, `Versioner` (CX-004), `Out` → `ContextEvent` with message IDs |
| `src/tb_context_data/publish.py` | NATS and in-memory publishers |
| `src/tb_context_data/http.py` | Rate-limited HTTP with bounded retries, `Retry-After`, no redirects, redacted errors |
| `src/tb_context_data/resolve.py` | Ticker → instrument as of a time; universe tickers from `ref/` |
| `src/tb_context_data/sanitize.py` | Untrusted-text cleaning (CX-011) |
| `src/tb_context_data/sentiment.py` | Quarantine wrapper, lexicon and local-LLM backends, contamination (CX-012, CX-014) |
| `src/tb_context_data/sources/` | `fmp.py`, `alfred.py`, `finra.py`, `edgar.py`, `news.py` (gRPC client + pipeline) |
| `src/tb_context_data/spike_v6.py` | Spike V6: FMP earnings report timing in `/stable` |
| `ctx_tests/` | Fixture-backed tests (`kit.py` stands in for marketdata's sink), opt-in real-API tests (`-m ctx_live`) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_NATS_USER` / `TB_NATS_URL` | `context_data` / `nats://nats:4222` | Password from secret `nats_context_data_password` |
| `TB_LAKE_DIR` | `/data/lake` | Mounted read-only |
| `TB_BROKER_GATEWAY_ADDR` | `broker-gateway:9090` | `GetNews`; signing key `svc_context_data_ed25519` |
| `TB_CTX_UNIVERSE` / `TB_CTX_SYMBOLS` | `dev` / empty | Universe from `ref/`; comma list overrides |
| `fmp_api_key`, `fred_api_key` (secrets) | `unset` | Missing keys disable the source (listed at startup) |
| `TB_CTX_FMP_RATE_PER_MIN` | `50` | Shares the FMP plan with marketdata (`TB_FMP_RATE_PER_MIN`): keep the sum ≤ 80 % of the plan |
| `TB_CTX_FMP_REPORT_TIMES` | `false` | Set after spike V6 confirms `time` in `/stable` earnings |
| `TB_CTX_FRED_SERIES` | payrolls, unemployment, CPI, core CPI, PCE, real GDP, claims, fed funds, 2y, 10y, 10y-2y, VIX, VIX3M | ALFRED series |
| `TB_CTX_SEC_USER_AGENT` | empty (EDGAR off) | `"<name> <email>"` per SEC fair access |
| `TB_CTX_EDGAR_FORMS`, `TB_CTX_ECON_COUNTRIES` | `8-K,10-Q,10-K,4,…` / `US` | |
| `TB_CTX_NEWS_ENABLED`, `TB_CTX_NEWS_POLL_SECONDS`, `TB_CTX_NEWS_STORE_BODY` | `true`, `15`, `false` | Bodies only when licensed |
| `TB_CTX_SENTIMENT_SCORER` | `lexicon` | `local_llm` needs `TB_CTX_LLM_URL` (allowlisted host), `TB_CTX_LLM_MODEL`, `TB_CTX_LLM_MODEL_VERSION`, `TB_CTX_LLM_TRAINING_CUTOFF` |
| `TB_CTX_*_EVERY` | calendar 1800, earnings 3600, fundamentals 21600, macro 3600, finra 21600, edgar 900, regime 3600 (s) | |
| `TB_CTX_STALE_FACTOR` | `3` | Stale after `every × factor` without success |
| Availability: `TB_CTX_NEWS_DELAY_S`, `…_SENTIMENT_LATENCY_S`, `…_ECON_ACTUAL_DELAY_S`, `…_ECON_SCHEDULE_LEAD_DAYS`, `…_EARNINGS_{BMO,DMH,AMC,UNKNOWN}_TIME`, `…_EARNINGS_SCHEDULE_LEAD_DAYS`, `…_FUNDAMENTALS_DELAY_S`, `…_EDGAR_DELAY_S`, `…_FINRA_PUBLISH_TIME`, `…_FINRA_FALLBACK_SESSIONS`, `…_MACRO_DEFAULT_RELEASE_TIME` | see [context-data.md](../../docs/business-logic/context-data.md) | CX-005..CX-012 |

## Run and test
- Run: `make up` (container), or `uv run python -m tb_context_data`. Backfill: `make ctx-backfill SOURCE=macro START=2016-01-01 END=2026-10-17`.
- Test: `uv run pytest services/context-data/ctx_tests` (in `make test-py`). Real APIs (opt-in): `make test-ctx-live`; set `TB_CTX_SEC_USER_AGENT` for EDGAR and `TB_CTX_FRED_KEY` / `TB_CTX_FMP_KEY` for keyed sources.
- Spike V6: `make spike-v6` (needs `.secrets/fmp_api_key`; writes `spike-v6-report.json`).

## Operational notes
- Metrics: `tb_context_versions_total{kind,source}`, `tb_context_job_runs_total{ctx_job,outcome}`, `tb_context_job_last_success_timestamp_seconds`, `tb_context_source_stale`. Alerts: `ContextSourceStale`, `ContextLakeSinkPoison`. Dashboard: "Market data and brokers". Runbook: [context-data.md](../../docs/runbooks/context-data.md).
- With only the simulator behind broker-gateway, news reports `unavailable` (retried hourly, no alert).

## Gotchas
- Payload fields must be deterministic functions of vendor content (no fetch times, no `updated_at`, no `realtime_end`), or every fetch becomes a new version.
- FINRA returns an empty body (not `[]`) for dates with no data. Settlement dates near the window end must resolve against whole-month sessions.
- The EDGAR `acceptanceDateTime` in the submissions API is not true UTC; keep the conservative parse (CX-009).
- Shared trading logic (availability rules, reads, features, blackouts) belongs in `libs/py/tbcore/context`, never here.
