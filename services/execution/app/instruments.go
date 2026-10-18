package app

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
)

// RefdataInstruments resolves instrument_id → current symbol and broker identifiers from the
// instrument master (`refdata`, read-only for svc_execution), cached for 10 minutes.
type RefdataInstruments struct {
	Pool *pgxpool.Pool
	mu   sync.Mutex
	m    map[string]cached
}

type cached struct {
	inst *commonv1.Instrument
	at   time.Time
}

// ErrUnknownInstrument is returned for IDs not in the instrument master (fail closed).
var ErrUnknownInstrument = errors.New("execution: instrument not in the instrument master")

// Lookup implements oms.Instruments.
func (r *RefdataInstruments) Lookup(ctx context.Context, id string) (*commonv1.Instrument, error) {
	r.mu.Lock()
	if c, ok := r.m[id]; ok && time.Since(c.at) < 10*time.Minute {
		r.mu.Unlock()
		return c.inst, nil
	}
	r.mu.Unlock()
	now := time.Now().UnixNano()
	inst := &commonv1.Instrument{InstrumentId: id, AssetClass: commonv1.AssetClass_ASSET_CLASS_US_EQUITY, Currency: "USD"}
	err := r.Pool.QueryRow(ctx, `SELECT s.symbol, s.exchange, i.currency FROM refdata.symbols s JOIN refdata.instruments i USING (instrument_id)
		WHERE s.instrument_id = $1 AND s.valid_from <= $2 AND (s.valid_to IS NULL OR s.valid_to > $2) ORDER BY s.valid_from DESC LIMIT 1`, id, now).
		Scan(&inst.Symbol, &inst.Exchange, &inst.Currency)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrUnknownInstrument
	}
	if err != nil {
		return nil, err
	}
	rows, err := r.Pool.Query(ctx, `SELECT broker, broker_instrument_id FROM refdata.broker_ids WHERE instrument_id = $1 AND valid_from <= $2 AND (valid_to IS NULL OR valid_to > $2)`, id, now)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var b, bid string
		if err := rows.Scan(&b, &bid); err != nil {
			return nil, err
		}
		switch b {
		case "alpaca":
			inst.AlpacaAssetId = bid
		case "ibkr":
			inst.IbkrConId = bid
		}
	}
	r.mu.Lock()
	if r.m == nil {
		r.m = map[string]cached{}
	}
	r.m[id] = cached{inst: inst, at: time.Now()}
	r.mu.Unlock()
	return inst, rows.Err()
}
