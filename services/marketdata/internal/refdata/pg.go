package refdata

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/calendar"
)

// PG persists the reference store in schema refdata. marketdata is the only writer (D4-style single
// writer for reference data), so the store loads everything at startup, mutates in memory, and
// Flush writes all rows back with idempotent upserts in one transaction. Tables are small (one row
// per instrument/span/action), so a full flush after each sync job is cheap and never loses a span.
type PG struct {
	*Memory
	Pool *pgxpool.Pool
}

// LoadPG reads every refdata table.
func LoadPG(ctx context.Context, pool *pgxpool.Pool) (*PG, error) {
	m := NewMemory()
	q := func(sql string, scan func(pgx.Rows) error) error {
		rows, err := pool.Query(ctx, sql)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			if err := scan(rows); err != nil {
				return err
			}
		}
		return rows.Err()
	}
	err := q(`SELECT instrument_id, asset_class, currency, name, primary_exchange, sector_etf, coalesce(listed_at,0), coalesce(delisted_at,0) FROM refdata.instruments`,
		func(r pgx.Rows) error {
			var i Instrument
			if err := r.Scan(&i.InstrumentID, &i.AssetClass, &i.Currency, &i.Name, &i.PrimaryExchange, &i.SectorETF, &i.ListedAt, &i.DelistedAt); err != nil {
				return err
			}
			m.Instruments[i.InstrumentID] = i
			return nil
		})
	if err == nil {
		err = q(`SELECT instrument_id, symbol, exchange, valid_from, coalesce(valid_to,0), source FROM refdata.symbols`, func(r pgx.Rows) error {
			var s SymbolSpan
			err := r.Scan(&s.InstrumentID, &s.Symbol, &s.Exchange, &s.ValidFrom, &s.ValidTo, &s.Source)
			m.Symbols = append(m.Symbols, s)
			return err
		})
	}
	if err == nil {
		err = q(`SELECT instrument_id, broker, broker_instrument_id, valid_from, coalesce(valid_to,0) FROM refdata.broker_ids`, func(r pgx.Rows) error {
			var b BrokerID
			err := r.Scan(&b.InstrumentID, &b.Broker, &b.BrokerInstrumentID, &b.ValidFrom, &b.ValidTo)
			m.BrokerIDs = append(m.BrokerIDs, b)
			return err
		})
	}
	if err == nil {
		err = q(`SELECT instrument_id, source, tradable, shortable, easy_to_borrow, fractionable, marginable, valid_from, coalesce(valid_to,0) FROM refdata.attributes`, func(r pgx.Rows) error {
			var a Attributes
			err := r.Scan(&a.InstrumentID, &a.Source, &a.Tradable, &a.Shortable, &a.EasyToBorrow, &a.Fractionable, &a.Marginable, &a.ValidFrom, &a.ValidTo)
			m.Attrs = append(m.Attrs, a)
			return err
		})
	}
	if err == nil {
		err = q(`SELECT source, source_id, instrument_id, symbol, type, ex_date, coalesce(ratio::text,''), coalesce(amount::text,''), new_symbol,
			successor_instrument_id, record_date, payable_date, declared_at, available_at, ingested_at, agreement FROM refdata.corporate_actions`, func(r pgx.Rows) error {
			var a CorporateAction
			err := r.Scan(&a.Source, &a.SourceID, &a.InstrumentID, &a.Symbol, &a.Type, &a.ExDate, &a.Ratio, &a.Amount, &a.NewSymbol,
				&a.SuccessorInstrumentID, &a.RecordDate, &a.PayableDate, &a.DeclaredAt, &a.AvailableAt, &a.IngestedAt, &a.Agreement)
			m.Actions[a.Source+"|"+a.SourceID] = a
			return err
		})
	}
	if err == nil {
		err = q(`SELECT universe, instrument_id, valid_from, coalesce(valid_to,0) FROM refdata.universe_members`, func(r pgx.Rows) error {
			var mb Member
			err := r.Scan(&mb.Universe, &mb.InstrumentID, &mb.ValidFrom, &mb.ValidTo)
			m.Members = append(m.Members, mb)
			return err
		})
	}
	if err != nil {
		return nil, fmt.Errorf("refdata: load: %w", err)
	}
	return &PG{Memory: m, Pool: pool}, nil
}

func nz(v int64) any {
	if v == 0 {
		return nil
	}
	return v
}

