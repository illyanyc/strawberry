# contracts

Single source of truth for every cross-service message and RPC. Services never invent private formats; they extend these files, regenerate, then implement.

**Status:** In progress (Phase 0 baseline messages)  ·  **Stack:** Protobuf + `buf`, codegen to Go, Python, TypeScript ([ADR-0003](../docs/adr/0003-contracts-protobuf-buf.md), D3)

## Responsibilities
- Owns: `.proto` schemas, codegen config, generated packages, the gateway OpenAPI spec.
- Does not own: business logic, persistence schemas (those live with their service's migrations).

## Layout
| Path | Purpose |
|---|---|
| `proto/tb/<domain>/v1/*.proto` | Schemas, one package per domain (`tb.common.v1`, `tb.marketdata.v1`, `tb.orders.v1`, ...) |
| `buf.yaml`, `buf.gen.yaml` | Lint (STANDARD), breaking (FILE), local-plugin codegen |
| `gen/go/` | Go module `github.com/illyanyc/strawberry/contracts/gen/go` (messages + gRPC) |
| `gen/python/` | Python package `tbcontracts` (import as `tb.<domain>.v1.<file>_pb2`, with `.pyi` stubs) |
| `gen/ts/` | pnpm package `@strawberry/contracts` (protoc-gen-es v2, `target=ts`) |
| `openapi/` | Gateway REST API (hand-written OpenAPI 3.1) |
| `tests/` | Python round-trip tests (Go test lives in `gen/go/contracts_test.go`, TS type test in `gen/ts/test/`) |

Only `*.pb.go`, `*_pb2.py(i)`, and `*_pb.ts` files are generated; `go.mod`, `package.json`, `pyproject.toml`, `tsconfig.json`, and tests under `gen/` are hand-written and survive `make gen`.

## Conventions
- Times: UTC int64 ns. Money/qty: `tb.common.v1.Decimal` (string). IDs: ULID strings. Every order-path event carries `correlation_id`.
- Breaking changes need a new package version (`v2`) and a migration note in the PR; `buf breaking` runs against `origin/main`.
- Comments in `.proto` files are the field documentation; cite business-rule IDs where a field enforces one.

## Run and test
- Regenerate: `make gen` (needs `make tools` and `pnpm install`).
- Stale check (CI): `make gen-check`; lint + breaking: `make lint-proto`.
- Tests: `go test ./contracts/gen/go/...`, `uv run pytest contracts/tests`, `pnpm --filter @strawberry/contracts typecheck`.

## Gotchas
- `protoc_builtin` Python generation needs the pinned `protoc` in `.tools/bin`; the Python runtime (`protobuf==7.36.2`) must match protoc 36.x or imports fail at runtime.
- JSON Schema generation (`contracts/jsonschema/`) is Planned for Phase 1 when the gateway starts validating order payloads.
