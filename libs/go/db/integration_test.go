package db_test

// Integration tests against a real Postgres. They run when TB_TEST_PG_SUPER_DSN is set (CI sets it
// via a postgres service container; locally: `make test-integration`), and skip otherwise.

import (
	"context"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/db"
)

const testDB = "platform_test"

func setup(t *testing.T) (super string, connect func(role string) *pgxpool.Pool) {
	t.Helper()
	super = os.Getenv("TB_TEST_PG_SUPER_DSN")
	if super == "" {
		t.Skip("TB_TEST_PG_SUPER_DSN not set")
	}
	ctx := context.Background()
	admin, err := pgx.Connect(ctx, super)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = admin.Exec(ctx, "DROP DATABASE IF EXISTS "+testDB+" WITH (FORCE)")
	admin.Close(ctx)

	pw := map[string]string{}
	for _, r := range append(append([]string{}, db.LoginRoles...), db.AppDatabases...) {
		pw[r] = "test-pw-" + r
	}
	if err := db.Bootstrap(ctx, super, testDB, pw); err != nil {
		t.Fatalf("bootstrap: %v", err)
	}
	// Idempotent: a second run is a no-op.
	if err := db.Bootstrap(ctx, super, testDB, pw); err != nil {
		t.Fatalf("second bootstrap: %v", err)
	}
	cfg, _ := pgx.ParseConfig(super)
	return super, func(role string) *pgxpool.Pool {
		c := db.Config{Host: cfg.Host, Port: int(cfg.Port), Name: testDB, User: role, SSLMode: "disable"}
		pool, err := pgxpool.New(ctx, c.DSN(pw[role]))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(pool.Close)
		return pool
	}
}

// SE-020/SE-022/SE-023: append through the definer function, verify, reject direct writes, detect tampering.
func TestSE020AuditChainEndToEnd(t *testing.T) {
	super, connect := setup(t)
	ctx := context.Background()
	gw := connect("svc_api_gateway")
	w := audit.NewWriter(gw)
	for i, action := range []string{"auth.login.success", "settings.update", "auth.logout"} {
		seq, hash, err := w.Append(ctx, audit.Event{Actor: "owner", Action: action, Target: "t",
			Details: map[string]any{"i": i, "password": "never-stored-123"}})
		if err != nil || seq != int64(i+1) || len(hash) != 64 {
			t.Fatalf("append %d: seq=%d hash=%q err=%v", i, seq, hash, err)
		}
	}
	ops := connect("svc_ops")
	res, err := audit.Verify(ctx, ops)
	if err != nil || res.Count != 3 {
		t.Fatalf("verify clean chain: %+v %v", res, err)
	}
	var details string
	if err := ops.QueryRow(ctx, `SELECT details_json FROM audit.events WHERE seq=1`).Scan(&details); err != nil ||
		strings.Contains(details, "never-stored-123") {
		t.Fatalf("details not redacted: %s %v", details, err)
	}

	for _, q := range []string{
		`UPDATE audit.events SET actor='x' WHERE seq=1`,
		`DELETE FROM audit.events WHERE seq=1`,
		`INSERT INTO audit.events (seq,event_id,occurred_at,actor,action,prev_hash,hash) VALUES (99,'x',1,'a','b','\x00','\x00')`,
	} {
		if _, err := gw.Exec(ctx, q); err == nil {
			t.Fatalf("writer role could run %q", q)
		}
	}

	// Tamper as superuser (bypassing the append-only trigger) → verification must fail.
	cfg, _ := pgx.ParseConfig(super)
	cfg.Database = testDB
	admin, err := pgx.ConnectConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close(ctx)
	if _, err := admin.Exec(ctx, `ALTER TABLE audit.events DISABLE TRIGGER events_no_update;
		UPDATE audit.events SET target='tampered' WHERE seq=2;
		ALTER TABLE audit.events ENABLE TRIGGER events_no_update`); err != nil {
		t.Fatal(err)
	}
	res, err = audit.Verify(ctx, ops)
	if !errors.Is(err, audit.ErrChainBroken) || res.BrokenAt != 2 {
		t.Fatalf("tampering not detected: %+v %v", res, err)
	}
}

