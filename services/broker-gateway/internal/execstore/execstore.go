// Package execstore persists broker executions and order events and enqueues their bus events in
// the same transaction (BR-008, BR-011, ADR-0018). Memory is for tests and the store-less dev mode;
// Postgres is production (schema `broker`, outbox relayed by libs/go/outbox).
package execstore

import (
	"context"
	"errors"
	"fmt"
	"sync"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/outbox"
)

// Subjects (ADR-0018; master "Bus subjects").
func ExecSubject(acct string) string     { return "exec." + acct }
func OrderSubject(acct string) string    { return "ord.update." + acct }
func SnapshotSubject(acct string) string { return "acct.snapshot." + acct }
func ReconSubject(acct string) string    { return "recon.request." + acct }
func AlertSubject(sev alertsv1.Severity) string {
	switch sev {
	case alertsv1.Severity_SEVERITY_CRITICAL:
		return "alert.critical"
	case alertsv1.Severity_SEVERITY_WARNING:
		return "alert.warning"
	default:
		return "alert.info"
	}
}

// OrderDedupeKey identifies one distinct order event: order, raw status, cumulative fill, broker
// update time. The same event from the stream and from a resync has the same key.
func OrderDedupeKey(u *brokerv1.BrokerOrderUpdate) string {
	o := u.GetOrder()
	return fmt.Sprintf("%s|%s|%s|%d", o.GetBrokerOrderId(), o.GetBrokerStatus(), o.GetFilledQty().GetValue(), o.GetUpdatedAt())
}

// Message is a published (or enqueued) bus message, for tests.
type Message struct {
	Subject string
	MsgID   string
	Msg     proto.Message
}

// Memory is an in-memory Store. Published messages are recorded in order.
type Memory struct {
	mu        sync.Mutex
	execs     map[string]*ordersv1.ExecutionReport // key acct|exec_id
	orderKeys map[string]bool
	cursor    map[string]*ordersv1.ExecutionReport
	Published []Message
	// FailNext makes the next Record* call fail (fault injection).
	FailNext error
	// OnPublish (optional) receives every message in order, under the store lock (the paper
	// harness forwards them to JetStream with the same msg IDs as the Postgres outbox).
	OnPublish func(Message)
}

func (m *Memory) publish(x Message) {
	m.Published = append(m.Published, x)
	if m.OnPublish != nil {
		m.OnPublish(x)
	}
}

// NewMemory returns an empty store.
func NewMemory() *Memory {
	return &Memory{execs: map[string]*ordersv1.ExecutionReport{}, orderKeys: map[string]bool{}, cursor: map[string]*ordersv1.ExecutionReport{}}
}

func (m *Memory) fail() error {
	if m.FailNext != nil {
		err := m.FailNext
		m.FailNext = nil
		return err
	}
	return nil
}

// RecordExecution implements adapter.Store.
func (m *Memory) RecordExecution(_ context.Context, acct string, e *ordersv1.ExecutionReport, u *brokerv1.BrokerOrderUpdate) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.fail(); err != nil {
		return false, err
	}
	k := acct + "|" + e.GetBrokerExecId()
	if _, ok := m.execs[k]; ok {
		return false, nil
	}
	m.execs[k] = proto.Clone(e).(*ordersv1.ExecutionReport)
	if c := m.cursor[acct]; c == nil || e.GetEventTime() >= c.GetEventTime() {
		m.cursor[acct] = e
	}
	m.publish(Message{Subject: ExecSubject(acct), MsgID: "exec:" + k, Msg: e})
	return true, nil
}

// RecordOrderUpdate implements adapter.Store.
func (m *Memory) RecordOrderUpdate(_ context.Context, acct string, u *brokerv1.BrokerOrderUpdate) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.fail(); err != nil {
		return false, err
	}
	k := acct + "|" + OrderDedupeKey(u)
	if m.orderKeys[k] {
		return false, nil
	}
	m.orderKeys[k] = true
	m.publish(Message{Subject: OrderSubject(acct), MsgID: "ord:" + k, Msg: u})
	return true, nil
}

// RecordSnapshot implements adapter.Store.
func (m *Memory) RecordSnapshot(_ context.Context, acct string, s *ledgerv1.AccountSnapshot) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.publish(Message{Subject: SnapshotSubject(acct), MsgID: fmt.Sprintf("acct:%s:%d", acct, s.GetAsOf()), Msg: s})
	return nil
}

// RequestReconciliation implements adapter.Store.
func (m *Memory) RequestReconciliation(_ context.Context, r *ledgerv1.ReconciliationRequest) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.publish(Message{Subject: ReconSubject(r.GetBrokerAccountId()), MsgID: "recon:" + r.GetCorrelationId(), Msg: r})
	return nil
}

// Alert implements adapter.Store.
func (m *Memory) Alert(_ context.Context, a *alertsv1.Alert) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.publish(Message{Subject: AlertSubject(a.GetSeverity()), MsgID: "alert:" + a.GetAlertId(), Msg: a})
	return nil
}

// Cursor implements adapter.Store.
func (m *Memory) Cursor(_ context.Context, acct string) (string, int64, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	c := m.cursor[acct]
	return c.GetBrokerExecId(), c.GetEventTime(), nil
}

// Messages returns a copy of the published messages on subject (all when empty).
func (m *Memory) Messages(subject string) []Message {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []Message
	for _, x := range m.Published {
		if subject == "" || x.Subject == subject {
			out = append(out, x)
		}
	}
	return out
}