func ns(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// Flush upserts every row in one transaction.
func (p *PG) Flush(ctx context.Context) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	b := &pgx.Batch{}
	for _, i := range p.Instruments {
		b.Queue(`INSERT INTO refdata.instruments (instrument_id, asset_class, currency, name, primary_exchange, sector_etf, listed_at, delisted_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (instrument_id) DO UPDATE SET asset_class=EXCLUDED.asset_class, currency=EXCLUDED.currency,
			name=EXCLUDED.name, primary_exchange=EXCLUDED.primary_exchange, sector_etf=EXCLUDED.sector_etf, listed_at=EXCLUDED.listed_at,
			delisted_at=EXCLUDED.delisted_at, updated_at=now()`,
			i.InstrumentID, i.AssetClass, i.Currency, i.Name, i.PrimaryExchange, i.SectorETF, nz(i.ListedAt), nz(i.DelistedAt))
	}
	for _, s := range p.Symbols {
		b.Queue(`INSERT INTO refdata.symbols (instrument_id, symbol, exchange, valid_from, valid_to, source) VALUES ($1,$2,$3,$4,$5,$6)
			ON CONFLICT (instrument_id, valid_from) DO UPDATE SET symbol=EXCLUDED.symbol, exchange=EXCLUDED.exchange, valid_to=EXCLUDED.valid_to`,
			s.InstrumentID, s.Symbol, s.Exchange, s.ValidFrom, nz(s.ValidTo), s.Source)
	}
	for _, x := range p.BrokerIDs {
		b.Queue(`INSERT INTO refdata.broker_ids (instrument_id, broker, broker_instrument_id, valid_from, valid_to) VALUES ($1,$2,$3,$4,$5)
			ON CONFLICT (broker, broker_instrument_id, valid_from) DO UPDATE SET instrument_id=EXCLUDED.instrument_id, valid_to=EXCLUDED.valid_to`,
			x.InstrumentID, x.Broker, x.BrokerInstrumentID, x.ValidFrom, nz(x.ValidTo))
	}
	for _, a := range p.Attrs {
		b.Queue(`INSERT INTO refdata.attributes (instrument_id, source, tradable, shortable, easy_to_borrow, fractionable, marginable, valid_from, valid_to)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT (instrument_id, source, valid_from) DO UPDATE SET valid_to=EXCLUDED.valid_to`,
			a.InstrumentID, a.Source, a.Tradable, a.Shortable, a.EasyToBorrow, a.Fractionable, a.Marginable, a.ValidFrom, nz(a.ValidTo))
	}
	for _, a := range p.Actions {
		b.Queue(`INSERT INTO refdata.corporate_actions (source, source_id, instrument_id, symbol, type, ex_date, ratio, amount, new_symbol,
			successor_instrument_id, record_date, payable_date, declared_at, available_at, ingested_at, agreement)
			VALUES ($1,$2,$3,$4,$5,$6,$7::numeric,$8::numeric,$9,$10,$11,$12,$13,$14,$15,$16)
			ON CONFLICT (source, source_id) DO UPDATE SET instrument_id=EXCLUDED.instrument_id, ratio=EXCLUDED.ratio, amount=EXCLUDED.amount,
			agreement=EXCLUDED.agreement, available_at=LEAST(refdata.corporate_actions.available_at, EXCLUDED.available_at)`,
			a.Source, a.SourceID, a.InstrumentID, a.Symbol, a.Type, a.ExDate, ns(a.Ratio), ns(a.Amount), a.NewSymbol, a.SuccessorInstrumentID,
			a.RecordDate, a.PayableDate, a.DeclaredAt, a.AvailableAt, a.IngestedAt, a.Agreement)
	}
	for _, mb := range p.Members {
		b.Queue(`INSERT INTO refdata.universe_members (universe, instrument_id, valid_from, valid_to) VALUES ($1,$2,$3,$4)
			ON CONFLICT (universe, instrument_id, valid_from) DO UPDATE SET valid_to=EXCLUDED.valid_to`, mb.Universe, mb.InstrumentID, mb.ValidFrom, nz(mb.ValidTo))
	}
	tx, err := p.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if err := tx.SendBatch(ctx, b).Close(); err != nil {
		return fmt.Errorf("refdata: flush: %w", err)
	}
	return tx.Commit(ctx)
}

// MirrorCalendar writes the embedded calendar into refdata.sessions (MD-005).
func MirrorCalendar(ctx context.Context, pool *pgxpool.Pool, c *calendar.Calendar) (int, error) {
	b := &pgx.Batch{}
	n := 0
	for _, s := range c.Sessions(c.First().Open, c.Last().Close.Add(1)) {
		b.Queue(`INSERT INTO refdata.sessions (exchange, session_date, pre_open, open, close, post_close, early_close) VALUES ('XNYS',$1,$2,$3,$4,$5,$6)
			ON CONFLICT (exchange, session_date) DO UPDATE SET pre_open=EXCLUDED.pre_open, open=EXCLUDED.open, close=EXCLUDED.close,
			post_close=EXCLUDED.post_close, early_close=EXCLUDED.early_close`,
			s.Date, s.PreOpen.UnixNano(), s.Open.UnixNano(), s.Close.UnixNano(), s.PostClose.UnixNano(), s.EarlyClose)
		n++
	}
	if err := pool.SendBatch(ctx, b).Close(); err != nil {
		return 0, err
	}
	return n, nil
}
