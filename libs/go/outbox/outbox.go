// Package outbox implements the transactional outbox (D2, ADR-0018): a service writes an event row
// in the same Postgres transaction as the state change it announces, and a relay publishes pending
// rows to NATS JetStream with Nats-Msg-Id = the row's msg_id. A crash before commit publishes
// nothing; a crash after publish but before the row is marked re-publishes with the same msg_id,
// which JetStream's dedupe window drops. Events are therefore neither lost nor duplicated.
//
// Each owning schema has its own table (created by its migrations) with this shape:
//
//	CREATE TABLE <schema>.outbox (
//	    id             bigserial PRIMARY KEY,
//	    subject        text NOT NULL,
//	    msg_id         text NOT NULL,
//	    payload        bytea NOT NULL,
//	    correlation_id text NOT NULL DEFAULT '',
//	    created_at     timestamptz NOT NULL DEFAULT now(),
//	    published_at   timestamptz,
//	    attempts       integer NOT NULL DEFAULT 0,
//	    last_error     text NOT NULL DEFAULT ''
//	);
package outbox

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
)

// Message is one event to publish.
type Message struct {
	Subject       string
	MsgID         string
	Payload       []byte
	CorrelationID string
}

// Execer is satisfied by pgx.Tx, *pgxpool.Pool, and *pgx.Conn.
type Execer interface {
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
}

// Publisher sends one message; implementations must set the dedupe ID (Nats-Msg-Id).
type Publisher interface {
	Publish(ctx context.Context, m Message) error
}

var tableRE = regexp.MustCompile(`^[a-z_]+\.outbox$`)

// Enqueue inserts m into table (e.g. "broker.outbox") using q, normally the caller's transaction.
func Enqueue(ctx context.Context, q Execer, table string, m Message) error {
	if !tableRE.MatchString(table) {
		return fmt.Errorf("outbox: invalid table %q", table)
	}
	if m.Subject == "" || m.MsgID == "" {
		return fmt.Errorf("outbox: subject and msg_id are required")
	}
	_, err := q.Exec(ctx, `INSERT INTO `+table+` (subject, msg_id, payload, correlation_id) VALUES ($1, $2, $3, $4)`,
		m.Subject, m.MsgID, m.Payload, m.CorrelationID)
	if err != nil {
		return fmt.Errorf("outbox: enqueue %s: %w", m.Subject, err)
	}
	return nil
}

// Metrics for a relay.
type Metrics struct {
	Published prometheus.Counter
	Failures  prometheus.Counter
	Pending   prometheus.Gauge
}

// NewMetrics registers relay metrics with reg (label table).
func NewMetrics(reg prometheus.Registerer, table string) *Metrics {
	m := &Metrics{
		Published: prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_outbox_published_total", Help: "Outbox rows published to the bus.", ConstLabels: prometheus.Labels{"table": table}}),
		Failures:  prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_outbox_publish_failures_total", Help: "Outbox publish failures.", ConstLabels: prometheus.Labels{"table": table}}),
		Pending:   prometheus.NewGauge(prometheus.GaugeOpts{Name: "tb_outbox_pending", Help: "Unpublished outbox rows seen in the last pass.", ConstLabels: prometheus.Labels{"table": table}}),
	}
	if reg != nil {
		reg.MustRegister(m.Published, m.Failures, m.Pending)
	}
	return m
}

// Relay publishes pending rows in id order.
type Relay struct {
	Pool     *pgxpool.Pool
	Table    string
	Pub      Publisher
	Interval time.Duration
	Batch    int
	Log      *slog.Logger
	Metrics  *Metrics
	kick     chan struct{}
}

// NewRelay returns a relay polling every interval (and immediately on Kick).
func NewRelay(pool *pgxpool.Pool, table string, pub Publisher, log *slog.Logger, m *Metrics) *Relay {
	return &Relay{Pool: pool, Table: table, Pub: pub, Interval: 250 * time.Millisecond, Batch: 200, Log: log, Metrics: m, kick: make(chan struct{}, 1)}
}

// Kick wakes the relay after a commit so publication latency is not bounded by the poll interval.
func (r *Relay) Kick() {
	select {
	case r.kick <- struct{}{}:
	default:
	}
}

