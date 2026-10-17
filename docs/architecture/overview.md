# Architecture overview

**Status:** Phase 0 in progress. Stack decisions are accepted per [`../decisions.md`](../decisions.md) (research report); ADRs are added as each phase implements them. The service list with paths and owners is in the [root `AGENTS.md` system map](../../AGENTS.md#system-map); this doc covers how the pieces connect.

## Components and data flow

```mermaid
flowchart LR
  subgraph Brokers
    ALP[Alpaca API + WS]
    IBG[IB Gateway / TWS API]
  end
  subgraph External[External data]
    NEWS[News / sentiment]
    MACRO[Macro / calendars]
    TV[TradingView webhooks]
  end
  subgraph Core[Core services]
    BRK[broker-gateway]
    MD[marketdata]
    OMS[execution: OMS + risk]
    LED[ledger]
  end
  subgraph Py[Python services]
    CTX[context-data]
    FS[features: offline + online]
    ML[ml train / infer]
    BT[backtest]
    OPT[optimizer]
    AST[assistant]
    MON[monitor]
  end
  subgraph Storage
    LAKE[(Parquet lake + DuckDB)]
    PG[(PostgreSQL)]
    RD[(Redis / Valkey)]
    OBJ[(Artifact store)]
  end
  BUS{{Event bus}}
  GW[api-gateway]
  UI[Web UI: desktop + mobile]

  ALP <--> BRK
  IBG <--> BRK
  BRK --> MD --> BUS
  MD --> LAKE
  MD --> RD
  NEWS --> CTX
  MACRO --> CTX
  CTX --> LAKE
  CTX --> BUS
  TV --> GW
  BUS --> FS --> ML
  FS --> RD
  ML -- signals --> BUS
  BUS -- order intents --> OMS --> BRK
  BRK -- executions --> BUS --> LED --> PG
  OMS --> PG
  LAKE --> BT --> OPT --> ML --> OBJ
  AST --> OPT
  BUS --> MON --> BUS
  GW <--> UI
  GW --> BUS
  GW --> PG
```

## The two loops

**Trading path (latency-sensitive, fail-closed):**
market data → features → model → signal → strategy (sizing, rules) → `OrderIntent` → pre-trade risk → OMS → broker adapter → `ExecutionReport` → ledger → reconciliation → monitoring.
Rules: [orders and execution](../business-logic/orders-and-execution.md), [risk](../business-logic/risk-management.md), [virtual portfolios](../business-logic/virtual-portfolios-and-attribution.md).

**Research loop (throughput-oriented, reproducible):**
data snapshot → features + labels → train → backtest + validation → optimize → register → shadow/paper → live → monitor → retrain.
Rules: [data and features](../business-logic/data-and-features.md), [strategies and model lifecycle](../business-logic/strategies-and-model-lifecycle.md).

## Cross-cutting design

| Concern | Design | Decision |
|---|---|---|
| Contracts | All cross-service messages in `contracts/proto/tb/<domain>/v1/`; generated (committed) for Go, Python, TypeScript; `make gen-check` fails CI on stale code; internal RPC is gRPC, UI is REST + WebSocket ([contracts](#contracts)) | D3 ([ADR-0003](../adr/0003-contracts-protobuf-buf.md)) |
| Messaging | Durable streams for order path (`ord.*`, `exec.*`, `risk.kill`), at-least-once, idempotent consumers | D2 |
| Storage split | Market/context data in the lake (single writer, many readers); transactional state (orders, ledgers, users, audit) in Postgres; hot bars and online features in cache | D4–D6 |
| Time | UTC int64 ns; `event_time` / `available_at` / `ingested_at` on every market and context record | — |
| Identity | ULIDs; `instrument_id` independent of ticker; `correlation_id` from signal to fill | — |
| Modes | `mode ∈ {backtest, paper, live}` and `broker_account_id` on every strategy, order, and fill | — |
| Security | Authenticated gateway, step-up for dangerous actions, encrypted credentials only in broker-gateway, private network only, hash-chained audit log | D15–D17 |
| Observability | Metrics, logs, traces per service; trade audit trail | D22 |
| Deployment | Mac Mini host: Docker Compose under Colima (boot-time LaunchDaemon), native launchd ML workers; same Compose on a droplet later | D21, D28, D29 |

## Contracts

| Package | Messages / services | Producer → consumers |
|---|---|---|
| `tb.common.v1` | `Decimal`, `Mode`, `AssetClass`, `Side`, `Broker`, `Instrument`, `Provenance` | shared |
| `tb.marketdata.v1` | `Bar`, `Quote`, `Trade`, `CorporateAction` | marketdata → bus, lake, cache |
| `tb.context.v1` | `ContextEvent` | context-data → lake, bus |
| `tb.features.v1` | `FeatureVector` | feature service → ml-infer |
| `tb.signals.v1` | `Signal` | ml-infer / strategy → execution |
| `tb.orders.v1` | `OrderIntent`, `RiskDecision`, `Order`, `ExecutionReport`, `KillSwitchEvent` | strategy/UI → execution → broker-gateway → ledger |
| `tb.ledger.v1` | `AccountSnapshot`, `StrategyPosition`, `LedgerEntry`, `ReconciliationResult` | broker-gateway, ledger → UI, execution, monitor |
| `tb.ml.v1` | `ModelVersion`, `BacktestRun` | ml, backtest, optimizer → registry, UI |
| `tb.alerts.v1` | `Alert` | any → monitor → channels |
| `tb.audit.v1` | `AuditEvent` | all → audit store |
| `tb.broker.v1` | `CredentialService` (gRPC: put, delete, list status; no plaintext read) | api-gateway → broker-gateway |

## Bus subjects (planned)

```
md.bar.<timeframe>.<instrument_id>   md.quote.<instrument_id>   md.trade.<instrument_id>
ctx.<kind>                            feat.<feature_set>.<instrument_id>
sig.<strategy_id>                     ord.intent   ord.decision   ord.update.<broker_account>
exec.<broker_account>                 acct.snapshot.<broker_account>
ledger.update.<broker_account>        recon.result.<broker_account>
risk.kill                             alert.<severity>           sys.health.<service>
```

Update this doc when a data flow, subject, store, or trust boundary changes.
