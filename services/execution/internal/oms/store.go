package oms

import (
	"context"
	"fmt"
	"sync"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/protobuf/proto"

	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/outbox"
)

// Tx is one atomic OMS change: state rows, audit records, and outbox messages (OE-001, RM-002).
type Tx struct {
	Intents    []*ordersv1.IntentRecord
	Orders     []*ordersv1.Order
	Fills      []*ordersv1.Fill
	Strategies []*ordersv1.Strategy
	Limits     map[string]*ordersv1.RiskLimits
	Kills      []*ordersv1.KillSwitch
	Halts      []*ordersv1.Halt
	Audit      []audit.Event
	Outbox     []outbox.Message
}

// State is the persisted OMS state.
type State struct {
	Intents    []*ordersv1.IntentRecord
	Orders     []*ordersv1.Order
	Fills      []*ordersv1.Fill
	Strategies []*ordersv1.Strategy
	Limits     map[string]*ordersv1.RiskLimits
	Kills      []*ordersv1.KillSwitch
	Halts      []*ordersv1.Halt
}

// Store persists the OMS.
type Store interface {
	Load(ctx context.Context) (*State, error)
	Commit(ctx context.Context, tx *Tx) error
}

// Memory is an in-memory Store; Publish (optional) receives committed outbox messages.
type Memory struct {
	mu      sync.Mutex
	st      State
	idx     map[string]int
	Audits  []audit.Event
	Outbox  []outbox.Message
	Publish func(outbox.Message)
	// FailNext makes the next Commit fail (fault injection).
	FailNext error
}

// NewMemory returns an empty store.
func NewMemory() *Memory {
	return &Memory{st: State{Limits: map[string]*ordersv1.RiskLimits{}}, idx: map[string]int{}}
}

func upsert[T proto.Message](m *Memory, kind, id string, list *[]T, v T) {
	key := kind + "|" + id
	c := proto.Clone(v).(T)
	if i, ok := m.idx[key]; ok {
		(*list)[i] = c
		return
	}
	m.idx[key] = len(*list)
	*list = append(*list, c)
}

// Load implements Store.
func (m *Memory) Load(context.Context) (*State, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := State{Limits: map[string]*ordersv1.RiskLimits{}}
	for _, x := range m.st.Intents {
		out.Intents = append(out.Intents, proto.Clone(x).(*ordersv1.IntentRecord))
	}
	for _, x := range m.st.Orders {
		out.Orders = append(out.Orders, proto.Clone(x).(*ordersv1.Order))
	}
	for _, x := range m.st.Fills {
		out.Fills = append(out.Fills, proto.Clone(x).(*ordersv1.Fill))
	}
	for _, x := range m.st.Strategies {
		out.Strategies = append(out.Strategies, proto.Clone(x).(*ordersv1.Strategy))
	}
	for k, v := range m.st.Limits {
		out.Limits[k] = proto.Clone(v).(*ordersv1.RiskLimits)
	}
	for _, x := range m.st.Kills {
		out.Kills = append(out.Kills, proto.Clone(x).(*ordersv1.KillSwitch))
	}
	for _, x := range m.st.Halts {
		out.Halts = append(out.Halts, proto.Clone(x).(*ordersv1.Halt))
	}
	return &out, nil
}

// Commit implements Store.
func (m *Memory) Commit(_ context.Context, tx *Tx) error {
	m.mu.Lock()
	if m.FailNext != nil {
		err := m.FailNext
		m.FailNext = nil
		m.mu.Unlock()
		return err
	}
	for _, x := range tx.Intents {
		upsert(m, "i", x.GetIntent().GetIntentId(), &m.st.Intents, x)
	}
	for _, x := range tx.Orders {
		upsert(m, "o", x.GetOrderId(), &m.st.Orders, x)
	}
	for _, x := range tx.Fills {
		upsert(m, "f", x.GetBrokerAccountId()+"|"+x.GetBrokerExecId(), &m.st.Fills, x)
	}
	for _, x := range tx.Strategies {
		upsert(m, "s", x.GetStrategyId(), &m.st.Strategies, x)
	}
	for k, v := range tx.Limits {
		m.st.Limits[k] = proto.Clone(v).(*ordersv1.RiskLimits)
	}
	for _, x := range tx.Kills {
		upsert(m, "k", x.GetEventId(), &m.st.Kills, x)
	}
	for _, x := range tx.Halts {
		upsert(m, "h", x.GetHaltId(), &m.st.Halts, x)
	}
	m.Audits = append(m.Audits, tx.Audit...)
	m.Outbox = append(m.Outbox, tx.Outbox...)
	pub := m.Publish
	m.mu.Unlock()
	if pub != nil {
		for _, o := range tx.Outbox {
			pub(o)
		}
	}
	return nil
}

