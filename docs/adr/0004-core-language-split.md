# ADR-0004: Go for core services, Python for ML and research

- **Status:** Accepted (provisional: default, pending research §7)
- **Date:** 2026-10-17
- **Decision register:** D1
- **Related:** [ADR-0002](0002-monorepo-layout-and-toolchain.md), [ADR-0003](0003-contracts-protobuf-buf.md)

## Context

The order path (broker adapters, OMS, risk, ledger, gateway) needs predictable latency (U8: p99 < 500 ms decision-to-submit), strong typing, easy concurrency, and small static binaries that run on a Mac Mini (linux/arm64 containers) and later a droplet. ML, backtesting, and research need the Python ecosystem (pandas/Polars, LightGBM, PyTorch/MPS, MLflow, Optuna).

## Decision

- **Go** for `api-gateway`, `broker-gateway`, `marketdata`, `execution`, `ledger`, shared `libs/go`, and the ops CLI `tools/tbctl`.
- **Python** for `context-data`, `ml`, `backtest`, `optimizer`, `assistant`, `monitor`, and `libs/py/tbcore` (features, labels, strategy API shared by train/backtest/live).
- The boundary is the contracts in `contracts/` (Protobuf over NATS and gRPC), so either side can be rewritten without touching the other.
- Go images: static `CGO_ENABLED=0` binaries on distroless (no shell). Python images: slim base, non-root, read-only root filesystem.

## Alternatives considered

| Option | Why not |
|---|---|
| All Python | Simpler stack, but GIL/async complexity and weaker typing on the order path; larger images. |
| Rust core | Best performance and safety, but slower iteration for a team of one; seconds-level latency does not need it. |
| All Go | No credible ML/backtesting ecosystem. |

## Consequences

- Positive: typed, fast, small order-path services; full ML ecosystem where it matters.
- Negative / risks: two toolchains; strategy code lives in Python (`tbcore`), so live strategy hosting needs a Python runtime next to the Go OMS (decided in section 07).
- Follow-up: redaction, config, and service-template conventions are mirrored in both languages (`libs/go`, `libs/py/tbcore`).

## Validation

Phase 2 latency measurements (U8) on the Mac Mini; revisit if the Go/Python seam causes duplicated logic.