// Run publishes until ctx ends.
func (r *Relay) Run(ctx context.Context) {
	t := time.NewTicker(r.Interval)
	defer t.Stop()
	for {
		for {
			n, err := r.Drain(ctx)
			if err != nil {
				if ctx.Err() == nil && r.Log != nil {
					r.Log.Warn("outbox relay pass failed", "table", r.Table, "err", err)
				}
				break
			}
			if n < r.Batch {
				break
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		case <-r.kick:
		}
	}
}

// Drain runs one pass and returns the number of rows published. Rows are locked with SKIP LOCKED
// so two relays never publish the same row concurrently; publication stops at the first failure to
// preserve per-table order.
func (r *Relay) Drain(ctx context.Context) (int, error) {
	if !tableRE.MatchString(r.Table) {
		return 0, fmt.Errorf("outbox: invalid table %q", r.Table)
	}
	tx, err := r.Pool.Begin(ctx)
	if err != nil {
		return 0, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	rows, err := tx.Query(ctx, `SELECT id, subject, msg_id, payload, correlation_id FROM `+r.Table+
		` WHERE published_at IS NULL ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED`, r.Batch)
	if err != nil {
		return 0, err
	}
	type row struct {
		id int64
		m  Message
	}
	var batch []row
	for rows.Next() {
		var x row
		if err := rows.Scan(&x.id, &x.m.Subject, &x.m.MsgID, &x.m.Payload, &x.m.CorrelationID); err != nil {
			rows.Close()
			return 0, err
		}
		batch = append(batch, x)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, err
	}
	if r.Metrics != nil {
		r.Metrics.Pending.Set(float64(len(batch)))
	}
	published := 0
	var pubErr error
	for _, x := range batch {
		if pubErr = r.Pub.Publish(ctx, x.m); pubErr != nil {
			if r.Metrics != nil {
				r.Metrics.Failures.Inc()
			}
			if _, err := tx.Exec(ctx, `UPDATE `+r.Table+` SET attempts = attempts + 1, last_error = $2 WHERE id = $1`, x.id, truncate(pubErr.Error(), 500)); err != nil {
				return published, err
			}
			break
		}
		if _, err := tx.Exec(ctx, `UPDATE `+r.Table+` SET published_at = now(), attempts = attempts + 1 WHERE id = $1`, x.id); err != nil {
			return published, err
		}
		published++
	}
	if err := tx.Commit(ctx); err != nil {
		return 0, err
	}
	if r.Metrics != nil {
		r.Metrics.Published.Add(float64(published))
	}
	if pubErr != nil {
		return published, pubErr
	}
	return published, nil
}

// Purge deletes published rows older than age (retention).
func Purge(ctx context.Context, q Execer, table string, age time.Duration) (int64, error) {
	if !tableRE.MatchString(table) {
		return 0, fmt.Errorf("outbox: invalid table %q", table)
	}
	tag, err := q.Exec(ctx, `DELETE FROM `+table+` WHERE published_at IS NOT NULL AND published_at < now() - $1::interval`, fmt.Sprintf("%d seconds", int(age.Seconds())))
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// TxFunc runs fn in a transaction on pool and commits it.
func TxFunc(ctx context.Context, pool *pgxpool.Pool, fn func(pgx.Tx) error) error {
	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := fn(tx); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// JetStreamPublisher publishes outbox rows through JetStream with the row's msg_id as the dedupe ID
// and the correlation ID as the Tb-Correlation-Id header (same header as bus.Publish).
type JetStreamPublisher struct {
	JS jetstream.JetStream
}

// Publish implements Publisher.
func (p JetStreamPublisher) Publish(ctx context.Context, m Message) error {
	msg := nats.NewMsg(m.Subject)
	msg.Data = m.Payload
	if m.CorrelationID != "" {
		msg.Header.Set("Tb-Correlation-Id", m.CorrelationID)
	}
	if _, err := p.JS.PublishMsg(ctx, msg, jetstream.WithMsgID(m.MsgID)); err != nil {
		return fmt.Errorf("outbox: publish %s: %w", m.Subject, err)
	}
	return nil
}
