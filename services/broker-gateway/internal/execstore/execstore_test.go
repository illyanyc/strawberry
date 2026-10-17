package execstore_test

import (
	"context"
	"os"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/execstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/livegate"
)

const testDB = "broker_store_test"

func pool(t *testing.T) (*pgxpool.Pool, *pgxpool.Pool) {
	t.Helper()
	super := os.Getenv("TB_TEST_PG_SUPER_DSN")
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
		pw[r] = "pw-" + r
	}
	if err := db.Bootstrap(ctx, super, testDB, pw); err != nil {
		t.Fatal(err)
	}
	cfg, _ := pgx.ParseConfig(super)
	c := db.Config{Host: cfg.Host, Port: int(cfg.Port), Name: testDB, User: "svc_broker_gateway", SSLMode: "disable"}
	p, err := pgxpool.New(ctx, c.DSN(pw["svc_broker_gateway"]))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(p.Close)
	scfg, _ := pgxpool.ParseConfig(super)
	scfg.ConnConfig.Database = testDB
	sp, err := pgxpool.NewWithConfig(ctx, scfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(sp.Close)
	return p, sp
}

type pub struct{ got []outbox.Message }

func (p *pub) Publish(_ context.Context, m outbox.Message) error {
	p.got = append(p.got, m)
	return nil
}

func TestBR008StoreIntegration(t *testing.T) {
	p, _ := pool(t)
	ctx := context.Background()
	s := &execstore.Postgres{Pool: p}
	e := &ordersv1.ExecutionReport{BrokerExecId: "o1:5", BrokerOrderId: "o1", ClientOrderId: "tb1-x", Symbol: "AAPL", Side: commonv1.Side_SIDE_BUY,
		Qty: &commonv1.Decimal{Value: "5"}, Price: &commonv1.Decimal{Value: "101.25"}, EventTime: 1000}
	u := &brokerv1.BrokerOrderUpdate{Order: &brokerv1.BrokerOrder{BrokerOrderId: "o1", BrokerStatus: "filled", FilledQty: &commonv1.Decimal{Value: "5"}, UpdatedAt: 1000}}
	for i := range 3 {
		isNew, err := s.RecordExecution(ctx, "alpaca-paper", e, u)
		if err != nil || isNew != (i == 0) {
			t.Fatalf("record #%d: new=%v err=%v", i, isNew, err)
		}
		isNew, err = s.RecordOrderUpdate(ctx, "alpaca-paper", u)
		if err != nil || isNew != (i == 0) {
			t.Fatalf("order update #%d: new=%v err=%v", i, isNew, err)
		}
	}
	if id, ts, err := s.Cursor(ctx, "alpaca-paper"); err != nil || id != "o1:5" || ts != 1000 {
		t.Fatalf("cursor %s %d %v", id, ts, err)
	}
	pb := &pub{}
	r := outbox.NewRelay(p, "broker.outbox", pb, nil, nil)
	if n, err := r.Drain(ctx); err != nil || n != 2 {
		t.Fatalf("relay published %d (%v); want exec + order update exactly once", n, err)
	}
	if pb.got[0].Subject != "exec.alpaca-paper" || pb.got[0].MsgID != "exec:alpaca-paper:o1:5" || pb.got[1].Subject != "ord.update.alpaca-paper" {
		t.Fatalf("published %+v", pb.got)
	}
}

func TestBR013LiveGateIntegration(t *testing.T) {
	p, super := pool(t)
	ctx := context.Background()
	if ok, _ := livegate.New(false, p).Allowed(ctx, "alpaca-live"); ok {
		t.Fatal("flag off must close the gate")
	}
	if ok, why := livegate.New(true, p).Allowed(ctx, "alpaca-live"); ok || why == "" {
		t.Fatal("no enablement row must close the gate")
	}
	if _, err := super.Exec(ctx, `INSERT INTO broker.live_enablement (broker_account_id, enabled, enabled_by, audit_seq) VALUES ('alpaca-live', true, 'owner', 42)`); err != nil {
		t.Fatal(err)
	}
	if ok, why := livegate.New(true, p).Allowed(ctx, "alpaca-live"); !ok {
		t.Fatalf("flag + audited enablement must open the gate: %s", why)
	}
	if ok, _ := livegate.New(false, p).Allowed(ctx, "alpaca-live"); ok {
		t.Fatal("enablement without the flag must stay closed")
	}
}
