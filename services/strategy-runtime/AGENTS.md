# strategy-runtime

Runs runtime-hosted strategies (`tbcore.strategy`) on final bars and publishes their order intents to execution over the bus (section 07, [ADR-0026](../../docs/adr/0026-strategy-runtime-and-orb-baseline.md), D44). Phase 2 hosts the ORB baseline; Phase 3 adds event blackouts (CX-016). Rules: SM-011, SM-013, SM-014, RM-017, CX-016.

**Status:** Phase 2 (paper)  ·  **Language/stack:** Python 3.12, `tbcore`, `nats-py` ([ADR-0004](../../docs/adr/0004-core-language-split.md), D1)

## Responsibilities
- Owns: bar dispatch to strategies, own-portfolio tracking from `ledger.update.*`, intent publication, the runtime heartbeat.
- Does not own: strategy logic and the host core (in `libs/py/tbcore/src/tbcore/strategy/`, `host.py`), risk, orders, brokers.

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | NATS (ordered consumers) | `md.bar.<tf>.<instrument>` for hosted instruments; `ledger.update.<acct>` from today's start | `tb.marketdata.v1.Bar`, `tb.ledger.v1.LedgerUpdate` |
| out | NATS (JetStream) | `ord.intent` (`Nats-Msg-Id` = intent ID) | `tb.orders.v1.OrderIntent` |
| out | NATS (core) | `sys.health.strategy-runtime` | `tb.signals.v1.RuntimeHeartbeat` |
| in | file | registry seed (`TB_STRATEGY_REGISTRY`) shared with execution | `infra/compose/strategies.json` |
| in | file | research deployments (`TB_RESEARCH_REGISTRY`, written by the optimizer): shadow/paper model strategies, never live (EX-056) | same entry format, runtime classes `model_signal`/`meta_filter` (SM-015) |
| in | NATS (ordered consumers, stream `CONTEXT`) | `ctx.calendar`, `ctx.earnings` (only when a hosted strategy has `runtime.blackout`) | `tb.context.v1.ContextEvent`, CX-016 |
| in | filesystem | lake (read-only): stored calendar and earnings versions at start and hourly (the bus keeps 7 days) | `tbcore.context.ContextStore`, CX-016 |
| in | NATS (ordered consumers, stream `SIGNALS`, new messages only) | `sig.<tv-strategy>.>` for hosted `tradingview` strategies (webhook signals from api-gateway) | `tb.signals.v1.Signal`, SM-020 |

## Layout
| Path | Purpose |
|---|---|
| `src/tb_strategy_runtime/__main__.py` | NATS loop around `tbcore.strategy.host.Runner` (the pure core: registry, dispatch, own positions, proto conversion, event blackouts with `BlackoutStrategy` + `InMemoryEventSource`), shared with the backtester (BT-002) |
| `src/tb_strategy_runtime/__main__.py` | NATS loop, heartbeat, ops listener |
| `tests/` | Runner tests (SM-014 isolation, protected opens, CPI blackout in paper) |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TB_STRATEGY_REGISTRY` | `/etc/tb/strategies.json` | Strategies with a `runtime` block and mode paper/shadow/live are hosted |
| `TB_NATS_URL` / `TB_NATS_USER` | `nats://nats:4222` / `strategy_runtime` | Password from secret `nats_strategy_runtime_password`; empty user = no auth (tests only) |
| `TB_HEARTBEAT_INTERVAL` | `5` | Seconds |
| `TB_RESEARCH_REGISTRY` | empty (Compose: `/data/ml-artifacts/deploy/research-strategies.json`) | Optimizer deployments added at start (shadow/paper only); model bundles are read from the read-only ML artifacts mount |
| `TB_LAKE_DIR` | empty (Compose: `/data/lake`, read-only, marketdata's UID) | Scheduled events for blackouts; without it only bus events are used |
| `TB_OPS_DISABLED` | — | `1` skips the ops listener (paper harness) |

## Run and test
- Run: `make up` (container) or `uv run --package strategy-runtime python -m tb_strategy_runtime`.
- Test: `uv run pytest services/strategy-runtime/tests libs/py/tbcore/tests/test_strategy.py`; full path: `make paper-gate`.

## Operational notes
- Execution latches a cancel-open kill switch for hosted strategies if this heartbeat stops during the session (RM-017).
- A restart replays the day's ledger updates to rebuild positions; replayed decisions produce the same intent IDs, which execution deduplicates.

## Gotchas
- Every JetStream subscribe passes `stream=`: the NATS user (least privilege) may not call `$JS.API.STREAM.NAMES`, and nats-py looks the stream up when it is missing, which times out after 5 s and kills startup. Tested by `test_every_jetstream_subscription_names_its_stream`.
- Blackouts: add `"blackout": {"kinds": ["CPI", "FOMC", "earnings"], "before_minutes": 5, "after_minutes": 30}` to a strategy's `runtime` block; only opening intents are dropped, and only for events known at the decision time (CX-016).
- The compose seed lists no instruments: add instrument IDs (from `refdata.instruments`) to `runtime.instruments` to trade.
- TradingView strategies (runtime class `tradingview`, SM-020) need a `tv-<name>` strategy ID and the instruments they may trade in `runtime.instruments`; the runtime also subscribes to their bars, because sizing and stops use the platform's own close. Setup: `docs/runbooks/tradingview-webhook.md`.
