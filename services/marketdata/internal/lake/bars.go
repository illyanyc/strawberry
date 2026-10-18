package lake

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"time"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
)

// Timeframe names used in paths and rows.
var TimeframeName = map[mdv1.Timeframe]string{
	mdv1.Timeframe_TIMEFRAME_1M: "1m", mdv1.Timeframe_TIMEFRAME_5M: "5m", mdv1.Timeframe_TIMEFRAME_15M: "15m",
	mdv1.Timeframe_TIMEFRAME_30M: "30m", mdv1.Timeframe_TIMEFRAME_1H: "1h", mdv1.Timeframe_TIMEFRAME_1D: "1d",
}

// SessionName maps the contract session enum.
var SessionName = map[mdv1.Session]string{
	mdv1.Session_SESSION_PRE: "pre", mdv1.Session_SESSION_REGULAR: "regular", mdv1.Session_SESSION_POST: "post",
}

// ErrPartialBar rejects non-final bars (DF-007).
var ErrPartialBar = errors.New("lake: partial (non-final) bars are never written")

func f64(d interface{ GetValue() string }) (float64, error) {
	if d == nil || d.GetValue() == "" {
		return 0, nil
	}
	return strconv.ParseFloat(d.GetValue(), 64)
}

// RowFromBar converts a contract bar. It enforces DF-001 (provenance timestamps present), DF-005
// (raw prices only), and DF-007 (final bars only).
func RowFromBar(b *mdv1.Bar) (BarRow, error) {
	if !b.GetIsFinal() {
		return BarRow{}, ErrPartialBar
	}
	if b.GetInstrumentId() == "" || b.GetFeed() == "" || b.GetTsOpen() == 0 || b.GetTsClose() <= b.GetTsOpen() {
		return BarRow{}, fmt.Errorf("lake: bar missing instrument_id/feed/ts (%q %q %d %d)", b.GetInstrumentId(), b.GetFeed(), b.GetTsOpen(), b.GetTsClose())
	}
	if b.GetAvailableAt() < b.GetTsClose() || b.GetIngestedAt() == 0 {
		// DF-001/DF-002: a bar cannot be knowable before it closes.
		return BarRow{}, fmt.Errorf("lake: bar %s %d has available_at %d before ts_close %d or no ingested_at", b.GetInstrumentId(), b.GetTsOpen(), b.GetAvailableAt(), b.GetTsClose())
	}
	if ab := b.GetAdjustmentBasis(); ab != mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW && ab != mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_UNSPECIFIED {
		return BarRow{}, fmt.Errorf("lake: only raw bars are stored (DF-005), got %s", ab)
	}
	tf, ok := TimeframeName[b.GetTimeframe()]
	if !ok {
		return BarRow{}, fmt.Errorf("lake: unknown timeframe %s", b.GetTimeframe())
	}
	r := BarRow{InstrumentID: b.GetInstrumentId(), Symbol: b.GetSymbol(), Timeframe: tf, TsOpen: b.GetTsOpen(), TsClose: b.GetTsClose(),
		TradeCount: b.GetTradeCount(), Feed: b.GetFeed(), Source: b.GetSource(), Revision: b.GetRevision(), IsFinal: true,
		Session: SessionName[b.GetSession()], Repaired: b.GetRepaired(), AdjustmentBasis: "raw", AvailableAt: b.GetAvailableAt(), IngestedAt: b.GetIngestedAt()}
	var err error
	var errs []error
	for _, x := range []struct {
		dst *float64
		src interface{ GetValue() string }
	}{{&r.Open, b.GetOpen()}, {&r.High, b.GetHigh()}, {&r.Low, b.GetLow()}, {&r.Close, b.GetClose()}, {&r.Volume, b.GetVolume()}, {&r.VWAP, b.GetVwap()}} {
		if *x.dst, err = f64(x.src); err != nil {
			errs = append(errs, err)
		}
	}
	if err := errors.Join(errs...); err != nil {
		return BarRow{}, fmt.Errorf("lake: bad decimal: %w", err)
	}
	return r, nil
}

