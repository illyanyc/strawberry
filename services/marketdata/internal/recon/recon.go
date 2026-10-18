// Package recon reconciles Alpaca SIP daily bars against FMP end-of-day bars (rule MD-010,
// research 04 §6.1) and quarantines symbol-days that breach tolerance so training excludes them.
package recon

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"

	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/fmp"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

// Tolerances (MD-010): close within 0.05% or one tick; high/low within 0.1% or one tick; volume
// within 5% when both sides are consolidated (never compare IEX volume with SIP).
type Tolerances struct {
	CloseFrac, HighLowFrac, VolumeFrac, Tick float64
}

// Default tolerances.
var Default = Tolerances{CloseFrac: 0.0005, HighLowFrac: 0.001, VolumeFrac: 0.05, Tick: 0.01}

// Daily is one daily bar from a source.
type Daily struct {
	Open, High, Low, Close, Volume float64
}

// Result of one symbol-day comparison.
type Result struct {
	Date          string
	InstrumentID  string
	PrimaryFeed   string
	Secondary     string
	Status        string // ok | breach | missing_primary | missing_secondary
	CloseDiffBps  float64
	HighDiffBps   float64
	LowDiffBps    float64
	VolumeDiffPct float64
	Detail        string
}

func bps(a, b float64) float64 {
	if b == 0 {
		return math.Inf(1)
	}
	return (a - b) / b * 1e4
}

// Compare applies the tolerances to one symbol-day.
func Compare(t Tolerances, primary, secondary Daily, compareVolume bool) (status string, closeBps, highBps, lowBps, volPct float64, detail string) {
	closeBps, highBps, lowBps = bps(primary.Close, secondary.Close), bps(primary.High, secondary.High), bps(primary.Low, secondary.Low)
	within := func(a, b, frac float64) bool { return math.Abs(a-b) <= math.Max(frac*math.Abs(b), t.Tick)+1e-9 }
	var problems []string
	if !within(primary.Close, secondary.Close, t.CloseFrac) {
		problems = append(problems, fmt.Sprintf("close %g vs %g", primary.Close, secondary.Close))
	}
	if !within(primary.High, secondary.High, t.HighLowFrac) {
		problems = append(problems, fmt.Sprintf("high %g vs %g", primary.High, secondary.High))
	}
	if !within(primary.Low, secondary.Low, t.HighLowFrac) {
		problems = append(problems, fmt.Sprintf("low %g vs %g", primary.Low, secondary.Low))
	}
	if secondary.Volume > 0 {
		volPct = (primary.Volume - secondary.Volume) / secondary.Volume * 100
	}
	if compareVolume && secondary.Volume > 0 && math.Abs(primary.Volume-secondary.Volume) > t.VolumeFrac*secondary.Volume {
		problems = append(problems, fmt.Sprintf("volume %g vs %g", primary.Volume, secondary.Volume))
	}
	if len(problems) > 0 {
		return "breach", closeBps, highBps, lowBps, volPct, fmt.Sprint(problems)
	}
	return "ok", closeBps, highBps, lowBps, volPct, ""
}

// Store persists results and quarantine entries.
type Store interface {
	SaveResult(ctx context.Context, r Result) error
	Quarantine(ctx context.Context, instrumentID, date, reason, detail string) error
	Quarantined(ctx context.Context) ([]QuarantineRow, error)
}

// QuarantineRow is exported to ref/quarantine.parquet for Python readers.
type QuarantineRow struct {
	InstrumentID string `parquet:"instrument_id"`
	SessionDate  string `parquet:"session_date"`
	Reason       string `parquet:"reason"`
	Detail       string `parquet:"detail"`
}

// MemoryStore is an in-memory Store.
type MemoryStore struct {
	mu      sync.Mutex
	Results []Result
	Q       map[string]QuarantineRow
}

// NewMemoryStore returns an empty store.
func NewMemoryStore() *MemoryStore { return &MemoryStore{Q: map[string]QuarantineRow{}} }

