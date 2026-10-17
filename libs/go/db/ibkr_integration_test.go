package db_test

import (
	"context"
	"strings"
	"testing"
)

// SE-032/SE-050, IB-062, ADR-0020: svc_broker_gateway_ibkr may append audit events, use its own
// broker_ibkr schema, and read broker.live_enablement; nothing else. Other services cannot read
// broker_ibkr.
func TestIB062IbkrRoleIsolation(t *testing.T) {
	_, connect := setup(t)
	ctx := context.Background()
	ibkr := connect("svc_broker_gateway_ibkr")
	for _, q := range []string{
		`SELECT * FROM secrets.broker_credentials`,
		`SELECT * FROM auth.sessions`,
		`SELECT * FROM audit.events`,
		`SELECT * FROM config.settings`,
		`SELECT * FROM broker.executions`,
		`SELECT * FROM broker.outbox`,
		`INSERT INTO broker.live_enablement (broker_account_id, enabled, enabled_by, audit_seq) VALUES ('a', true, 'x', 1)`,
	} {
		if _, err := ibkr.Exec(ctx, q); err == nil || !strings.Contains(err.Error(), "permission denied") {
			t.Errorf("svc_broker_gateway_ibkr: expected permission denied for %q, got %v", q, err)
		}
	}
	for _, q := range []string{
		`SELECT seq FROM audit.append('ibkr-test', 1, 'broker-gateway-ibkr', 'broker.order.submit', 'tb1-test', '', '', '', '', '', '{}')`,
		`SELECT count(*) FROM broker.live_enablement`,
		`INSERT INTO broker_ibkr.outbox (subject, msg_id, payload) VALUES ('exec.x', 'exec:x:1', '\x00')`,
		`UPDATE broker_ibkr.outbox SET published_at = now()`,
		`INSERT INTO broker_ibkr.state (key, value) VALUES ('last_ready_at', 1)`,
		`SELECT count(*) FROM broker_ibkr.executions`,
	} {
		if _, err := ibkr.Exec(ctx, q); err != nil {
			t.Errorf("svc_broker_gateway_ibkr: %q should be allowed: %v", q, err)
		}
	}
	for _, role := range []string{"svc_broker_gateway", "svc_execution", "svc_ledger", "svc_api_gateway"} {
		if _, err := connect(role).Exec(ctx, `SELECT * FROM broker_ibkr.executions`); err == nil || !strings.Contains(err.Error(), "permission denied") {
			t.Errorf("%s: expected permission denied on broker_ibkr, got %v", role, err)
		}
	}
}
