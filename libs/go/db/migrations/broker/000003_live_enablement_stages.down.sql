REVOKE SELECT, INSERT, UPDATE ON broker.live_enablement FROM svc_execution;
REVOKE USAGE ON SCHEMA broker FROM svc_execution;
ALTER TABLE broker.live_enablement DROP COLUMN IF EXISTS reason;
ALTER TABLE broker.live_enablement DROP COLUMN IF EXISTS orders_enabled;
