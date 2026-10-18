package engine

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/ledger/book"
)

// Postgres is the production Store (schema `ledger`). Relay (optional) is kicked after commits.
type Postgres struct {
	Pool  *pgxpool.Pool
	Relay *outbox.Relay
}

const outboxTable = "ledger.outbox"

// Load implements Store.
func (p *Postgres) Load(ctx context.Context) (*State, error) {
	st := &State{EquityStart: map[string]DayStart{}, PendingActions: map[string]*mdv1.CorporateAction{}}
	rows, err := p.Pool.Query(ctx, `SELECT payload FROM ledger.events ORDER BY seq`)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var raw []byte
		var ev book.Event
		if err := rows.Scan(&raw); err != nil {
			rows.Close()
			return nil, err
		}
		if err := json.Unmarshal(raw, &ev); err != nil {
			rows.Close()
			return nil, fmt.Errorf("ledger: decode event: %w", err)
		}
		st.Events = append(st.Events, ev)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if err := scanJSON(ctx, p.Pool, `SELECT payload FROM ledger.breaks`, func(raw []byte) error {
		var b book.Break
		if err := json.Unmarshal(raw, &b); err != nil {
			return err
		}
		st.Breaks = append(st.Breaks, &b)
		return nil
	}); err != nil {
		return nil, err
	}
	if err := scanJSON(ctx, p.Pool, `SELECT payload FROM ledger.reservations`, func(raw []byte) error {
		var r book.Reservation
		if err := json.Unmarshal(raw, &r); err != nil {
			return err
		}
		st.Reservations = append(st.Reservations, &r)
		return nil
	}); err != nil {
		return nil, err
	}
	if err := scanJSON(ctx, p.Pool, `SELECT DISTINCT ON (account) payload FROM ledger.snapshots ORDER BY account, as_of DESC`, func(raw []byte) error {
		s := &ledgerv1.AccountSnapshot{}
		if err := proto.Unmarshal(raw, s); err != nil {
			return err
		}
		st.Snapshots = append(st.Snapshots, s)
		return nil
	}); err != nil {
		return nil, err
	}
	if err := scanJSON(ctx, p.Pool, `SELECT DISTINCT ON (account, owner) payload FROM ledger.equity_points ORDER BY account, owner, ts DESC`, func(raw []byte) error {
		var e EquityRow
		if err := json.Unmarshal(raw, &e); err != nil {
			return err
		}
		st.EquityStart[e.Account+"|"+e.Owner] = DayStart{Date: e.Date, DayStart: e.DayStart, HWM: e.HWM}
		return nil
	}); err != nil {
		return nil, err
	}
	rows, err = p.Pool.Query(ctx, `SELECT action_key, payload FROM ledger.pending_actions WHERE applied_at IS NULL`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var k string
		var raw []byte
		if err := rows.Scan(&k, &raw); err != nil {
			return nil, err
		}
		ca := &mdv1.CorporateAction{}
		if err := proto.Unmarshal(raw, ca); err != nil {
			return nil, err
		}
		st.PendingActions[k] = ca
	}
	return st, rows.Err()
}

func scanJSON(ctx context.Context, pool *pgxpool.Pool, q string, fn func([]byte) error) error {
	rows, err := pool.Query(ctx, q)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return err
		}
		if err := fn(raw); err != nil {
			return err
		}
	}
	return rows.Err()
}

