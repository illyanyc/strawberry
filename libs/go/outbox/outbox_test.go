package outbox

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type fakePub struct {
	mu     sync.Mutex
	got    []Message
	failAt int // fail the Nth publish (1-based) once
	n      int
}

func (f *fakePub) Publish(_ context.Context, m Message) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.n++
	if f.n == f.failAt {
		return errors.New("nats down")
	}
	f.got = append(f.got, m)
	return nil
}

func testPool(t *testing.T) *pgxpool.Pool {
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
	_, _ = admin.Exec(ctx, "DROP DATABASE IF EXISTS outbox_test WITH (FORCE)")
	if _, err := admin.Exec(ctx, "CREATE DATABASE outbox_test"); err != nil {
		t.Fatal(err)
	}
	admin.Close(ctx)
	cfg, _ := pgxpool.ParseConfig(super)
	cfg.ConnConfig.Database = "outbox_test"
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	if _, err := pool.Exec(ctx, `CREATE SCHEMA t; CREATE TABLE t.outbox (
	    id bigserial PRIMARY KEY, subject text NOT NULL, msg_id text NOT NULL, payload bytea NOT NULL,
	    correlation_id text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
	    published_at timestamptz, attempts integer NOT NULL DEFAULT 0, last_error text NOT NULL DEFAULT '')`); err != nil {
		t.Fatal(err)
	}
	return pool
}

func TestOutboxRollbackPublishesNothingAndOrderIsKept(t *testing.T) {
	pool := testPool(t)
	ctx := context.Background()
	// A rolled-back transaction leaves no event.
	tx, _ := pool.Begin(ctx)
	if err := Enqueue(ctx, tx, "t.outbox", Message{Subject: "exec.a", MsgID: "rolled-back", Payload: []byte("x")}); err != nil {
		t.Fatal(err)
	}
	_ = tx.Rollback(ctx)
	for i := range 5 {
		if err := TxFunc(ctx, pool, func(tx pgx.Tx) error {
			return Enqueue(ctx, tx, "t.outbox", Message{Subject: "exec.a", MsgID: fmt.Sprintf("m%d", i), Payload: []byte{byte(i)}})
		}); err != nil {
			t.Fatal(err)
		}
	}
	pub := &fakePub{failAt: 3}
	r := NewRelay(pool, "t.outbox", pub, nil, NewMetrics(nil, "t.outbox"))
	n, err := r.Drain(ctx)
	if err == nil || n != 2 {
		t.Fatalf("first pass: n=%d err=%v (publication must stop at the first failure)", n, err)
	}
	n, err = r.Drain(ctx)
	if err != nil || n != 3 {
		t.Fatalf("second pass: n=%d err=%v", n, err)
	}
	if n, _ := r.Drain(ctx); n != 0 {
		t.Fatal("published rows must not be republished")
	}
	if len(pub.got) != 5 {
		t.Fatalf("got %d messages", len(pub.got))
	}
	for i, m := range pub.got {
		if m.MsgID != fmt.Sprintf("m%d", i) {
			t.Fatalf("order broken at %d: %s", i, m.MsgID)
		}
	}
	if err := Enqueue(ctx, pool, "t.outbox; DROP TABLE t.outbox", Message{Subject: "s", MsgID: "x"}); err == nil {
		t.Fatal("invalid table name accepted")
	}
	if n, err := Purge(ctx, pool, "t.outbox", 0); err != nil || n != 5 {
		t.Fatalf("purge: %d %v", n, err)
	}
}