// Postgres is the production Store.
type Postgres struct {
	Pool  *pgxpool.Pool
	Relay interface{ Kick() }
}

const outboxTable = "broker.outbox"

func (p *Postgres) kick() {
	if p.Relay != nil {
		p.Relay.Kick()
	}
}

func marshal(m proto.Message) ([]byte, error) { return proto.Marshal(m) }

// RecordExecution inserts the execution (unique on account + canonical exec ID) and, if new,
// enqueues exec.<acct> in the same transaction (BR-008).
func (p *Postgres) RecordExecution(ctx context.Context, acct string, e *ordersv1.ExecutionReport, _ *brokerv1.BrokerOrderUpdate) (bool, error) {
	payload, err := marshal(e)
	if err != nil {
		return false, err
	}
	isNew := false
	err = outbox.TxFunc(ctx, p.Pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `INSERT INTO broker.executions (broker_account_id, broker_exec_id, broker_order_id, client_order_id, perm_id,
			symbol, instrument_id, side, qty, price, fees, event_time, sequence, payload)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::numeric,$10::numeric,$11::numeric,$12,$13,$14) ON CONFLICT DO NOTHING`,
			acct, e.GetBrokerExecId(), e.GetBrokerOrderId(), e.GetClientOrderId(), e.GetPermId(), e.GetSymbol(), e.GetInstrumentId(),
			e.GetSide().String(), decOr0(e.GetQty().GetValue()), decOr0(e.GetPrice().GetValue()), decOr0(e.GetFees().GetValue()),
			e.GetEventTime(), e.GetSequence(), payload)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return nil
		}
		isNew = true
		return outbox.Enqueue(ctx, tx, outboxTable, outbox.Message{Subject: ExecSubject(acct), MsgID: "exec:" + acct + ":" + e.GetBrokerExecId(),
			Payload: payload, CorrelationID: e.GetCorrelationId()})
	})
	if err != nil {
		return false, fmt.Errorf("execstore: record execution: %w", err)
	}
	if isNew {
		p.kick()
	}
	return isNew, nil
}

// RecordOrderUpdate inserts a distinct order event and enqueues ord.update.<acct>.
func (p *Postgres) RecordOrderUpdate(ctx context.Context, acct string, u *brokerv1.BrokerOrderUpdate) (bool, error) {
	payload, err := marshal(u)
	if err != nil {
		return false, err
	}
	key := OrderDedupeKey(u)
	o := u.GetOrder()
	isNew := false
	err = outbox.TxFunc(ctx, p.Pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `INSERT INTO broker.order_events (broker_account_id, dedupe_key, broker_order_id, client_order_id, broker_status,
			state, filled_qty, event_time, payload) VALUES ($1,$2,$3,$4,$5,$6,$7::numeric,$8,$9) ON CONFLICT DO NOTHING`,
			acct, key, o.GetBrokerOrderId(), o.GetClientOrderId(), o.GetBrokerStatus(), o.GetState().String(),
			decOr0(o.GetFilledQty().GetValue()), u.GetEventTime(), payload)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return nil
		}
		isNew = true
		return outbox.Enqueue(ctx, tx, outboxTable, outbox.Message{Subject: OrderSubject(acct), MsgID: "ord:" + acct + ":" + key, Payload: payload})
	})
	if err != nil {
		return false, fmt.Errorf("execstore: record order update: %w", err)
	}
	if isNew {
		p.kick()
	}
	return isNew, nil
}

func (p *Postgres) enqueue(ctx context.Context, m outbox.Message) error {
	if err := outbox.Enqueue(ctx, p.Pool, outboxTable, m); err != nil {
		return err
	}
	p.kick()
	return nil
}

// RecordSnapshot enqueues acct.snapshot.<acct>.
func (p *Postgres) RecordSnapshot(ctx context.Context, acct string, s *ledgerv1.AccountSnapshot) error {
	b, err := marshal(s)
	if err != nil {
		return err
	}
	return p.enqueue(ctx, outbox.Message{Subject: SnapshotSubject(acct), MsgID: fmt.Sprintf("acct:%s:%d", acct, s.GetAsOf()), Payload: b})
}

// RequestReconciliation enqueues recon.request.<acct> (BR-011).
func (p *Postgres) RequestReconciliation(ctx context.Context, r *ledgerv1.ReconciliationRequest) error {
	b, err := marshal(r)
	if err != nil {
		return err
	}
	return p.enqueue(ctx, outbox.Message{Subject: ReconSubject(r.GetBrokerAccountId()), MsgID: "recon:" + r.GetCorrelationId(), Payload: b, CorrelationID: r.GetCorrelationId()})
}

// Alert enqueues alert.<severity>.
func (p *Postgres) Alert(ctx context.Context, a *alertsv1.Alert) error {
	b, err := marshal(a)
	if err != nil {
		return err
	}
	return p.enqueue(ctx, outbox.Message{Subject: AlertSubject(a.GetSeverity()), MsgID: "alert:" + a.GetAlertId(), Payload: b})
}

// Cursor returns the latest stored execution for acct.
func (p *Postgres) Cursor(ctx context.Context, acct string) (string, int64, error) {
	var id string
	var t int64
	err := p.Pool.QueryRow(ctx, `SELECT broker_exec_id, event_time FROM broker.executions WHERE broker_account_id = $1
		ORDER BY event_time DESC, sequence DESC LIMIT 1`, acct).Scan(&id, &t)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", 0, nil
	}
	return id, t, err
}

func decOr0(s string) string {
	if s == "" {
		return "0"
	}
	return s
}