// Commit implements Store: one transaction for the whole batch.
func (p *Postgres) Commit(ctx context.Context, b *Batch) error {
	err := outbox.TxFunc(ctx, p.Pool, func(tx pgx.Tx) error {
		for _, e := range b.Events {
			raw, err := json.Marshal(e)
			if err != nil {
				return err
			}
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.events (account, kind, source, event_time, actor, payload) VALUES ($1,$2,$3,$4,$5,$6)
				ON CONFLICT (account, kind, source) DO NOTHING`, e.Account, e.Kind, e.Source, e.Time, e.Actor, raw); err != nil {
				return fmt.Errorf("insert event: %w", err)
			}
		}
		for _, r := range b.Positions {
			lots, _ := json.Marshal(r.Lots)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.positions (account, owner, instrument_id, qty, cost, realized, lots) VALUES ($1,$2,$3,$4,$5,$6,$7)
				ON CONFLICT (account, owner, instrument_id) DO UPDATE SET qty=$4, cost=$5, realized=$6, lots=$7, updated_at=now()`,
				r.Account, r.Owner, r.InstrumentID, r.Qty.String(), r.Cost.String(), r.Realized.String(), lots); err != nil {
				return fmt.Errorf("upsert position: %w", err)
			}
		}
		for _, r := range b.Reservations {
			raw, _ := json.Marshal(r)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.reservations (reservation_id, intent_id, payload) VALUES ($1,$2,$3)
				ON CONFLICT (reservation_id) DO UPDATE SET payload=$3`, r.ID, r.IntentID, raw); err != nil {
				return fmt.Errorf("upsert reservation: %w", err)
			}
		}
		for _, id := range b.DeletedReservations {
			if _, err := tx.Exec(ctx, `DELETE FROM ledger.reservations WHERE reservation_id=$1`, id); err != nil {
				return err
			}
		}
		for _, x := range b.Breaks {
			raw, _ := json.Marshal(x)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.breaks (break_id, account, type, status, instrument_id, detected_at, resolved_at, payload)
				VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (break_id) DO UPDATE SET type=$3, status=$4, resolved_at=$7, payload=$8, updated_at=now()`,
				x.ID, x.Account, x.Type, x.Status, x.InstrumentID, x.Detected, x.Resolved, raw); err != nil {
				return fmt.Errorf("upsert break: %w", err)
			}
		}
		for _, r := range b.Reports {
			raw, _ := proto.Marshal(r)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.recon_reports (account, as_of, status, trigger, payload) VALUES ($1,$2,$3,$4,$5)`,
				r.GetBrokerAccountId(), r.GetAsOf(), r.GetStatus().String(), r.GetTrigger(), raw); err != nil {
				return err
			}
		}
		for _, e := range b.Equity {
			raw, _ := json.Marshal(e)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.equity_points (account, owner, ts, payload) VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING`,
				e.Account, e.Owner, e.TS, raw); err != nil {
				return err
			}
		}
		for _, w := range b.Washes {
			raw, _ := json.Marshal(w)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.wash_sales (flag_id, sell_time, payload) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`, w.ID, w.SellTime, raw); err != nil {
				return err
			}
		}
		if s := b.Snapshot; s != nil {
			raw, _ := proto.Marshal(s)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.snapshots (account, as_of, payload) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`, s.GetBrokerAccountId(), s.GetAsOf(), raw); err != nil {
				return err
			}
		}
		for k, ca := range b.PendingActions {
			raw, _ := proto.Marshal(ca)
			if _, err := tx.Exec(ctx, `INSERT INTO ledger.pending_actions (action_key, payload) VALUES ($1,$2) ON CONFLICT (action_key) DO UPDATE SET payload=$2`, k, raw); err != nil {
				return err
			}
		}
		for _, k := range b.AppliedActions {
			if _, err := tx.Exec(ctx, `UPDATE ledger.pending_actions SET applied_at=now() WHERE action_key=$1`, k); err != nil {
				return err
			}
		}
		for _, a := range b.Audit {
			if _, _, err := audit.AppendWith(ctx, tx, a); err != nil {
				return fmt.Errorf("audit: %w", err)
			}
		}
		for _, m := range b.Outbox {
			if err := outbox.Enqueue(ctx, tx, outboxTable, m); err != nil {
				return err
			}
		}
		return nil
	})
	if err == nil && p.Relay != nil && len(b.Outbox) > 0 {
		p.Relay.Kick()
	}
	return err
}

// Positions implements Store.
func (p *Postgres) Positions(ctx context.Context) ([]PositionRow, error) {
	rows, err := p.Pool.Query(ctx, `SELECT account, owner, instrument_id, qty::text, cost::text, realized::text, lots FROM ledger.positions`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []PositionRow
	for rows.Next() {
		var r PositionRow
		var q, c, re string
		var lots []byte
		if err := rows.Scan(&r.Account, &r.Owner, &r.InstrumentID, &q, &c, &re, &lots); err != nil {
			return nil, err
		}
		var e1, e2, e3 error
		r.Qty, e1 = parse(q)
		r.Cost, e2 = parse(c)
		r.Realized, e3 = parse(re)
		if err := errors.Join(e1, e2, e3, json.Unmarshal(lots, &r.Lots)); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// Equity implements Store.
func (p *Postgres) Equity(ctx context.Context, acct, owner string, start, end int64) ([]EquityRow, error) {
	if end == 0 {
		end = 1 << 62
	}
	var out []EquityRow
	err := scanJSONArgs(ctx, p.Pool, `SELECT payload FROM ledger.equity_points WHERE account=$1 AND owner=$2 AND ts BETWEEN $3 AND $4 ORDER BY ts`,
		[]any{acct, owner, start, end}, func(raw []byte) error {
			var e EquityRow
			if err := json.Unmarshal(raw, &e); err != nil {
				return err
			}
			out = append(out, e)
			return nil
		})
	return out, err
}

// Reports implements Store.
func (p *Postgres) Reports(ctx context.Context, acct string, since int64, limit int) ([]*ledgerv1.ReconciliationResult, error) {
	if limit <= 0 || limit > 1000 {
		limit = 1000
	}
	var out []*ledgerv1.ReconciliationResult
	err := scanJSONArgs(ctx, p.Pool, `SELECT payload FROM ledger.recon_reports WHERE ($1 = '' OR account=$1) AND as_of >= $2 ORDER BY id DESC LIMIT $3`,
		[]any{acct, since, limit}, func(raw []byte) error {
			r := &ledgerv1.ReconciliationResult{}
			if err := proto.Unmarshal(raw, r); err != nil {
				return err
			}
			out = append(out, r)
			return nil
		})
	return out, err
}

// Washes implements Store.
func (p *Postgres) Washes(ctx context.Context, since int64) ([]book.WashSale, error) {
	var out []book.WashSale
	err := scanJSONArgs(ctx, p.Pool, `SELECT payload FROM ledger.wash_sales WHERE sell_time >= $1 ORDER BY sell_time`, []any{since}, func(raw []byte) error {
		var w book.WashSale
		if err := json.Unmarshal(raw, &w); err != nil {
			return err
		}
		out = append(out, w)
		return nil
	})
	return out, err
}

func scanJSONArgs(ctx context.Context, pool *pgxpool.Pool, q string, args []any, fn func([]byte) error) error {
	rows, err := pool.Query(ctx, q, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return err
		}
		if err := fn(raw); err != nil {
			return err
		}
	}
	return rows.Err()
}

func parse(s string) (decimal.Decimal, error) { return decimal.NewFromString(s) }