// BarFromRow converts back to the contract shape (prices as shortest decimal strings).
func BarFromRow(r BarRow) *mdv1.Bar {
	d := func(f float64) *commonDecimal { return &commonDecimal{Value: strconv.FormatFloat(f, 'f', -1, 64)} }
	var tf mdv1.Timeframe
	for k, v := range TimeframeName {
		if v == r.Timeframe {
			tf = k
		}
	}
	var sess mdv1.Session
	for k, v := range SessionName {
		if v == r.Session {
			sess = k
		}
	}
	return &mdv1.Bar{InstrumentId: r.InstrumentID, Symbol: r.Symbol, Timeframe: tf, TsOpen: r.TsOpen, TsClose: r.TsClose,
		Open: d(r.Open), High: d(r.High), Low: d(r.Low), Close: d(r.Close), Volume: d(r.Volume), Vwap: d(r.VWAP), TradeCount: r.TradeCount,
		Feed: r.Feed, Source: r.Source, Revision: r.Revision, IsFinal: r.IsFinal, Session: sess, Repaired: r.Repaired,
		AdjustmentBasis: mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW, AvailableAt: r.AvailableAt, IngestedAt: r.IngestedAt}
}

// StageBars writes bars to staging files, one per partition, and returns the partitions touched
// with their max ts_open watermark. Staged rows are visible to readers immediately; Compact merges
// them (ST-004).
func (l *Lake) StageBars(ctx context.Context, bars []*mdv1.Bar) (map[string]int64, error) {
	byPart := map[string][]BarRow{}
	for _, b := range bars {
		r, err := RowFromBar(b)
		if err != nil {
			return nil, err
		}
		p := BarPartition("bars", r.Feed, r.Timeframe, r.TsOpen)
		byPart[p] = append(byPart[p], r)
	}
	wm := map[string]int64{}
	for p, rows := range byPart {
		lk := l.partLock(p)
		lk.Lock()
		sortRows(rows)
		fi, err := writeAtomic(l.Root, p, "staging-"+newULID()+".parquet", rows, nil)
		if err == nil {
			fi.MinTs, fi.MaxTs = rows[0].TsOpen, rows[len(rows)-1].TsOpen
			err = l.record(ctx, fi, p, "staging")
		}
		lk.Unlock()
		if err != nil {
			return nil, err
		}
		for _, r := range rows {
			if r.TsOpen > wm[p] {
				wm[p] = r.TsOpen
			}
		}
	}
	return wm, nil
}

func sortRows(rows []BarRow) {
	sort.SliceStable(rows, func(i, j int) bool {
		if rows[i].InstrumentID != rows[j].InstrumentID {
			return rows[i].InstrumentID < rows[j].InstrumentID
		}
		return rows[i].TsOpen < rows[j].TsOpen
	})
}

// CompactResult summarizes one partition compaction.
type CompactResult struct {
	Partition string
	Rows      int // rows in the new part file
	Added     int // new natural keys
	Revised   int // keys whose content changed (old version moved to bar_revisions)
	Unchanged int // staged rows identical to the stored version (dropped)
}

