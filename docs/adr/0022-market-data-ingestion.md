# ADR-0022: Market data ingestion: Alpaca through broker-gateway, FMP `/stable`, finality window, resumable backfill, and nightly reconciliation

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D40 (implements D20)
- **Related:** [market-data.md](../business-logic/market-data.md) MD-001..MD-012, [storage.md](../business-logic/storage.md), DF-002, DF-006, DF-007, BR-015, BR-016, RM-004; [ADR-0019](0019-alpaca-adapter-and-simulator.md), [ADR-0020](0020-lake-layout-write-path-and-query-layer.md); research [04](../research/04-brokers-data-ingestion.md), [verify-early](../verify-early.md) V2, V3, V7, V9

## Context

D20 chose Alpaca Algo Trader Plus (SIP) as the primary bar source and FMP for reference data and daily reconciliation. Section 05 needs: complete, deduplicated, point-in-time correct bars in the lake; live bars on the bus fast enough for intraday decisions (U8: finality to bus p99 < 500 ms); no silent gaps across disconnects or crashes; vendor limits respected; and a second opinion on every daily bar. Constraints: one Alpaca market-data socket per account (BR-015, owned by broker-gateway), Alpaca revises a minute bar for about 30 s after its close (`updatedBars`), FMP has moved new features to `/stable` and its plan limits are unknown until the owner's key exists (V3), and no credentials exist in CI.

## Decision

1. **Alpaca only through broker-gateway.** marketdata calls `BrokerMarketDataService` (historical bars, the stream, assets, corporate actions); it never holds Alpaca keys or opens its own socket. Historical requests are raw and carry `asof` = the window's last day so recycled tickers resolve to the right company (V2).
2. **Finality window 60 s.** A live 1-minute bar is partial (UI-only subject `md.bar.partial.*`) until 60 s after its close, then final (`md.bar.<tf>.<id>`, Valkey, lake). Later updates are corrections: a new lake revision (DF-007), `md.bar.correction.*`, cache overwritten, derived bars rebuilt. `available_at` = `ts_close` + per-feed publication delay (MD-003); backfill uses the same delay so backtests never see a bar earlier than live did.
3. **Bars above 1 minute are built locally** from final 1-minute bars, session-aligned (MD-004), and checked against an offline resample oracle. One code path (`live.Aggregate`) serves live and repair.
4. **Gaps are calendar-defined.** After any reconnect the pipeline fetches every closed minute since the last final bar over REST and finalizes each once (`repaired`); the end-of-day job backfills the whole session from the source, so provisional live bars become revisions of source-final bars, then records and re-fetches runs of ≥ 5 missing regular minutes (MD-006).
5. **Historical intervals:** 1m, 5m, 15m, and 1d come from the vendor (5m/15m clock boundaries coincide with session boundaries because 04:00, 09:30, 16:00, and 20:00 are on 15-minute marks); vendor 1h bars start on the hour and are refused; live 30m/1h are built from minutes.
6. **Resumable backfill jobs** in Postgres (`marketdata.backfill_jobs`, `backfill_chunks`, `gaps`): chunks of ≤ 100 symbols × one month, checkpointed after the lake write; restart skips finished chunks; natural-key deduplication makes reruns write zero rows (MD-001). Exposed as `MarketDataService.Backfill` / `GetBackfillStatus`. On start the service queues a history load from `TB_MD_HISTORY_START` and a catch-up of the last 10 days, with IDs derived from their parameters so restarts never repeat work.
7. **FMP client on `/stable` only**, hand-written: token bucket at 80% of the plan (default 240/min for Starter), 30-day bandwidth budget, jittered retries honouring `Retry-After`, circuit breaker, key redacted from every error (MD-008). Used for the asset universe and delistings, symbol changes, splits and dividends, and non-split-adjusted EOD bars.
8. **Nightly reconciliation**: Alpaca SIP daily bars vs FMP non-split-adjusted EOD, tolerances close 5 bps, high/low 10 bps (or a tick), volume 5% when both are consolidated; breaches quarantine the symbol-day (`marketdata.quarantine` → `ref/quarantine.parquet`), which `tbcore.features.materialize` excludes by default (MD-010). Corporate actions from both sources are cross-checked; disagreements block adjusted reads (MD-012) and alert.
9. **Freshness is a service API.** `MarketDataService.IsFresh` fails closed (disconnected, unsubscribed, no bar, or older than max age + window); execution uses it for RM-004 from Phase 2. Source health goes to `sys.health.<source>` every 15 s.
10. **Publication path:** final bars are handed to bus, cache, and lake sinks together; the bus sink publishes a minute's bars concurrently with `Nats-Msg-Id` = a hash of (instrument, timeframe, `ts_open`, feed, revision) so JetStream drops duplicates after a crash. The lake sink buffers and flushes every 30 s (the lake is not on the decision path). Corporate actions go to the durable `REF` stream (`ref.corporate_action.<id>`) for the ledger.
11. **Scheduling** is calendar-aware in-process (`internal/sched`): reference sync 06:00 ET, end-of-day job at close + 20 min (half days included), reconciliation 07:00 the next morning; each job runs once per session and missed runs are retried for 72 h.
12. **Universe:** an embedded dev universe (~100 liquid names plus SPY and sector ETFs) is the default; `SetUniverse` and `TB_UNIVERSE_FILE` replace it, and resubscription diffs the symbol set on the single upstream stream.

