# Runbook: market data stale, slow, gapped, or disputed

**Alerts:** `MarketDataStaleSymbols` (critical), `BarPublicationSlow`, `ReconciliationBreaches`, `FMPBreakerOpen` (warning) from Prometheus; bus alerts `md.reconciliation.breach` and `md.corporate_action.disagreement` from `marketdata`. Feed-level socket problems (`CONNECTION_LIMIT`, `AUTH_FAILED`) are in [broker-outage](broker-outage.md).
**Rules:** MD-001..MD-012, DF-007, RM-004, BR-015. Design: [ADR-0022](../adr/0022-market-data-ingestion.md).

## What the platform already did

- While the stream is disconnected or a symbol has no recent final bar, `IsFresh` answers false, so execution blocks new risk on that symbol (fail closed, MD-007).
- On reconnect, every minute missed during the outage was fetched over REST and published once, flagged repaired (MD-006). The end-of-day job re-fetches the whole session from the source and records remaining gaps.
- A reconciliation breach has already quarantined that symbol-day: feature snapshots exclude it (MD-010). A corporate-action disagreement has already blocked adjusted reads for that instrument (MD-012). Nothing is deleted.

## Check

1. Grafana → "Market data and brokers": stale symbols, final-to-bus p99, repaired bars, gaps, unknown symbols, FMP requests by outcome, backfill chunks.
2. `make logs SERVICE=marketdata`: look for `stream`, `scheduled job`, `startup backfill`, and `reference sync` lines.
3. Source health: the dashboard's FMP and stream panels; the same data goes to `sys.health.alpaca.sip` / `sys.health.fmp` every 15 s (breaker state, last-message age).
4. Postgres (`psql` in the `postgres` container): `marketdata.backfill_jobs` (state, error), `marketdata.gaps` (unrepaired runs), `marketdata.recon_results` and `marketdata.quarantine` (latest breaches), `refdata.corporate_actions WHERE agreement = 'disagreed'`.

## Specific cases

| Symptom | Action |
|---|---|
| `MarketDataStaleSymbols` for all symbols during the session | The stream is down. Check broker-gateway first ([broker-outage](broker-outage.md)): data socket state, `CONNECTION_LIMIT` (another client on the key), Alpaca status page. marketdata reconnects by itself and repairs the gap. |
| `MarketDataStaleSymbols` for a few symbols | Usually halted or very illiquid names (no trades → no bars). Confirm a halt on the exchange site. If a symbol was renamed, the morning reference sync remaps it; to force it, restart marketdata (it re-reads the universe). |
| `BarPublicationSlow` | NATS or Valkey is slow or the host is overloaded: check `docker stats`, NATS JetStream disk, Valkey memory. The lake sink is off the hot path and does not cause this. |
| `FMPBreakerOpen` | FMP is failing (status page, plan limits, or a revoked key). Reconciliation and reference sync wait and retry; live trading is unaffected. If 401/403: write a valid key to `.secrets/fmp_api_key` and `make up`. If 429s: lower `TB_FMP_RATE_PER_MIN` to 80% of the plan limit (spike V3 records the plan). |
| `ReconciliationBreaches` | Read `marketdata.recon_results.detail` for the symbol-day. Compare with the exchange or a third source. If Alpaca was wrong, the next backfill revision usually fixes it; if FMP was wrong, mark the quarantine row resolved (`UPDATE marketdata.quarantine SET resolved_at = now(), resolved_by = 'owner: <why>' WHERE instrument_id = ... AND session_date = ...`) so it leaves `ref/quarantine.parquet` at the next export. Never delete rows. |
| `md.corporate_action.disagreement` | Two sources disagree on a split/dividend (type, ex-date, or ratio). Check the company's investor-relations page or SEC filing. Then `docker compose stop marketdata` (it holds reference data in memory and would overwrite a live edit), set the correct row's `agreement` to `confirmed` and the wrong one's to `rejected` in `refdata.corporate_actions`, and `make up`. Owner resolutions survive later syncs; adjusted reads ignore `rejected` rows and unblock at the next `ref/` export. Note the evidence in the incident notes. |
| Gaps listed in `marketdata.gaps` after the end-of-day job | Runs of ≥ 5 missing minutes the vendor also does not have. For liquid names, open an Alpaca support ticket; for thin names it is expected. They stay recorded for data-quality reports. |
| marketdata was down for days | Restart it: missed end-of-day jobs re-run for the last 72 h and the start-up catch-up backfills the last `TB_MD_CATCHUP_DAYS` (10). For longer outages set `TB_MD_CATCHUP_DAYS` higher in `infra/compose/.env`, `make up`, then remove it. |
| Initial history load | Set `TB_MD_HISTORY_START=YYYY-MM-DD` in `infra/compose/.env` and `make up`. Progress: `marketdata.backfill_jobs` / the dashboard. Restarts resume from the last checkpoint. |
| `backfill_jobs.state = FAILED` | Read `error`. Vendor errors: restart marketdata (unfinished jobs resume from their checkpoint). Repeated failures on one chunk: check the symbol in the vendor's data explorer. |

## After recovery

- Confirm `tb_md_stale_symbols` is 0 and the repaired-bars counter increased by about the outage length × symbols.
- Run `make lake-verify` after any disk or host incident (ST-003).
- Record what happened and any manual quarantine or corporate-action resolution in the incident notes.