// Compact merges a bar partition's part and staging files into one part file (ST-004..ST-006):
// one row per natural key at its latest revision; identical re-deliveries are dropped (a rerun adds
// zero rows); a content change increments the revision and appends the previous version to
// bar_revisions (both retained, DF-007). Ordering of candidates is by available_at, then ingested_at.
func (l *Lake) Compact(ctx context.Context, partition string) (CompactResult, error) {
	lk := l.partLock(partition)
	lk.Lock()
	defer lk.Unlock()
	res := CompactResult{Partition: partition}
	parts, err := l.listFiles(partition, "part-")
	if err != nil {
		return res, err
	}
	stages, err := l.listFiles(partition, "staging-")
	if err != nil {
		return res, err
	}
	if len(stages) == 0 && len(parts) <= 1 {
		return res, nil
	}
	current := map[string]BarRow{}
	var revisions []BarRow
	for _, name := range parts {
		rows, err := readRows[BarRow](l.Root, partition, name)
		if err != nil {
			return res, fmt.Errorf("lake: read %s/%s: %w", partition, name, err)
		}
		for _, r := range rows {
			k := r.Key()
			if c, ok := current[k]; !ok || r.Revision > c.Revision {
				current[k] = r
			}
		}
	}
	var staged []BarRow
	for _, name := range stages {
		rows, err := readRows[BarRow](l.Root, partition, name)
		if err != nil {
			return res, fmt.Errorf("lake: read %s/%s: %w", partition, name, err)
		}
		staged = append(staged, rows...)
	}
	sort.SliceStable(staged, func(i, j int) bool {
		if staged[i].AvailableAt != staged[j].AvailableAt {
			return staged[i].AvailableAt < staged[j].AvailableAt
		}
		return staged[i].IngestedAt < staged[j].IngestedAt
	})
	for _, r := range staged {
		k := r.Key()
		c, ok := current[k]
		switch {
		case !ok:
			r.Revision = 0
			current[k] = r
			res.Added++
		case c.sameContent(r):
			res.Unchanged++
		default:
			if r.AvailableAt < c.AvailableAt {
				// A correction cannot be known before the version it corrects (DF-002).
				r.AvailableAt = c.AvailableAt
			}
			revisions = append(revisions, c)
			r.Revision = c.Revision + 1
			current[k] = r
			res.Revised++
		}
	}
	out := make([]BarRow, 0, len(current))
	for _, r := range current {
		out = append(out, r)
	}
	sortRows(out)
	res.Rows = len(out)
	if len(revisions) > 0 {
		rp := "bar_revisions" + partition[len("bars"):]
		sortRows(revisions)
		fi, err := writeAtomic(l.Root, rp, "part-"+newULID()+".parquet", revisions, nil)
		if err != nil {
			return res, err
		}
		fi.MinTs, fi.MaxTs = revisions[0].TsOpen, revisions[len(revisions)-1].TsOpen
		if err := l.record(ctx, fi, rp, "revisions"); err != nil {
			return res, err
		}
	}
	if len(out) > 0 {
		fi, err := writeAtomic(l.Root, partition, "part-"+newULID()+".parquet", out, map[string]string{"tb.compacted_at": time.Now().UTC().Format(time.RFC3339)})
		if err != nil {
			return res, err
		}
		fi.MinTs, fi.MaxTs = minMaxTs(out)
		if err := l.record(ctx, fi, partition, "part"); err != nil {
			return res, err
		}
	}
	// Old files are removed only after the new file is durable; a crash in between leaves duplicates
	// that readers and the next compaction deduplicate by key and revision.
	for _, name := range append(parts, stages...) {
		if err := l.remove(ctx, partition, name); err != nil {
			return res, err
		}
	}
	return res, nil
}

func minMaxTs(rows []BarRow) (int64, int64) {
	mn, mx := rows[0].TsOpen, rows[0].TsOpen
	for _, r := range rows {
		mn, mx = min(mn, r.TsOpen), max(mx, r.TsOpen)
	}
	return mn, mx
}

// Partitions lists every bar partition that has files (relative paths).
func (l *Lake) Partitions(dataset string) ([]string, error) {
	var out []string
	err := walkPartitions(l.Root, dataset, func(p string) { out = append(out, p) })
	sort.Strings(out)
	return out, err
}

// CompactAll compacts every partition with staged files.
func (l *Lake) CompactAll(ctx context.Context) ([]CompactResult, error) {
	parts, err := l.Partitions("bars")
	if err != nil {
		return nil, err
	}
	var out []CompactResult
	for _, p := range parts {
		r, err := l.Compact(ctx, p)
		if err != nil {
			return out, err
		}
		if r.Rows > 0 || r.Unchanged > 0 {
			out = append(out, r)
		}
	}
	return out, nil
}

// ReadBars returns the latest version of every bar in a partition (dedup by key, highest revision,
// earliest available_at among equal revisions), sorted by instrument and time.
func (l *Lake) ReadBars(partition string) ([]BarRow, error) {
	lk := l.partLock(partition)
	lk.Lock()
	defer lk.Unlock()
	cur := map[string]BarRow{}
	for _, prefix := range []string{"part-", "staging-"} {
		names, err := l.listFiles(partition, prefix)
		if err != nil {
			return nil, err
		}
		for _, n := range names {
			rows, err := readRows[BarRow](l.Root, partition, n)
			if err != nil {
				return nil, err
			}
			for _, r := range rows {
				k := r.Key()
				c, ok := cur[k]
				if !ok || r.Revision > c.Revision || (r.Revision == c.Revision && r.AvailableAt < c.AvailableAt) {
					cur[k] = r
				}
			}
		}
	}
	out := make([]BarRow, 0, len(cur))
	for _, r := range cur {
		out = append(out, r)
	}
	sortRows(out)
	return out, nil
}
