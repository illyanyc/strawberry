package engine

import (
	"context"
	"errors"
	"strings"
	"sync"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// RefdataResolver resolves broker symbols and IDs through the instrument master (`refdata`,
// read-only for svc_ledger), with a small cache. Unknown symbols resolve to "" and are tracked
// under an unmapped key for `manual` only (VP-010).
type RefdataResolver struct {
	Pool *pgxpool.Pool
	mu   sync.Mutex
	m    map[string]string
}

// Resolve implements Resolver.
func (r *RefdataResolver) Resolve(ctx context.Context, symbol, brokerID string, at int64) (string, error) {
	key := strings.ToUpper(symbol) + "|" + brokerID
	r.mu.Lock()
	if id, ok := r.m[key]; ok {
		r.mu.Unlock()
		return id, nil
	}
	r.mu.Unlock()
	var id string
	err := pgx.ErrNoRows
	if brokerID != "" {
		err = r.Pool.QueryRow(ctx, `SELECT instrument_id FROM refdata.broker_ids WHERE broker_instrument_id = $1
			AND valid_from <= $2 AND (valid_to IS NULL OR valid_to > $2) LIMIT 1`, brokerID, at).Scan(&id)
	}
	if errors.Is(err, pgx.ErrNoRows) && symbol != "" {
		err = r.Pool.QueryRow(ctx, `SELECT instrument_id FROM refdata.symbols WHERE symbol = $1
			AND valid_from <= $2 AND (valid_to IS NULL OR valid_to > $2) ORDER BY valid_from DESC LIMIT 1`, strings.ToUpper(symbol), at).Scan(&id)
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	r.mu.Lock()
	if r.m == nil {
		r.m = map[string]string{}
	}
	r.m[key] = id
	r.mu.Unlock()
	return id, nil
}
