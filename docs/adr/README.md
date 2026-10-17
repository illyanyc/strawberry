# Architecture Decision Records

One file per significant decision: stack choices, service boundaries, contract shapes, storage layout, security model, and any rule that constrains future work. The current status of every platform decision (D1–D27) is in [`../decisions.md`](../decisions.md).

## Process

1. Copy [`template.md`](template.md) to `NNNN-short-kebab-title.md` with the next free number (4 digits, never reused).
2. Fill it in. Status starts as `Proposed`; set `Accepted` when the owner (or the assigned task) approves.
3. In the same commit: add it to the index below, update `../decisions.md` (if it settles a `D` item), replace affected `DECISION PENDING` markers, and add a changelog entry.
4. A provisional ADR accepted by the research report gets its status updated and a dated "Research outcome" section appended; the original decision text is not edited. A decision the research *changes* gets a new ADR.
5. To change a decision, write a new ADR that supersedes the old one. Set the old one's status to `Superseded by ADR-NNNN`; do not edit its decision text.

Write an ADR when a choice is hard to reverse, affects more than one service, or a future agent would otherwise ask "why is it like this?". Skip it for local implementation details.

## Index

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-agent-documentation-structure.md) | Agent documentation structure and protocol | Accepted | 2026-10-17 |
| [0002](0002-monorepo-layout-and-toolchain.md) | Monorepo layout, workspaces, and pinned toolchain | Accepted | 2026-10-17 |
| [0003](0003-contracts-protobuf-buf.md) | Contracts in Protobuf with buf; Go/Python/TS codegen | Accepted (research) | 2026-10-17 |
| [0004](0004-core-language-split.md) | Go for core services, Python for ML and research | Accepted (research) | 2026-10-17 |
| [0005](0005-postgres-layout-and-migrations.md) | PostgreSQL layout, least-privilege roles, and migrations | Accepted (research) | 2026-10-17 |
| [0006](0006-audit-log-hash-chain.md) | Tamper-evident audit log as a Postgres hash chain | Accepted | 2026-10-17 |
| [0007](0007-service-to-service-auth.md) | Service-to-service authentication with short-lived Ed25519 tokens | Accepted | 2026-10-17 |
| [0008](0008-event-bus-nats-jetstream.md) | NATS JetStream as the event bus | Accepted (research) | 2026-10-17 |
| [0010](0010-account-routing-and-day-trade-policy.md) | Account routing, PDT guard off by default, and an intraday-margin guard | Accepted (research) | 2026-10-17 |