// SaveResult implements Store.
func (m *MemoryStore) SaveResult(_ context.Context, r Result) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Results = append(m.Results, r)
	return nil
}

// Quarantine implements Store.
func (m *MemoryStore) Quarantine(_ context.Context, id, date, reason, detail string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.Q[id+"|"+date+"|"+reason] = QuarantineRow{InstrumentID: id, SessionDate: date, Reason: reason, Detail: detail}
	return nil
}

// Quarantined implements Store.
func (m *MemoryStore) Quarantined(context.Context) ([]QuarantineRow, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]QuarantineRow, 0, len(m.Q))
	for _, q := range m.Q {
		out = append(out, q)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].InstrumentID+out[i].SessionDate < out[j].InstrumentID+out[j].SessionDate
	})
	return out, nil
}

// PGStore persists in schema marketdata.
type PGStore struct {
	Pool *pgxpool.Pool
}

func nf(f float64) any {
	if math.IsInf(f, 0) || math.IsNaN(f) {
		return nil
	}
	return f
}

// SaveResult implements Store.
func (s PGStore) SaveResult(ctx context.Context, r Result) error {
	_, err := s.Pool.Exec(ctx, `INSERT INTO marketdata.recon_results (session_date, instrument_id, primary_feed, secondary, status,
		close_diff_bps, high_diff_bps, low_diff_bps, volume_diff_pct, detail) VALUES ($1::date,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (session_date, instrument_id, primary_feed, secondary) DO UPDATE SET status=EXCLUDED.status,
		close_diff_bps=EXCLUDED.close_diff_bps, high_diff_bps=EXCLUDED.high_diff_bps, low_diff_bps=EXCLUDED.low_diff_bps,
		volume_diff_pct=EXCLUDED.volume_diff_pct, detail=EXCLUDED.detail, checked_at=now()`,
		r.Date, r.InstrumentID, r.PrimaryFeed, r.Secondary, r.Status, nf(r.CloseDiffBps), nf(r.HighDiffBps), nf(r.LowDiffBps), nf(r.VolumeDiffPct), r.Detail)
	return err
}

// Quarantine implements Store.
func (s PGStore) Quarantine(ctx context.Context, id, date, reason, detail string) error {
	_, err := s.Pool.Exec(ctx, `INSERT INTO marketdata.quarantine (instrument_id, session_date, reason, detail) VALUES ($1,$2::date,$3,$4)
		ON CONFLICT (instrument_id, session_date, reason) DO UPDATE SET detail=EXCLUDED.detail`, id, date, reason, detail)
	return err
}