// AuditActions returns recorded audit actions (tests).
func (m *Memory) AuditActions() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]string, 0, len(m.Audits))
	for _, a := range m.Audits {
		out = append(out, a.Action)
	}
	return out
}

// Messages returns outbox messages on subject (tests).
func (m *Memory) Messages(subject string) []outbox.Message {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []outbox.Message
	for _, o := range m.Outbox {
		if subject == "" || o.Subject == subject {
			out = append(out, o)
		}
	}
	return out
}

// Postgres is the production Store (schema `execution`).
type Postgres struct {
	Pool  *pgxpool.Pool
	Relay *outbox.Relay
}

// Load implements Store.
func (p *Postgres) Load(ctx context.Context) (*State, error) {
	st := &State{Limits: map[string]*ordersv1.RiskLimits{}}
	load := func(q string, fn func(id string, raw []byte) error) error {
		rows, err := p.Pool.Query(ctx, q)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var id string
			var raw []byte
			if err := rows.Scan(&id, &raw); err != nil {
				return err
			}
			if err := fn(id, raw); err != nil {
				return err
			}
		}
		return rows.Err()
	}
	err := load(`SELECT intent_id, payload FROM execution.intents WHERE received_at > (extract(epoch from now()) - 7*86400)::bigint * 1000000000 OR state NOT IN ('rejected','done','failed','shadow') ORDER BY received_at`, func(_ string, raw []byte) error {
		x := &ordersv1.IntentRecord{}
		st.Intents = append(st.Intents, x)
		return proto.Unmarshal(raw, x)
	})
	if err == nil {
		err = load(`SELECT order_id, payload FROM execution.orders WHERE updated_at > now() - interval '7 days' OR state NOT IN ('ORDER_STATE_FILLED','ORDER_STATE_CANCELED','ORDER_STATE_REJECTED','ORDER_STATE_EXPIRED','ORDER_STATE_REPLACED') ORDER BY created_at`, func(_ string, raw []byte) error {
			x := &ordersv1.Order{}
			st.Orders = append(st.Orders, x)
			return proto.Unmarshal(raw, x)
		})
	}
	if err == nil {
		err = load(`SELECT broker_exec_id, payload FROM execution.fills WHERE event_time > (extract(epoch from now()) - 10*86400)::bigint * 1000000000 ORDER BY event_time`, func(_ string, raw []byte) error {
			x := &ordersv1.Fill{}
			st.Fills = append(st.Fills, x)
			return proto.Unmarshal(raw, x)
		})
	}
	if err == nil {
		err = load(`SELECT strategy_id, payload FROM execution.strategies`, func(_ string, raw []byte) error {
			x := &ordersv1.Strategy{}
			st.Strategies = append(st.Strategies, x)
			return proto.Unmarshal(raw, x)
		})
	}
	if err == nil {
		err = load(`SELECT scope, payload FROM execution.risk_limits`, func(id string, raw []byte) error {
			x := &ordersv1.RiskLimits{}
			st.Limits[id] = x
			return proto.Unmarshal(raw, x)
		})
	}
	if err == nil {
		err = load(`SELECT event_id, payload FROM execution.kill_switches WHERE latched OR updated_at > now() - interval '30 days'`, func(_ string, raw []byte) error {
			x := &ordersv1.KillSwitch{}
			st.Kills = append(st.Kills, x)
			return proto.Unmarshal(raw, x)
		})
	}
	if err == nil {
		err = load(`SELECT halt_id, payload FROM execution.halts WHERE NOT cleared`, func(_ string, raw []byte) error {
			x := &ordersv1.Halt{}
			st.Halts = append(st.Halts, x)
			return proto.Unmarshal(raw, x)
		})
	}
	return st, err
}

