# strategy-runtime

Runs runtime-hosted strategies (`tbcore.strategy`) on final bars and publishes their order intents to execution over the bus (section 07, [ADR-0026](../../docs/adr/0026-strategy-runtime-and-orb-baseline.md), D44). Phase 2 hosts the ORB baseline. Rules: SM-011, SM-013, SM-014, RM-017.

**Status:** Phase 2 (paper)  ·  **Language/stack:** Python 3.12, `tbcore`, `nats-py` ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: bar dispatch to strategies, own-portfolio tracking from `ledger.update.*`, intent publication, the runtime heartbeat.
- Does not own: strategy logic (in `libs/py/tbcore/src/tbcore/strategy/`), risk, orders, brokers.

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | NATS (ordered consumers) | `md.bar.<tf>.<instrument>` for hosted instruments; `ledger.update.<acct>` from today's start | `tb.marketdata.v1.Bar`, `tb.ledger.v1.LedgerUpdate` |
| out | NATS (JetStream) | `ord.intent` (`Nats-Msg-Id` = intent ID) | `tb.orders.v1.OrderIntent` |
| out | NATS (core) | `sys.health.strategy-runtime` | `tb.signals.v1.RuntimeHeartbeat` |
| in | file | registry seed (`TB_STRATEGY_REGISTRY`) shared with execution | `infra/compose/strategies.json` |

## Layout
| Path | Purpose |
|---|---|
| `src/tb_strategy_runtime/runner.py` | Pure core: registry, dispatch, own positions, proto conversion (unit-tested) |
| `src/tb_strategy_runtime/__main__.py` | NATS loop, heartbeat, ops listener |
| `tests/` | Runner tests (SM-014 isolation, protected opens) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_STRATEGY_REGISTRY` | `/etc/tb/strategies.json` | Strategies with a `runtime` block and mode paper/shadow/live are hosted |
| `TB_NATS_URL` / `TB_NATS_USER` | `nats://nats:4222` / `strategy_runtime` | Password from secret `nats_strategy_runtime_password`; empty user = no auth (tests only) |
| `TB_HEARTBEAT_INTERVAL` | `5` | Seconds |
| `TB_OPS_DISABLED` | — | `1` skips the ops listener (paper harness) |

## Run and test
- Run: `make up` (container) or `uv run --package strategy-runtime python -m tb_strategy_runtime`.
- Test: `uv run pytest services/strategy-runtime/tests libs/py/tbcore/tests/test_strategy.py`; full path: `make paper-gate`.

## Operational notes
- Execution latches a cancel-open kill switch for hosted strategies if this heartbeat stops during the session (RM-017).
- A restart replays the day's ledger updates to rebuild positions; replayed decisions produce the same intent IDs, which execution deduplicates.

## Gotchas
- The compose seed lists no instruments: add instrument IDs (from `refdata.instruments`) to `runtime.instruments` to trade.