// SE-032/SE-050: least-privilege schema access per service role.
func TestSE032SchemaIsolation(t *testing.T) {
	_, connect := setup(t)
	ctx := context.Background()
	deny := map[string][]string{
		"svc_api_gateway": {`SELECT * FROM secrets.broker_credentials`, `SELECT * FROM research.docs`, `SELECT * FROM holdout.docs`},
		"svc_ops":         {`SELECT * FROM secrets.broker_credentials`, `SELECT * FROM auth.sessions`},
		"svc_marketdata":  {`SELECT * FROM secrets.broker_credentials`, `SELECT * FROM auth.sessions`, `SELECT * FROM audit.events`},
		"svc_broker_gateway": {`SELECT * FROM auth.sessions`, `SELECT * FROM config.settings`,
			// BR-013: live enablement is written only by the audited owner action (section 15).
			`INSERT INTO broker.live_enablement (broker_account_id, enabled, enabled_by, audit_seq) VALUES ('a', true, 'x', 1)`},
		"svc_execution": {`SELECT * FROM broker.executions`, `SELECT * FROM broker.outbox`,
			`INSERT INTO refdata.instruments (instrument_id, asset_class) VALUES ('x', 'us_equity')`},
		"svc_ledger": {`SELECT * FROM marketdata.lake_files`, `DELETE FROM refdata.corporate_actions`},
		// SE-035 / EX-012: only svc_holdout reaches the sealed holdout; research records are ml/optimizer only.
		"svc_optimizer": {`SELECT * FROM holdout.docs`},
		"svc_ml":        {`SELECT * FROM holdout.docs`, `SELECT * FROM secrets.broker_credentials`},
		"svc_holdout":   {`SELECT * FROM research.docs`},
		// AN-030 / AN-032: the analyst reads research only through the services, never the holdout,
		// credentials, the order path, or the ledger; it cannot write reference data.
		"svc_assistant": {`SELECT * FROM holdout.docs`, `SELECT * FROM research.docs`, `SELECT * FROM secrets.broker_credentials`,
			`SELECT * FROM broker.executions`, `SELECT * FROM auth.sessions`, `SELECT * FROM config.settings`,
			`INSERT INTO refdata.instruments (instrument_id, asset_class) VALUES ('x', 'us_equity')`},
	}
	for role, queries := range deny {
		pool := connect(role)
		for _, q := range queries {
			if _, err := pool.Exec(ctx, q); err == nil || !strings.Contains(err.Error(), "permission denied") {
				t.Errorf("%s: expected permission denied for %q, got %v", role, q, err)
			}
		}
	}
	allow := map[string]string{
		"svc_broker_gateway": `SELECT count(*) FROM secrets.broker_credentials`,
		"svc_api_gateway":    `SELECT count(*) FROM auth.sessions`,
	}
	for role, q := range map[string]string{
		"svc_marketdata":     `INSERT INTO refdata.instruments (instrument_id, asset_class) VALUES ('01TEST', 'us_equity')`,
		"svc_ledger":         `SELECT count(*) FROM refdata.corporate_actions`,
		"svc_broker_gateway": `SELECT count(*) FROM refdata.broker_ids`,
		"svc_ops":            `SELECT count(*) FROM marketdata.lake_files`,
		"svc_ml":             `SELECT count(*) FROM research.docs`,
		"svc_optimizer":      `SELECT count(*) FROM research.docs`,
		"svc_holdout":        `SELECT count(*) FROM holdout.docs`,
		"svc_assistant":      `SELECT count(*) FROM analyst.rounds`,
	} {
		if _, err := connect(role).Exec(ctx, q); err != nil {
			t.Errorf("%s: %q should be allowed: %v", role, q, err)
		}
	}
	for _, q := range []string{`SELECT count(*) FROM broker.executions`, `SELECT count(*) FROM broker.live_enablement`, `SELECT count(*) FROM broker.outbox`} {
		if _, err := connect("svc_broker_gateway").Exec(ctx, q); err != nil {
			t.Errorf("svc_broker_gateway: %q should be allowed: %v", q, err)
		}
	}
	for role, q := range allow {
		if _, err := connect(role).Exec(ctx, q); err != nil {
			t.Errorf("%s: %q should be allowed: %v", role, q, err)
		}
	}
	if _, err := connect("svc_assistant").Exec(ctx, `SELECT count(*) FROM refdata.symbols`); err != nil {
		t.Errorf("svc_assistant: refdata read (masking, AN-051) should be allowed: %v", err)
	}
	// AN-055: the analyst's LLM call log is append-only, even for its writer.
	asst := connect("svc_assistant")
	if _, err := asst.Exec(ctx, `INSERT INTO analyst.llm_calls (call_id, role, provider, model, prompt_sha256, prompt, response,
		input_tokens, output_tokens, cost_usd, latency_ms, ok, error, lineage_id, round_id, created_at)
		VALUES ('c1', 'planner', 'fake', 'm', 'h', 'p', 'r', 1, 1, 0, 0, true, '', 'l', 'r', now())`); err != nil {
		t.Fatalf("svc_assistant insert: %v", err)
	}
	if _, err := asst.Exec(ctx, `DELETE FROM analyst.llm_calls WHERE call_id = 'c1'`); err == nil {
		t.Error("analyst.llm_calls delete should be refused")
	}
	// EX-I1 / EX-I2: research and holdout records are append-only, even for their writers.
	opt := connect("svc_optimizer")
	if _, err := opt.Exec(ctx, `INSERT INTO research.docs (kind, id, created_at, updated_at, payload) VALUES ('trial', 't1', 1, 1, '\x00')`); err != nil {
		t.Fatalf("svc_optimizer insert: %v", err)
	}
	if _, err := opt.Exec(ctx, `DELETE FROM research.docs WHERE id = 't1'`); err == nil {
		t.Error("research.docs delete should be refused")
	}
	if _, err := connect("svc_holdout").Exec(ctx, `DELETE FROM holdout.docs`); err == nil {
		t.Error("holdout.docs delete should be refused")
	}
}
