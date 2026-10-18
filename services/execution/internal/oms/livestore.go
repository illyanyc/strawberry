package oms

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"
	"google.golang.org/protobuf/proto"

	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
)

// PostgresLive persists section 15 state (ADR-0054). Enablement writes go through audit.append so
// the row's audit_seq is a real chain entry (GL-011); the adapters only read the row (BR-013).
type PostgresLive struct{ Pool *pgxpool.Pool }

// Load implements LiveDB.
func (p *PostgresLive) Load(ctx context.Context) (map[string]*ordersv1.LiveReadiness, map[string][]string, map[string]Enablement, error) {
	ready := map[string]*ordersv1.LiveReadiness{}
	rows, err := p.Pool.Query(ctx, `SELECT broker_account_id, payload FROM execution.live_readiness`)
	if err != nil {
		return nil, nil, nil, err
	}
	for rows.Next() {
		var id string
		var raw []byte
		if err := rows.Scan(&id, &raw); err != nil {
			rows.Close()
			return nil, nil, nil, err
		}
		r := &ordersv1.LiveReadiness{}
		if err := proto.Unmarshal(raw, r); err != nil {
			rows.Close()
			return nil, nil, nil, err
		}
		ready[id] = r
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, nil, nil, err
	}
	sessions := map[string][]string{}
	rows, err = p.Pool.Query(ctx, `SELECT broker_account_id, session_date::text FROM execution.live_clean_sessions`)
	if err != nil {
		return nil, nil, nil, err
	}
	for rows.Next() {
		var acct, date string
		if err := rows.Scan(&acct, &date); err != nil {
			rows.Close()
			return nil, nil, nil, err
		}
		sessions[acct] = append(sessions[acct], date)
	}
	rows.Close()
	enable := map[string]Enablement{}
	rows, err = p.Pool.Query(ctx, `SELECT broker_account_id, enabled, orders_enabled, enabled_by, audit_seq, reason, (extract(epoch from updated_at)*1e9)::bigint FROM broker.live_enablement`)
	if err != nil {
		return nil, nil, nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var acct, by, reason string
		var en Enablement
		if err := rows.Scan(&acct, &en.Enabled, &en.Orders, &by, &en.Seq, &reason, &en.Updated); err != nil {
			return nil, nil, nil, err
		}
		en.By, en.Reason = by, reason
		enable[acct] = en
	}
	return ready, sessions, enable, rows.Err()
}

// SaveReadiness implements LiveDB.
func (p *PostgresLive) SaveReadiness(ctx context.Context, r *ordersv1.LiveReadiness) error {
	raw, _ := proto.Marshal(r)
	_, err := p.Pool.Exec(ctx, `INSERT INTO execution.live_readiness (broker_account_id, payload) VALUES ($1,$2)
		ON CONFLICT (broker_account_id) DO UPDATE SET payload=$2, updated_at=now()`, r.GetBrokerAccountId(), raw)
	return err
}

// AddSession implements LiveDB.
func (p *PostgresLive) AddSession(ctx context.Context, account, date string) error {
	_, err := p.Pool.Exec(ctx, `INSERT INTO execution.live_clean_sessions (broker_account_id, session_date) VALUES ($1,$2) ON CONFLICT DO NOTHING`, account, date)
	return err
}

// PutEnablement audits the change and upserts the row the adapters read (GL-011).
func (p *PostgresLive) PutEnablement(ctx context.Context, account string, en Enablement) (Enablement, error) {
	seq, _, err := audit.NewWriter(p.Pool).Append(ctx, audit.Event{Actor: en.By, Action: "execution.live.enablement", Target: account,
		Details: map[string]any{"enabled": en.Enabled, "orders": en.Orders, "reason": en.Reason}})
	if err != nil {
		return Enablement{}, fmt.Errorf("audit enablement: %w", err)
	}
	en.Seq = seq
	_, err = p.Pool.Exec(ctx, `INSERT INTO broker.live_enablement (broker_account_id, enabled, orders_enabled, enabled_by, audit_seq, reason)
		VALUES ($1,$2,$3,$4,$5,$6)
		ON CONFLICT (broker_account_id) DO UPDATE SET enabled=$2, orders_enabled=$3, enabled_by=$4, audit_seq=$5, reason=$6, updated_at=now()`,
		account, en.Enabled, en.Orders, en.By, en.Seq, en.Reason)
	return en, err
}
