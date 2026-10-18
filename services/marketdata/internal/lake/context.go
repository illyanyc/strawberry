package lake

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	contextv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/context/v1"
)

// ErrInvalidContext marks a context event that can never be stored (CX-001); bus consumers
// terminate such messages instead of retrying them.
var ErrInvalidContext = errors.New("lake: invalid context event")

// ContextKindName is the partition name of a context kind ("news", "short_interest", ...).
func ContextKindName(k contextv1.ContextKind) string {
	return strings.ToLower(strings.TrimPrefix(k.String(), "CONTEXT_KIND_"))
}

// ContextRowsFromEvent converts a bus event into lake rows: one per instrument, or one market-wide
// row with an empty instrument_id. The payload is canonical JSON (sorted keys), so the content hash
// is stable across redeliveries and compaction can drop unchanged snapshots (CX-003).
func ContextRowsFromEvent(ev *contextv1.ContextEvent, ingestedAt int64) ([]ContextRow, error) {
	p := ev.GetProvenance()
	switch {
	case ev.GetKind() == contextv1.ContextKind_CONTEXT_KIND_UNSPECIFIED:
		return nil, fmt.Errorf("%w: kind unspecified", ErrInvalidContext)
	case p.GetAvailableAt() <= 0 || p.GetEventTime() == 0:
		// CX-001 / DF-001: every context record carries event_time and available_at.
		return nil, fmt.Errorf("%w: event_time and available_at are required", ErrInvalidContext)
	case ev.GetRecordKey() == "" || ev.GetSource() == "":
		return nil, fmt.Errorf("%w: record_key and source are required", ErrInvalidContext)
	}
	payload := map[string]any{}
	if ev.GetPayload() != nil {
		payload = ev.GetPayload().AsMap()
	}
	raw, err := json.Marshal(payload) // encoding/json sorts map keys: canonical form
	if err != nil {
		return nil, fmt.Errorf("%w: payload: %w", ErrInvalidContext, err)
	}
	sum := sha256.Sum256(raw)
	if ing := p.GetIngestedAt(); ing > 0 {
		ingestedAt = ing
	}
	base := ContextRow{
		Kind: ContextKindName(ev.GetKind()), EventTime: p.GetEventTime(), AvailableAt: p.GetAvailableAt(), IngestedAt: ingestedAt,
		Source: ev.GetSource(), Vintage: ev.GetVintage(), PayloadJSON: string(raw), PayloadSHA256: hex.EncodeToString(sum[:]),
		RecordKey: ev.GetRecordKey(), EventID: ev.GetEventId(),
	}
	ids, syms := ev.GetInstrumentIds(), ev.GetSymbols()
	if len(ids) == 0 {
		base.Symbol = strings.Join(syms, ",")
		return []ContextRow{base}, nil
	}
	out := make([]ContextRow, 0, len(ids))
	for i, id := range ids {
		r := base
		r.InstrumentID = id
		if len(syms) == len(ids) {
			r.Symbol = syms[i]
		}
		out = append(out, r)
	}
	return out, nil
}

func (r ContextRow) versionKey() string {
	return r.Kind + "|" + r.Source + "|" + r.RecordKey + "|" + r.InstrumentID
}

// ContextCompactResult summarizes one context partition compaction.
type ContextCompactResult struct {
	Partition string
	Rows      int // rows in the new part file
	Dropped   int // redeliveries and unchanged snapshots removed
}

// CompactContext merges a context partition into one part file (ST-004 for context, CX-003):
// rows are ordered per (kind, source, record_key, instrument) by available_at, then ingested_at,
// and a row whose payload equals the previous kept version of the same record is dropped. Content
// changes are all kept with their own available_at, so a sequence A, B, A stays three versions and
// point-in-time reads return what was known then.
func (l *Lake) CompactContext(ctx context.Context, partition string) (ContextCompactResult, error) {
	lk := l.partLock(partition)
	lk.Lock()
	defer lk.Unlock()
	res := ContextCompactResult{Partition: partition}
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
	var all []ContextRow
	for _, name := range append(append([]string{}, parts...), stages...) {
		rows, err := readRows[ContextRow](l.Root, partition, name)
		if err != nil {
			return res, fmt.Errorf("lake: read %s/%s: %w", partition, name, err)
		}
		all = append(all, rows...)
	}
	for i := range all {
		if all[i].RecordKey == "" { // rows written before record_key existed
			all[i].RecordKey = fmt.Sprintf("t%d", all[i].EventTime)
		}
	}
	sort.SliceStable(all, func(i, j int) bool {
		a, b := all[i], all[j]
		if ka, kb := a.versionKey(), b.versionKey(); ka != kb {
			return ka < kb
		}
		if a.AvailableAt != b.AvailableAt {
			return a.AvailableAt < b.AvailableAt
		}
		return a.IngestedAt < b.IngestedAt
	})
	out := make([]ContextRow, 0, len(all))
	last := map[string]string{}
	for _, r := range all {
		k := r.versionKey()
		if sha, ok := last[k]; ok && sha == r.PayloadSHA256 {
			res.Dropped++
			continue
		}
		last[k] = r.PayloadSHA256
		out = append(out, r)
	}
	sort.SliceStable(out, func(i, j int) bool {
		if out[i].InstrumentID != out[j].InstrumentID {
			return out[i].InstrumentID < out[j].InstrumentID
		}
		if out[i].EventTime != out[j].EventTime {
			return out[i].EventTime < out[j].EventTime
		}
		return out[i].AvailableAt < out[j].AvailableAt
	})
	res.Rows = len(out)
	if len(out) > 0 {
		fi, err := writeAtomic(l.Root, partition, "part-"+newULID()+".parquet", out,
			map[string]string{"tb.compacted_at": time.Now().UTC().Format(time.RFC3339)})
		if err != nil {
			return res, err
		}
		fi.MinTs, fi.MaxTs = out[0].EventTime, out[0].EventTime
		for _, r := range out {
			fi.MinTs, fi.MaxTs = min(fi.MinTs, r.EventTime), max(fi.MaxTs, r.EventTime)
		}
		if err := l.record(ctx, fi, partition, "part"); err != nil {
			return res, err
		}
	}
	for _, name := range append(parts, stages...) {
		if err := l.remove(ctx, partition, name); err != nil {
			return res, err
		}
	}
	return res, nil
}

// CompactAllContext compacts every context partition with staged files.
func (l *Lake) CompactAllContext(ctx context.Context) ([]ContextCompactResult, error) {
	parts, err := l.Partitions("context")
	if err != nil {
		return nil, err
	}
	var out []ContextCompactResult
	for _, p := range parts {
		r, err := l.CompactContext(ctx, p)
		if err != nil {
			return out, err
		}
		if r.Rows > 0 || r.Dropped > 0 {
			out = append(out, r)
		}
	}
	return out, nil
}

// ReadContext returns every row of a context partition (all versions), for tests and tools.
func (l *Lake) ReadContext(partition string) ([]ContextRow, error) {
	lk := l.partLock(partition)
	lk.Lock()
	defer lk.Unlock()
	var out []ContextRow
	for _, prefix := range []string{"part-", "staging-"} {
		names, err := l.listFiles(partition, prefix)
		if err != nil {
			return nil, err
		}
		for _, n := range names {
			rows, err := readRows[ContextRow](l.Root, partition, n)
			if err != nil {
				return nil, err
			}
			out = append(out, rows...)
		}
	}
	return out, nil
}