// Commit implements Store in one transaction.
func (p *Postgres) Commit(ctx context.Context, tx *Tx) error {
	err := outbox.TxFunc(ctx, p.Pool, func(t pgx.Tx) error {
		exec := func(q string, args ...any) error {
			_, err := t.Exec(ctx, q, args...)
			return err
		}
		for _, x := range tx.Intents {
			raw, _ := proto.Marshal(x)
			it := x.GetIntent()
			if err := exec(`INSERT INTO execution.intents (intent_id, strategy_id, broker_account_id, state, correlation_id, received_at, payload)
				VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (intent_id) DO UPDATE SET state=$4, payload=$7, updated_at=now()`,
				it.GetIntentId(), it.GetStrategyId(), it.GetBrokerAccountId(), x.GetState(), it.GetCorrelationId(), x.GetReceivedAt(), raw); err != nil {
				return fmt.Errorf("intent: %w", err)
			}
		}
		for _, x := range tx.Orders {
			raw, _ := proto.Marshal(x)
			if err := exec(`INSERT INTO execution.orders (order_id, intent_id, client_order_id, broker_account_id, broker_order_id, strategy_id, state, created_at, payload)
				VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (order_id) DO UPDATE SET broker_order_id=$5, state=$7, payload=$9, updated_at=now()`,
				x.GetOrderId(), x.GetIntentId(), x.GetClientOrderId(), x.GetBrokerAccountId(), x.GetBrokerOrderId(), x.GetStrategyId(), x.GetState().String(), x.GetCreatedAt(), raw); err != nil {
				return fmt.Errorf("order: %w", err)
			}
		}
		for _, x := range tx.Fills {
			raw, _ := proto.Marshal(x)
			if err := exec(`INSERT INTO execution.fills (broker_account_id, broker_exec_id, order_id, strategy_id, event_time, payload) VALUES ($1,$2,$3,$4,$5,$6)
				ON CONFLICT (broker_account_id, broker_exec_id) DO NOTHING`, x.GetBrokerAccountId(), x.GetBrokerExecId(), x.GetOrderId(), x.GetStrategyId(), x.GetEventTime(), raw); err != nil {
				return fmt.Errorf("fill: %w", err)
			}
		}
		for _, x := range tx.Strategies {
			raw, _ := proto.Marshal(x)
			if err := exec(`INSERT INTO execution.strategies (strategy_id, short_id, payload) VALUES ($1,$2,$3) ON CONFLICT (strategy_id) DO UPDATE SET payload=$3, updated_at=now()`,
				x.GetStrategyId(), x.GetShortId(), raw); err != nil {
				return fmt.Errorf("strategy: %w", err)
			}
		}
		for k, v := range tx.Limits {
			raw, _ := proto.Marshal(v)
			if err := exec(`INSERT INTO execution.risk_limits (scope, payload) VALUES ($1,$2) ON CONFLICT (scope) DO UPDATE SET payload=$2, updated_at=now()`, k, raw); err != nil {
				return err
			}
		}
		for _, x := range tx.Kills {
			raw, _ := proto.Marshal(x)
			if err := exec(`INSERT INTO execution.kill_switches (event_id, latched, payload) VALUES ($1,$2,$3) ON CONFLICT (event_id) DO UPDATE SET latched=$2, payload=$3, updated_at=now()`,
				x.GetEventId(), x.GetLatched(), raw); err != nil {
				return err
			}
		}
		for _, x := range tx.Halts {
			raw, _ := proto.Marshal(x)
			if err := exec(`INSERT INTO execution.halts (halt_id, cleared, payload) VALUES ($1,$2,$3) ON CONFLICT (halt_id) DO UPDATE SET cleared=$2, payload=$3, updated_at=now()`,
				x.GetHaltId(), x.GetClearedAt() != 0, raw); err != nil {
				return err
			}
		}
		for _, a := range tx.Audit {
			if _, _, err := audit.AppendWith(ctx, t, a); err != nil {
				return fmt.Errorf("audit: %w", err)
			}
		}
		for _, m := range tx.Outbox {
			if err := outbox.Enqueue(ctx, t, "execution.outbox", m); err != nil {
				return err
			}
		}
		return nil
	})
	if err == nil && p.Relay != nil && len(tx.Outbox) > 0 {
		p.Relay.Kick()
	}
	return err
}
