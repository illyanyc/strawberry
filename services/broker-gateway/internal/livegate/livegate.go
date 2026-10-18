// Package livegate decides whether a live-environment broker account may run (rules BR-013,
// OE-010): the global config flag TB_TRADING_LIVE_ENABLED must be true AND broker.live_enablement
// must hold an enabled row for the account, written by an audited owner action (section 15).
package livegate

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Querier reads the enablement row (pgxpool.Pool in production).
type Querier interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

// Gate implements adapter.LiveGate.
type Gate struct {
	FlagEnabled bool
	DB          Querier
}

// New returns a gate over pool.
func New(flag bool, pool *pgxpool.Pool) *Gate {
	g := &Gate{FlagEnabled: flag}
	if pool != nil {
		g.DB = pool
	}
	return g
}

// Allowed reports whether account may run, with the reason when it may not.
func (g *Gate) Allowed(ctx context.Context, account string) (bool, string) {
	if !g.FlagEnabled {
		return false, "live trading disabled: TB_TRADING_LIVE_ENABLED is not true"
	}
	if g.DB == nil {
		return false, "live trading disabled: no enablement store"
	}
	var enabled, orders bool
	var seq int64
	err := g.DB.QueryRow(ctx, `SELECT enabled, orders_enabled, audit_seq FROM broker.live_enablement WHERE broker_account_id = $1`, account).Scan(&enabled, &orders, &seq)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		return false, "live trading disabled: no audited live enablement for " + account
	case err != nil:
		return false, "live trading disabled: enablement lookup failed"
	case !enabled || seq <= 0:
		return false, "live trading disabled: account not live-enabled"
	}
	_ = orders // read-only stage still lets the adapter run (GL-012); orders are a separate check
	return true, ""
}

// OrdersAllowed reports whether the account may accept orders (GL-012). Enabled alone is the
// read-only stage: the adapter streams and reconciles, and every order is refused.
func (g *Gate) OrdersAllowed(ctx context.Context, account string) (bool, string) {
	if !g.FlagEnabled {
		return false, "live orders disabled: TB_TRADING_LIVE_ENABLED is not true"
	}
	if g.DB == nil {
		return false, "live orders disabled: no enablement store"
	}
	var enabled, orders bool
	var seq int64
	err := g.DB.QueryRow(ctx, `SELECT enabled, orders_enabled, audit_seq FROM broker.live_enablement WHERE broker_account_id = $1`, account).Scan(&enabled, &orders, &seq)
	switch {
	case errors.Is(err, pgx.ErrNoRows), err != nil:
		return false, "live orders disabled: enablement lookup failed"
	case !enabled || !orders || seq <= 0:
		return false, "live orders disabled: account is read-only"
	}
	return true, ""
}
