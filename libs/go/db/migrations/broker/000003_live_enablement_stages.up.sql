-- Section 15 (ADR-0054, GL-011, GL-012): the audited live enablement has two stages. `enabled`
-- lets the live adapter run read-only (account snapshots and reconciliation, no orders);
-- `orders_enabled` is the trading stage. Only execution's go-live flow writes the row, after the
-- owner's passkey step-up and typed confirmation; the adapters only read it (BR-013).
ALTER TABLE broker.live_enablement ADD COLUMN orders_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE broker.live_enablement ADD COLUMN reason text NOT NULL DEFAULT '';

GRANT USAGE ON SCHEMA broker TO svc_execution;
GRANT SELECT, INSERT, UPDATE ON broker.live_enablement TO svc_execution;
