# ADR-0031: Two-tier backtester on `tbcore` with the live order path in a Go simcore

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D7 (implemented), D49
- **Related:** BT-001..BT-008, BT-020..BT-025, BT-070, BT-071, SM-011, SM-013, SM-014; ADR-0024, ADR-0025, ADR-0026, ADR-0029; research [02 §4.1](../research/02-labeling-features-validation.md)

## Context

D7 accepted a custom two-tier engine on `tbcore`: a vectorized screening tier and an event-driven tier that replays recorded bus messages through the live strategy API and cost model. Section 08 adds that the event-driven tier must use the live sizing, risk checks, attribution, and ledger math, and that "any duplicated logic is a defect". The live pre-trade gate and OMS are Go (`services/execution/internal/oms`, `internal/risk`), the ledger's accounting core is Go (`services/ledger/book`), and strategies are Python (`tbcore.strategy`, hosted by the runtime core). Gate 3 requires the backtester to replay the ORB baseline's paper stream and match its trades.

## Decision

1. **Event-driven tier (`tbcore.backtest.event`)**, Python, owns the clock, the data, and fill decisions:
   - strategies run through the live runtime core (`Runner`, moved into `tbcore.strategy.host`), fed final bars in `available_at` order from the lake or from a **bus recording** (`tb.backtest.v1.RecordedMessage` stream of `md.bar.*`, `ord.*`, `exec.*`, `ledger.update.*` messages);
   - fills come from the shared cost and fill models (ADR-0029), or, for parity replays, from the recorded executions.
2. **`tb-simcore`** (`services/execution/cmd/tb-simcore`, package `internal/simcore`), Go, owns every order-path decision: it hosts the **real OMS engine** (`oms.New` with the in-memory store), seeded from the same registry JSON as production (`app.Seed`), behind
   - a passive simulated broker (records orders, Alpaca-style held bracket legs and broker-side OCO, or IBKR-style stops placed by the OMS after the entry fill; never decides a fill), and
   - a ledger adapter implementing `oms.Ledger` over `services/ledger/book` (owner attribution, constraints, reservations, capital, owner views with day P&L and high-water mark, as the ledger engine computes them).
   The two processes talk over stdin/stdout with length-delimited `tb.backtest.v1.SimRequest`/`SimResponse` messages: no sockets, no files, no credentials, one response per request.
3. **Vectorized tier (`tbcore.backtest.vectorized`)**: NumPy over a time × instrument signal matrix with next-bar execution, the same cost model, and bar high/low stops; screening only (BT-006).
4. **Runs are identified by the hash of a serializable `BacktestConfig`** (strategies as registry entries, universe with survivorship status, data snapshot, cost and fill models, account, seed); results are written to the filesystem artifact store (D8) with the `BacktestRun` contract summary.

### Determinism and performance (measured)

- The OMS's wall clock in the simcore is the virtual clock plus one microsecond per read, so periodic work keyed on wall time (order reconciliation every 30 s, dead-man ages) depends only on the event sequence, never on host speed; wait loops still progress.
- The OMS tick runs only where its time-driven work can act (the intraday flatten window and outside the session); every submit carries the clock and current marks for held and working instruments.
- The live OMS keeps every order, intent, and fill in memory and scans them per intent (harmless at live volumes). `oms.Engine.Compact` drops finished history older than ten days at simulated session closes; two scans now check finality and time before string comparisons (behavior unchanged).
- Result: 1 year × 100 symbols × 5-minute bars (1,954,800 bars, ORB, ~40,000 simcore requests) in 32.9 s on the 4-vCPU CI-class VM (target 60 s); three runs of the same config give the same result fingerprint.

## Alternatives considered

| Option | Why not |
|---|---|
| Re-implement the gate and lot math in Python | Duplicated order-path logic drifts from production; section 08 calls it a defect |
| Run the paper harness (NATS, gRPC, simulator) as the backtester | Faithful but wall-clock bound (minutes per simulated session); cannot meet 1 year × 100 symbols in 60 s |
| gRPC between Python and the simcore | ~150–300 µs per unary call from Python; stdio framing is ~20–40 µs and needs no port |
| cgo shared library loaded with ctypes | Cross-compiling a c-shared library for macOS arm64 and Linux complicates the toolchain; a static binary is simpler |
| Move `risk` and `book` into `libs/go` | Larger move across two services' ownership; exporting `book` (anticipated by ADR-0024) and hosting the simcore inside the execution tree keeps ownership where it is |
| NautilusTrader / LEAN | Rejected by D7 (second OMS, no Alpaca adapter / paid CLI) |

## Consequences

- Positive: intents, decisions (all 32 checks plus capital), order construction (collars, brackets, cancel-before-exit, flatten before the close), reservations, FIFO lots, and P&L in a backtest come from production code; the parity gate tests the whole path.
- Negative / risks: the backtester needs the `tb-simcore` binary (built with `go build`, located by `TB_SIMCORE_BIN` or built on demand into the user cache); the OMS generates random internal order IDs, so results key orders by broker order ID and attribution ID, which are deterministic. Account snapshots in simulation follow the paper simulator's convention (buying power = cash).
- Follow-up: include the binary in the backtest image when the backtest service runs in Compose; the recorder that pulls recordings from JetStream for live/paper periods (Planned).

## Validation

`internal/simcore` tests (gate approval with every check, bracket construction, OCO, FIFO P&L with fees, ownership rejection with manual shares, stale-data rejection, IBKR-style GTC stop, stdio transport); the Gate 3 parity test (recorded paper ORB stream replayed with recorded and modeled fills); determinism and performance tests of the event-driven tier.
