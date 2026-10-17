# Runbook: broker session degraded or down

**Alerts:** `broker.session.degraded` (warning), `broker.margin_event`, `broker.order.unknown`, `broker.unmapped_status` (critical) from `broker-gateway`; feed state `CONNECTION_LIMIT` / `AUTH_FAILED` on the market-data stream.
**Rules:** BR-010..BR-013, BR-015, BR-021, OE-004, OE-005, N5.

## What the platform already did

- New orders and replaces are refused while the account is not `READY` (`BROKER_UNAVAILABLE`); cancels still work in `DEGRADED`.
- The adapter reconnects with jittered backoff (1 s → 60 s cap), resyncs open orders and executions, publishes anything missed exactly once, and requests reconciliation before returning to `READY`.
- Broker-side protective orders (brackets/OCO) keep protecting open positions during the outage.

## Check

1. Session state and reasons: `grpcurl`-free path is Grafana → "Stack overview" → `tb_broker_session_state{account=...}` (5 = READY, 6 = DEGRADED, 1 = DISCONNECTED, 7 = STOPPED) and `make logs SERVICE=broker-gateway` (look for `broker session state` lines; reasons never contain credentials).
2. Alpaca status page and your network (Tailscale up, DNS resolves `paper-api.alpaca.markets`).
3. `reason: no credential stored` → add or re-enter the key in the UI (Broker connections). It is picked up within a minute.
4. `authentication failed` → the key was revoked or regenerated at Alpaca; rotate it in the UI.

## Specific cases

| Symptom | Action |
|---|---|
| `broker.margin_event` (margin call or intraday margin deficit) | The account is halted (RM-013). Resolve at the broker (deposit, reduce manual positions). The session returns to `READY` only after a clean snapshot and a reconciliation request. |
| `broker.order.unknown` | A submit timed out and the lookup by `client_order_id` could not confirm it. Do not resubmit. Check the order in the Alpaca dashboard by its `tb1-...` ID; the next resync and reconciliation settle the state. |
| `broker.unmapped_status` | Alpaca returned an order status not in the mapping table (brokers.md). The order is treated as `UNKNOWN` (fail closed). Add the status to `internal/alpaca/mapping.go` and the table in the same commit. |
| Market-data feed `CONNECTION_LIMIT` (406) | Another client holds the single Alpaca data socket on this key: a notebook, a script, or possibly the TradingView broker panel (spike V7). Close it, or give the other client its own key. The stream retries every 60 s. |
| Market-data feed `AUTH_FAILED` | Data plan or key problem; confirm the Algo Trader Plus subscription (O2) or switch `TB_ALPACA_DATA_FEEDS` to `alpaca.iex`. |
| Live account shows `STOPPED` | Expected unless `TB_TRADING_LIVE_ENABLED=true` and the account has an audited live enablement (BR-013, section 15). |

## After recovery

- Confirm `recon.request.<acct>` was published (ledger reconciliation runs from Phase 2) and that there is no reconciliation break.
- If the outage exceeded a bar interval, the market-data service repairs gaps automatically; check the gap report.
