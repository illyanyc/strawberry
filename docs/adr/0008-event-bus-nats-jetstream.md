# ADR-0008: NATS JetStream as the event bus

- **Status:** Accepted (provisional: default, pending research §7)
- **Date:** 2026-10-17
- **Decision register:** D2
- **Related:** master "Bus subjects"; rules OE-006, OP-030

## Context

The platform needs durable, at-least-once delivery for the order path (`ord.*`, `exec.*`, `risk.kill`), cheap fan-out for market data, and request/reply. It must run on a Mac Mini with modest memory and ship linux/arm64 images.

## Decision

- **NATS 2.x with JetStream** (`nats:2-alpine`, multi-arch), single node, file storage on a named volume, password auth per service user from mounted secrets.
- Streams (created by `tbctl bus init`, defined in `libs/go/bus`): `ORDERS` and `LEDGER` (file, 90 d, 10-min dedupe window), `SIGNALS`, `ALERTS`, `CONTEXT` (file), `MARKETDATA` (`md.>`, `feat.>`; memory, 1 h, 512 MiB, discard old), `SYS` (memory, 10 min).
- Publishers set `Nats-Msg-Id` for dedupe and a `Tb-Correlation-Id` header; consumers stay idempotent on `intent_id`, `client_order_id`, `broker_exec_id` (OE-006).

## Alternatives considered

| Option | Why not |
|---|---|
| Redis Streams | Already have Valkey, but weaker consumer semantics and persistence guarantees for the order path. |
| Redpanda / Kafka | Heavy for one host; JVM-free Redpanda still wants GBs of RAM. |
| Postgres LISTEN/NOTIFY + outbox | Fine for low volume, poor for market-data fan-out. |

## Consequences

- Positive: one small binary covers durable streams, fan-out, and request/reply.
- Negative / risks: single node (no replication) until a second host exists; JetStream file store lives on a Docker volume (included in backups as needed).

## Validation

Compose smoke test checks NATS health and stream creation; Phase 1/2 gates measure latency and redelivery behavior.
