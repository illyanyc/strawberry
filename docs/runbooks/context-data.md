# Runbook: context data (section 09)

Alerts: `ContextSourceStale` (warning), `ContextLakeSinkPoison` (warning), and bus alerts `ctx-stale-<job>`. Rules: [CX-019](../business-logic/context-data.md), CX-001, CX-003.

Context data is advisory: features fall back to the last versions known, and nothing blocks trading except strategies that use `EventBlackout`. A stale calendar can make a blackout miss a newly announced event, so check the calendar job first on event days.

## 1. Which job, and why

```bash
make logs SERVICE=context-data          # look for "context job failed" / "context source unavailable"
curl -s localhost:9090/api/v1/query --data-urlencode 'query=tb_context_source_stale' | jq   # via Grafana Explore on the Mac
```

The startup log line `context-data running` lists the scheduled jobs and the `disabled` sources with the reason (missing key or contact).

| Symptom | Cause | Fix |
|---|---|---|
| `fmp: HTTP 401/403` | Key missing, wrong, or legacy-only plan (spike V3) | Write the key to `.secrets/fmp_api_key`, `make up` |
| `fmp: HTTP 429` | Plan rate limit shared with marketdata | Lower `TB_CTX_FMP_RATE_PER_MIN` or `TB_FMP_RATE_PER_MIN` (sum ≤ 80 % of the plan) |
| `alfred: HTTP 400` "api_key" | `fred_api_key` unset | Register a free key at fred.stlouisfed.org, write `.secrets/fred_api_key` |
| `edgar: HTTP 403` | SEC rejects the User-Agent or the rate | Set `TB_CTX_SEC_USER_AGENT="<name> <email>"` in `.env`, restart |
| `finra: HTTP 5xx` | FINRA API outage | Wait; the job retries every 6 h and backfills the last 45 days of settlements |
| news `unavailable` | broker-gateway has no Alpaca data account (simulator only) | Add Alpaca keys in the UI (Broker connections); news resumes within the hour |
| news `UNAUTHENTICATED`/`PERMISSION_DENIED` | Service key missing or allowlist mismatch | `make secrets` (creates `svc_context_data_ed25519*`), restart broker-gateway and context-data |
| every symbol failed | Universe empty or vendor down | Check `ref/` export (marketdata refsync) and the vendor status page |

## 2. Poison events (`ContextLakeSinkPoison`)

marketdata terminated events missing `kind`, `record_key`, `source`, `event_time`, or `available_at` (CX-001). That is a context-data bug: check `make logs SERVICE=marketdata` for `context event rejected` with the subject, fix, and backfill the affected window (below). Terminated messages are not retried.

## 3. Backfill a window

Backfills are idempotent (CX-004): first-seen records get rule times, unchanged ones add nothing.

```bash
docker compose -f infra/compose/docker-compose.yml exec context-data \
  python -m tb_context_data backfill <calendar|earnings|fundamentals|macro|short_interest|filings|news|regime> 2026-01-01 2026-10-17
```

Backfilled history uses assumed schedule lead times (`basis: assumed_lead`); FMP statements fetched long after acceptance may already include restatements. Both are recorded in the payload `basis`.

## 4. marketdata is down

Events wait in stream `CONTEXT` for 7 days and are written when marketdata returns. After a longer outage, run the backfill for the gap.

## 5. Changing the scoring model

Set `TB_CTX_SENTIMENT_SCORER=local_llm` with `TB_CTX_LLM_URL` (local host only), `TB_CTX_LLM_MODEL`, `TB_CTX_LLM_MODEL_VERSION`, and `TB_CTX_LLM_TRAINING_CUTOFF`; the service refuses to start without the version and cutoff (CX-014). Scores before the cutoff are flagged contaminated and excluded from features. Run the `news` backfill to score history with the new model (new records keyed by model version).