## Alternatives considered

| Option | Why not |
|---|---|
| marketdata connects to Alpaca directly | A second socket breaks Alpaca's one-connection limit (BR-015) and duplicates credentials |
| Treat each streamed minute bar as final immediately | Alpaca revises bars for ~30 s; decisions would use values the lake later changes, breaking parity (DF-004) |
| Use Alpaca's 5m/15m bars | Not available on the stream; REST aggregates would differ from what live built; local build from minutes is one code path |
| Prefect for scheduling the daily jobs | The three jobs are calendar-bound, short, and owned by one service; an extra dependency on the critical path for no benefit. Prefect stays for ML pipelines |
| An SDK for FMP | No maintained Go SDK covers `/stable`; a thin client gives control of rate limits and redaction |
| Polygon/Databento as reconciliation source | Extra cost; FMP is already paid for (D20) and EOD-level agreement is what reconciliation needs |
| Quarantine by deleting bars | Destroys evidence and history; a quarantine list applied at read time keeps the lake append-only |

## Consequences

- Positive: no silent gaps (repair on reconnect plus end-of-day source backfill); duplicates impossible by key; live and backtest see bars at the same `available_at`; a second source checks every daily bar; vendor limits enforced in code; everything runs in CI against fakes.
- Negative / risks: decisions on a bar wait 60 s after its close (acceptable for 5m+ strategies; 1-minute strategies would need a shorter window and accept corrections); FMP limits and the delisted coverage are defaults until V2/V3 run with the owner's keys; the dev universe is small until the owner selects one.
- Follow-up work: Phase 2 execution consumes `IsFresh`; Phase 3 context-data reuses the FMP client; FirstRate import (`feed=firstrate`) when the owner buys the history (O3).

## Validation

- `TestMD001*` (idempotent, resumable, gap repair, ticker change), `TestMD002FinalityWindowAndCorrections`, `TestMD004ResampledBarsEqualOracle`, `TestMD006StreamKilledThreeMinutesIsRepairedOnce` (zero missing, zero duplicate bars), `TestMD007*`, `TestMD008RetryAfterBreakerAndLegacy`, `TestMD010*`, `TestMD012*`.
- `TestU8BarFinalToBusLatency`: p99 ≈ 110 ms finality-to-bus-ack for 300 symbols × 5 minutes (plus 5m bars) through an fsync-always JetStream (target < 500 ms).
- Revisit if the owner's FMP plan lacks `/stable` EOD, if Alpaca changes the `updatedBars` window, or if 1-minute strategies need sub-60 s finality.
