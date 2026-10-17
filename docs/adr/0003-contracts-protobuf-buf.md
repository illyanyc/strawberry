# ADR-0003: Contracts in Protobuf, managed with buf, generated for Go, Python, and TypeScript

- **Status:** Accepted (research)
- **Date:** 2026-10-17
- **Decision register:** D3
- **Related:** master prompt "Service contracts", section 01 Stage A, rules OP-003, DF-001, VP-002, SE-030, SE-031

## Context

Go core services, Python ML/research services, and the TypeScript UI must agree on message shapes (bars, intents, orders, executions, audit events). Drift between hand-written copies is a classic source of order-path bugs. The master prompt defaults to gRPC + Protobuf with `buf` (high confidence).

## Decision

- All cross-service messages live in `contracts/proto/tb/<domain>/v1/`. Package root `tb` (not `strawberry`, which collides with a popular Python package name).
- `buf lint` (STANDARD) and `buf breaking` (FILE, against `origin/main`) run in CI.
- Codegen uses **local plugins only**: `protoc-gen-go` + `protoc-gen-go-grpc` (Go), protoc's built-in `python` + `pyi` generators (Python, pinned `protoc` 36.2 with `protobuf==7.36.2` runtime), and `protoc-gen-es` v2 (TypeScript). No dependency on the Buf Schema Registry at build time.
- Generated code is committed. `make gen-check` regenerates and fails if anything changed, so a contract change without regenerated consumers fails CI.
- Money and quantity use `tb.common.v1.Decimal` (string); timestamps are int64 UTC ns; IDs are ULID strings.
- Internal RPC is gRPC (first use: `tb.broker.v1.CredentialService`); the UI talks REST + WebSocket to the gateway, specified in `contracts/openapi/`.

## Alternatives considered

| Option | Why not |
|---|---|
| Remote BSR plugins | Rate limits and network dependency in CI; local plugins are pinned and offline. |
| JSON Schema as the source of truth | Weak typing for Go; no RPC story. JSON Schemas will be generated from proto for UI validation later. |
| Cap'n Proto / FlatBuffers | Faster zero-copy, but far weaker tooling for Python/TS and breaking-change detection. |
| Not committing generated code | Every consumer build would need the full toolchain; reviewers could not see generated diffs. |

## Consequences

- Positive: one schema, three languages, breaking-change detection, stale-code gate.
- Negative / risks: protoc and the Python protobuf runtime must move in lockstep (pinned together in `tools/versions.env` and `contracts/gen/python/pyproject.toml`).
- Follow-up: JSON Schema generation for the gateway's order-path validation (Phase 1); `buf breaking` becomes meaningful once `main` has contracts.

## Validation

`make gen-check`, `make lint-proto`, and the round-trip tests in Go, Python, and TypeScript pass in CI; a proto edit without `make gen` fails CI.

## Research outcome (2026-10-17)

Research [09 §D3](../research/09-decisions.md#d3) confirms the decision. The UI transport is REST + **SSE** (D14), not WebSocket; the gateway's streams are specified in `contracts/openapi/gateway.yaml`.
