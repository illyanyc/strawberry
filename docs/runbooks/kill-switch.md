# Runbook: kill switches, halts, and dead-man alerts

Rules RM-006, RM-007, RM-013, RM-016, RM-017 ([risk](../business-logic/risk-management.md)), OE-005, OE-014, OE-019.

## Engage (no step-up needed)

- UI: status bar → **Kill switch** (global) or Strategies → strategy → **Kill switch**. Choose the action: `freeze` (no new orders), `cancel_open` (also cancels working strategy orders, keeps protective stops), `flatten_strategy_owned` (cancels everything strategies have open in scope and exits strategy-owned quantity; manual holdings are never touched).
- Phone: the same global button (halt-only, U6).
- Server CLI when the UI is down: `docker compose -f infra/compose/docker-compose.yml run --rm ops kill engage --scope global --action cancel_open --reason "..."` (publishes `risk.kill`; execution applies it as soon as it runs).
- If execution itself is down: broker-gateway's dead-man cancels working platform orders 30 s after the last execution heartbeat (alert "execution heartbeat lost").

## Automatic engagements and what they mean

| Alert | Cause | Action |
|---|---|---|
| `kill switch engaged` by `risk:margin` | Broker reported an IMD, margin call, or trading restriction (RM-013) | Check the broker account; fix the margin issue; release with step-up |
| `kill switch engaged` by `risk:reject_storm` | 5 broker rejects in a minute for one strategy (RM-016) | Read the rejected intents (UI → Orders → Rejected); fix config or the strategy; release |
| `kill switch engaged` by `deadman:strategy-runtime` | Strategy runtime heartbeat lost during the session (RM-017) | `make logs SERVICE=strategy-runtime`; restart it; release once heartbeats are back |
| `strategy halted: unknown_order:*` | Submit outcome unknown (OE-005) | Usually clears itself within seconds when the reconciler finds the order; if it persists, check the broker app for the client order ID |
| `strategy halted: orphan:*` | A `tb1-` order is open at the broker but unknown to the OMS (OE-019) | Cancel it in the broker app if it is not expected; the halt clears after you resolve it (restart execution after the order is gone) |
| `strategy halted: naked:*` | A protective stop could not be placed after an entry (OE-014) | Place a stop manually in the broker app immediately, then investigate the reject |
| `broker session not ready` | Stream lost, IBKR status 1100, adapter down | `docs/runbooks/broker-outage.md`; intents resume automatically after a clean reconciliation |

## Release (desktop, passkey step-up)

UI → status bar → active kill switches → **Release**. Releases are refused from mobile clients and from the bus. Every engage and release is in the audit log (`execution.killswitch.*`).

## Verify

- Status bar shows `running`, no active kill switches or halts; `tb_execution_kill_switches_active == 0`.
- Broker app: no working `tb1-` orders you did not expect; protective stops still present for open strategy positions.
