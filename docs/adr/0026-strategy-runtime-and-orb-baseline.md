# ADR-0026: Python strategy runtime on `tbcore.strategy`, intents over the bus, and the ORB baseline

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D44 (implements D1 and D7 for the live strategy path)
- **Related:** SM-011, SM-013, SM-014, RM-010, RM-017, OE-006, OE-014, DF-002, [ADR-0025](0025-oms-risk-gate-and-kill-switches.md), section 07 prompt ("Strategy runtime model", "Position sizing default")

## Context

Section 07 needs a rule-based baseline trading paper before any model exists, and the same strategy code must later run in the event-driven backtester (D7, SM-011). Strategy logic may only live in `libs/py/tbcore` (core rule). The OMS is Go (ADR-0025). Open choices: where the runtime runs, how it reaches execution, the sizing default, and the baseline's exact rules.

## Decision

1. **`tbcore.strategy`** holds the strategy API (`Strategy` protocol, `StrategyContext` exposing only the decision time, own positions, allocation, params, and the calendar; `Bar`, `Intent`, `Fill`), the sizing function, and the ORB baseline. Intent IDs are ULIDs derived from the strategy, instrument, decision time, and kind, so a replayed decision is the same intent (execution deduplicates, OE-006) and backtest replays are deterministic.
2. **`services/strategy-runtime`** (Python, new service) loads runtime-hosted strategies from the registry seed shared with execution, consumes final bars (`md.bar.<tf>.<instrument>`, ordered consumers) and its strategies' own ledger updates (`ledger.update.<acct>`, filtered to its owners, SM-014), and publishes intents on the durable subject `ord.intent` with `Nats-Msg-Id` = intent ID. It never calls brokers or execution's gRPC. Heartbeats go to `sys.health.strategy-runtime` (`tb.signals.v1.RuntimeHeartbeat`) for the dead-man (RM-017).
3. **Sizing default:** fixed fractional risk per trade to the stop (`risk_per_trade` of allocation + realized P&L, whole shares, capped by own cash); the gate may only cap it (RM-010). Volatility targeting and fractional Kelly come with models (section 10).
4. **ORB baseline** on 15-minute bars: the first bar's range; long on the first close above the range high (one entry per day); stop = range low − 5 bps; target = 2R; broker-side bracket via the OMS (N5); exit at the first bar ending 15 minutes or less before the close; flat overnight.

## Alternatives considered

| Option | Why not |
|---|---|
| ORB in Go inside execution | Strategy logic would exist outside `tbcore` and the backtester could not run the same code (SM-011) |
| Runtime inside `services/ml` | Couples the always-on trading path to the ML worker lifecycle (native launchd workers on macOS, D29) |
| gRPC from the runtime to execution | Needs Python service tokens and makes intents fail when execution restarts; the durable bus subject survives restarts and is idempotent |
| 5-minute bars or MA crossover | The master prompt fixes 15-minute decision bars for intraday; ORB on stocks in play is the research's rule-based primary signal (D23) |

## Consequences

- Positive: the Phase 2 gate runs the real Python runtime against the in-process stack; the event-driven backtester (Phase 3) can drive the same `Strategy` objects.
- Negative / risks: one more always-on container; positions are rebuilt from the day's ledger updates on restart (fine for intraday; swing strategies will need a ledger snapshot query, Planned).
- Follow-up: swing strategies and `on_fill` use, model-driven strategies (`on_signal`), runtime position bootstrap from the ledger.

## Validation

`libs/py/tbcore/tests/test_strategy.py` (rules, determinism, no look-ahead), `services/strategy-runtime/tests` (own-portfolio isolation, protected opens), and `TestGate2FiveORBSessionsReconcileAndManualUntouched` (five simulated sessions through the full path).