// Quarantined implements Store (unresolved entries).
func (s PGStore) Quarantined(ctx context.Context) ([]QuarantineRow, error) {
	rows, err := s.Pool.Query(ctx, `SELECT instrument_id, session_date::text, reason, detail FROM marketdata.quarantine WHERE resolved_at IS NULL ORDER BY 1, 2`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []QuarantineRow
	for rows.Next() {
		var q QuarantineRow
		if err := rows.Scan(&q.InstrumentID, &q.SessionDate, &q.Reason, &q.Detail); err != nil {
			return nil, err
		}
		out = append(out, q)
	}
	return out, rows.Err()
}

// EODSource returns raw daily bars for a symbol (FMP non-split-adjusted).
type EODSource interface {
	NonSplitAdjustedEOD(ctx context.Context, symbol string, from, to time.Time) ([]fmp.EOD, error)
}

// SymbolAt resolves an instrument's ticker on a date.
type SymbolAt func(ctx context.Context, instrumentID string, at int64) string

// Alerter publishes a data-quality alert.
type Alerter func(ctx context.Context, subject, message string)

// Reconciler runs the nightly comparison.
type Reconciler struct {
	Lake        *lake.Lake
	EOD         EODSource
	Cal         *calendar.Calendar
	Store       Store
	SymbolAt    SymbolAt
	Alert       Alerter
	Tol         Tolerances
	PrimaryFeed string // alpaca.sip
	Breaches    prometheus.Counter
}

// Summary of one run.
type Summary struct {
	Checked, OK, Breaches, Missing int
}

func f(s string) float64 { v, _ := strconv.ParseFloat(s, 64); return v }

// Run reconciles the given instruments for sessions in [from, to] and exports quarantine to the lake.
func (r *Reconciler) Run(ctx context.Context, instruments []string, from, to time.Time) (Summary, error) {
	var sum Summary
	sessions := r.Cal.Sessions(from, to.Add(24*time.Hour))
	if len(sessions) == 0 {
		return sum, nil
	}
	primary := map[string]map[string]lake.BarRow{} // inst -> date -> bar
	seen := map[string]bool{}
	for _, s := range sessions {
		p := lake.BarPartition("bars", r.PrimaryFeed, "1d", s.Open.UnixNano())
		if seen[p] {
			continue
		}
		seen[p] = true
		rows, err := r.Lake.ReadBars(p)
		if err != nil {
			return sum, err
		}
		for _, row := range rows {
			if primary[row.InstrumentID] == nil {
				primary[row.InstrumentID] = map[string]lake.BarRow{}
			}
			primary[row.InstrumentID][time.Unix(0, row.TsOpen).In(ny).Format("2006-01-02")] = row
		}
	}
	compareVolume := r.PrimaryFeed != "alpaca.iex"
	var errs []error
	for _, id := range instruments {
		sym := r.SymbolAt(ctx, id, sessions[len(sessions)-1].Open.UnixNano())
		if sym == "" {
			continue
		}
		first, _ := time.ParseInLocation("2006-01-02", sessions[0].Date, ny)
		last, _ := time.ParseInLocation("2006-01-02", sessions[len(sessions)-1].Date, ny)
		eods, err := r.EOD.NonSplitAdjustedEOD(ctx, sym, first, last)
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", sym, err))
			continue
		}
		byDate := map[string]fmp.EOD{}
		for _, e := range eods {
			byDate[e.Date] = e
		}
		for _, s := range sessions {
			res := Result{Date: s.Date, InstrumentID: id, PrimaryFeed: r.PrimaryFeed, Secondary: "fmp"}
			p, okP := primary[id][s.Date]
			e, okS := byDate[s.Date]
			switch {
			case !okP:
				res.Status = "missing_primary"
				sum.Missing++
			case !okS:
				res.Status = "missing_secondary"
				sum.Missing++
			default:
				res.Status, res.CloseDiffBps, res.HighDiffBps, res.LowDiffBps, res.VolumeDiffPct, res.Detail = Compare(r.Tol,
					Daily{Open: p.Open, High: p.High, Low: p.Low, Close: p.Close, Volume: p.Volume},
					Daily{Open: f(e.Open.String()), High: f(e.High.String()), Low: f(e.Low.String()), Close: f(e.Close.String()), Volume: f(e.Volume.String())},
					compareVolume)
			}
			sum.Checked++
			if res.Status == "ok" {
				sum.OK++
			}
			if res.Status == "breach" {
				sum.Breaches++
				if r.Breaches != nil {
					r.Breaches.Inc()
				}
				if err := r.Store.Quarantine(ctx, id, s.Date, "recon_breach", res.Detail); err != nil {
					errs = append(errs, err)
				}
				if r.Alert != nil {
					r.Alert(ctx, "md.reconciliation.breach", fmt.Sprintf("%s %s: %s; symbol-day quarantined from training", sym, s.Date, res.Detail))
				}
			}
			if err := r.Store.SaveResult(ctx, res); err != nil {
				errs = append(errs, err)
			}
		}
	}
	q, err := r.Store.Quarantined(ctx)
	if err == nil {
		err = lake.WriteRef(ctx, r.Lake, "quarantine", q)
	}
	return sum, errors.Join(append(errs, err)...)
}

var ny = func() *time.Location { l, _ := time.LoadLocation("America/New_York"); return l }()
