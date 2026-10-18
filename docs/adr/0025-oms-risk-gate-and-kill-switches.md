# ADR-0025: OMS, pre-trade gate, D30 routing and margin guard, latched kill switches, and dead-man checks

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D43 (implements D30 and D2's order-path outbox for execution)
- **Related:** OE-001..OE-020, RM-001..RM-017, SM-001, SM-012, SM-013, VP-002..VP-008, [ADR-0010](0010-account-routing-and-day-trade-policy.md), [ADR-0018](0018-broker-gateway-interface.md), [ADR-0023](0023-ibkr-adapter-gateway-and-sessions.md), [ADR-0024](0024-virtual-ledgers-and-reconciliation.md), section 07 prompt

## Context

Section 07 needs an order path whose most likely failure is "no order sent": every intent risk-checked and audited, idempotent submission, broker-side protection on every entry, kill switches that work when parts of the stack are down, and D30 routing (intraday → Alpaca at 1× margin, swing → IBKR with GTC stops, intraday-margin guard, PDT guard off by default). The section left open the order-type default, the sizing default, and how strategies reach the OMS. The IBKR adapter (ADR-0023) declares no bracket/OCO/OTO support, so swing protection cannot ride on the entry.

## Decision

1. **Go OMS in `execution`** with Postgres schema `execution` (strategies, risk limits, intents with decisions, orders, fills, kill switches, halts, outbox). Every state change and its bus event (`ord.decision`, `ord.order.<acct>`, `risk.kill`, `alert.*`) commit in one transaction; audit records ride in the same transaction. Intents arrive on the durable bus subject `ord.intent` (strategy runtime) or `ExecutionService.SubmitIntent` (UI via api-gateway); processing is serialized, idempotent on `intent_id`.
2. **Pre-trade gate as pure checks** (`internal/risk`): 32 named checks evaluated for every intent (mode/account match, kill switch, halts, routing, session, reconciliation freshness and halted pairs, margin events, clock, data freshness, market session, tradability, symbol segregation, ownership, self-cross, protective order, collar, notional/qty/position/exposure/positions/concentration limits, daily loss, drawdown, consecutive losses, order rate, open orders, duplicates, fat finger, margin guard, PDT guard), then the ledger reservation (`capital`). Limits merge `global → account → strategy|manual`, tightest wins.
3. **Order construction:** marketable limits with a collar instead of market orders (10 bps entries, 50 bps exits); brackets/OTO at brokers that support them; at brokers that do not (IBKR), a separate stop with the intent's TIF (GTC for swing) placed as soon as the entry is final with a fill, with a halt + page if it cannot be placed. Attribution IDs persisted before submit; child orders use attempts 50–99.
4. **Exits** cancel the owner's own resting orders first, wait for them to be final, and re-size to what the owner still owns (a protective stop may have closed the position meanwhile). Opposite-side resting orders across owners are refused (`self_cross`).
5. **Ambiguity fails closed:** ambiguous submits become `unknown` and halt the strategy×instrument until the order reconciler resolves them by client order ID; open orders reconcile every 30 s and after reconnects (orphans/ghosts halt and page); after a restart nothing is approved until all READY accounts are reconciled.
6. **Kill switches latch** in Postgres; engage from the UI (no step-up), `tbctl kill` (bus `risk.kill`), or automatic triggers (IMD/margin call, reject storm, dead-man); release only via `ReleaseKillSwitch` with a passkey step-up from a desktop client. `cancel_open` keeps broker-side protective stops (removing them would leave naked risk, N5); `flatten_strategy_owned` cancels protection and exits strategy-owned quantity only.
7. **Dead-man in two places:** execution watches the strategy runtime heartbeat; broker-gateway watches execution's heartbeat (`sys.health.execution`) and cancels working platform orders itself, so the check works with execution down.
8. **D30:** routing table and per-account policies are config (`TB_ROUTING`, `TB_ACCOUNT_POLICIES`); defaults per the master prompt; PDT guard `off` with day trades counted.
9. **Strategy registry seed:** a JSON file shared with the strategy runtime registers strategies at startup (never live; existing entries keep their mode; the initial allocation applies once).
10. **Position sizing** stays in the strategy layer (`tbcore.strategy`, shared with the backtester; fixed fractional risk per trade to the stop), capped by the gate (RM-010).

## Alternatives considered

| Option | Why not |
|---|---|
| Market orders by default | Unbounded slippage at the open and in thin names; section 07 pitfall |
| Synthetic bracket at IBKR managed by the OMS in memory | A host outage would leave positions unprotected; a resting GTC stop at the broker survives |
| Cancel protective stops on `cancel_open` | Leaves positions naked during the incident that triggered the switch |
| gRPC from the Python runtime to execution | Needs Python service tokens and a gRPC client in the runtime; the durable bus subject is already idempotent and survives an execution restart |
| Kill-switch release from the bus or mobile | Violates RM-007 / U6 (halt-only phone) |

## Consequences

- Positive: every rejection path is table-tested; the full order path runs in-process in the paper harness against the same adapter engine as production; IBKR swing works through the same `BrokerGatewayService` with GTC protection.
- Negative / risks: serialized intent processing caps throughput at a few hundred intents per second (fine for 15-minute bars); between an IBKR entry fill and the stop placement there is a short unprotected window; held bracket legs are excluded from self-cross by their `held` status, which depends on adapters reporting it.
- Follow-up: protect partial fills while resting; replace-based exits; hypothetical fills for shadow mode (section 08); per-sector concentration once sectors are in refdata.

## Validation

`TestRM003RiskMatrix` (violation and boundary per check), `TestOE006StateMachineConvergesUnderReordering`, the `tests/paper` Gate 2 scenarios (same-ticker, segregation, self-cross, routing, margin guard, IMD, kill-switch drill with 20 orders canceled < 2 s, stale data, lost stream, dead-man, restart, unknown submit), `TestRM017DeadmanCancelsWorkingOrdersKeepsProtection`, and the five-session paper gate. Revisit when live trading (section 15) adds live-specific limits.
