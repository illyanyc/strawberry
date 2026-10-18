# Go live

The only path to a live strategy (GL-010..GL-023, [ADR-0054](../adr/0054-go-live-graduation-and-live-enablement.md)). Nothing in config turns an account live. Simulators and fakes are the only brokers in tests; this runbook is what the owner follows later, on paper credentials until the trading stage.

## Before you start

1. Leave `TB_TRADING_LIVE_ENABLED` unset (it defaults to false) until the checklist below is done.
2. Store the **live** credential on its own account id (`alpaca-live` or `ibkr-live-…`). A paper credential cannot decrypt as live (SE-030) and a live-mode order to a paper account is rejected (GL-052).
3. Open **Go live** in the UI (`g` then `l`). The Promote button stays disabled until every gate on that page passes.

## Readiness

Mark each attested item on the Go live page only after you have actually done it. The page records the mark; it does not waive an item.

- Security: section 02 Stage B, no open high findings, credential-leak drill, `broker_kek` escrowed (`docs/runbooks/secrets-and-keys.md`).
- Operations: backup restore drill in the last 30 days; runbooks for broker outage, IBKR re-auth, reconciliation, kill switch, and rollback rehearsed.
- Data: feeds healthy for 10 sessions, same feed the strategy was validated on.
- Risk drill, phone alerts, PDT and wash-sale visibility, docs current.

Automated rows (flag, live environment, a clean read-only session, no open breaks, ownership) fill themselves.

## Enable, then promote

1. Set `TB_TRADING_LIVE_ENABLED=true` and restart `broker-gateway`, `execution`, and (if used) `broker-gateway-ibkr-live`. Adapters stay stopped until the next step.
2. Enable the account at stage **read only** (passkey, type the account id). The adapter streams snapshots. Orders are refused.
3. Let one session reconcile with zero unexplained breaks. The checklist's read-only row turns passed.
4. Enable stage **trading** (passkey again). This requires the complete checklist.
5. Promote one paper strategy (passkey, type the strategy name, cap 10–20% of the intended allocation). A paper twin keeps running on the paper account. A second strategy cannot be promoted while one is live.

Live orders still pass the risk gate, the kill switch, and the virtual-portfolio checks. The analyst cannot enable or promote.
