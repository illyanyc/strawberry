// Package lake is the single writer of the Parquet lake (D4, ADR-0020, rules ST-001..ST-008,
// DF-001, DF-007). Layout (Hive-style paths, ZSTD Parquet, schema version in file metadata):
//
//	bars/feed=<feed>/timeframe=<tf>/year=<YYYY>/month=<MM>/{part,staging}-<ULID>.parquet
//	bar_revisions/feed=<feed>/timeframe=<tf>/year=<YYYY>/month=<MM>/part-<ULID>.parquet
//	context/kind=<kind>/year=<YYYY>/month=<MM>/{part,staging}-<ULID>.parquet
//	ref/<table>.parquet
//
// Every file is written to a dot-prefixed temporary name, fsynced, then atomically renamed (ST-002),
// so readers globbing *.parquet never see a partial file. Readers deduplicate on the natural key
// (instrument_id, timeframe, ts_open, feed) by highest revision, so a reader that races a
// compaction (old and new file both visible for a moment) still sees each bar once.
package lake

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/parquet-go/parquet-go"
	"github.com/parquet-go/parquet-go/compress/zstd"
)

// SchemaVersion is stored in every file's key-value metadata (ST-001).
const SchemaVersion = "1"

// BarRow is the Parquet schema of the bars and bar_revisions datasets. Prices are float64 in the
// research store (ADR-0020); the order path and ledger never read the lake.
type BarRow struct {
	InstrumentID    string  `parquet:"instrument_id"`
	Symbol          string  `parquet:"symbol"`
	Timeframe       string  `parquet:"timeframe"`
	TsOpen          int64   `parquet:"ts_open,timestamp(nanosecond)"`
	TsClose         int64   `parquet:"ts_close,timestamp(nanosecond)"`
	Open            float64 `parquet:"open"`
	High            float64 `parquet:"high"`
	Low             float64 `parquet:"low"`
	Close           float64 `parquet:"close"`
	Volume          float64 `parquet:"volume"`
	VWAP            float64 `parquet:"vwap"`
	TradeCount      int64   `parquet:"trade_count"`
	Feed            string  `parquet:"feed"`
	Source          string  `parquet:"source"`
	Revision        int32   `parquet:"revision"`
	IsFinal         bool    `parquet:"is_final"`
	Session         string  `parquet:"session"`
	Repaired        bool    `parquet:"repaired"`
	AdjustmentBasis string  `parquet:"adjustment_basis"`
	AvailableAt     int64   `parquet:"available_at,timestamp(nanosecond)"`
	IngestedAt      int64   `parquet:"ingested_at,timestamp(nanosecond)"`
}

// Key is the natural key (ST-004).
func (r BarRow) Key() string {
	return r.InstrumentID + "|" + r.Timeframe + "|" + strconv.FormatInt(r.TsOpen, 10) + "|" + r.Feed
}

// sameContent reports whether two versions carry the same market data (provenance ignored).
func (r BarRow) sameContent(o BarRow) bool {
	return r.Open == o.Open && r.High == o.High && r.Low == o.Low && r.Close == o.Close && r.Volume == o.Volume &&
		r.VWAP == o.VWAP && r.TradeCount == o.TradeCount && r.Session == o.Session
}

// ContextRow is the Parquet schema of context events (FMP fundamentals snapshots, calendars, ...),
// keyed for point-in-time joins on available_at (DF-002, DF-010).
type ContextRow struct {
	Kind          string `parquet:"kind"`
	InstrumentID  string `parquet:"instrument_id"`
	Symbol        string `parquet:"symbol"`
	EventTime     int64  `parquet:"event_time,timestamp(nanosecond)"`
	AvailableAt   int64  `parquet:"available_at,timestamp(nanosecond)"`
	IngestedAt    int64  `parquet:"ingested_at,timestamp(nanosecond)"`
	Source        string `parquet:"source"`
	Vintage       string `parquet:"vintage"`
	PayloadJSON   string `parquet:"payload_json"`
	PayloadSHA256 string `parquet:"payload_sha256"`
	// RecordKey identifies the logical record across snapshots (CX-002); readers fall back to
	// event_time for rows written before it existed.
	RecordKey string `parquet:"record_key"`
	EventID   string `parquet:"event_id"`
}

// FileInfo describes one lake file (manifest row, ST-003).
type FileInfo struct {
	Path      string // relative to the lake root
	Dataset   string
	Partition string
	Kind      string // part | staging | revisions | ref
	Rows      int64
	Bytes     int64
	SHA256    string
	MinTs     int64
	MaxTs     int64
}

// Manifest records lake files (Postgres in production).
type Manifest interface {
	Record(ctx context.Context, f FileInfo) error
	Remove(ctx context.Context, path string) error
	Live(ctx context.Context) ([]FileInfo, error)
}

// MemoryManifest is a Manifest for tests and tools.
type MemoryManifest struct {
	mu    sync.Mutex
	files map[string]FileInfo
}

// NewMemoryManifest returns an empty manifest.
func NewMemoryManifest() *MemoryManifest { return &MemoryManifest{files: map[string]FileInfo{}} }

// Record implements Manifest.
func (m *MemoryManifest) Record(_ context.Context, f FileInfo) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.files[f.Path] = f
	return nil
}

// Remove implements Manifest.
func (m *MemoryManifest) Remove(_ context.Context, p string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.files, p)
	return nil
}

// Live implements Manifest.
func (m *MemoryManifest) Live(context.Context) ([]FileInfo, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]FileInfo, 0, len(m.files))
	for _, f := range m.files {
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Path < out[j].Path })
	return out, nil
}

// Lake is the writer. One process (marketdata) owns it (D4); Writer methods are safe for concurrent
// use within that process.
type Lake struct {
	Root     string
	Manifest Manifest
	mu       sync.Mutex // serializes writes per process
	locks    map[string]*sync.Mutex
}

// New returns a lake rooted at root.
func New(root string, m Manifest) *Lake {
	return &Lake{Root: root, Manifest: m, locks: map[string]*sync.Mutex{}}
}

func (l *Lake) partLock(partition string) *sync.Mutex {
	l.mu.Lock()
	defer l.mu.Unlock()
	m, ok := l.locks[partition]
	if !ok {
		m = &sync.Mutex{}
		l.locks[partition] = m
	}
	return m
}

func newULID() string { return ulid.MustNew(ulid.Now(), rand.Reader).String() }

// BarPartition returns the partition directory (relative) of a bar.
func BarPartition(dataset, feed, tf string, tsOpen int64) string {
	t := time.Unix(0, tsOpen).UTC()
	return fmt.Sprintf("%s/feed=%s/timeframe=%s/year=%04d/month=%02d", dataset, feed, tf, t.Year(), int(t.Month()))
}

// ContextPartition returns the partition directory of a context row.
func ContextPartition(kind string, eventTime int64) string {
	t := time.Unix(0, eventTime).UTC()
	return fmt.Sprintf("context/kind=%s/year=%04d/month=%02d", kind, t.Year(), int(t.Month()))
}

// writeAtomic writes rows to dir/name via a temporary file, fsync, and rename (ST-002).
func writeAtomic[T any](root, rel, name string, rows []T, meta map[string]string) (FileInfo, error) {
	dir := filepath.Join(root, rel)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return FileInfo{}, err
	}
	tmp := filepath.Join(dir, "."+name+".tmp")
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o640) //nolint:gosec // path built from validated partition keys
	if err != nil {
		return FileInfo{}, err
	}
	h := sha256.New()
	cw := &countWriter{w: io.MultiWriter(f, h)}
	opts := []parquet.WriterOption{parquet.Compression(&zstd.Codec{}), parquet.KeyValueMetadata("tb.schema_version", SchemaVersion),
		parquet.PageBufferSize(1 << 20)}
	for _, k := range slices.Sorted(maps.Keys(meta)) {
		opts = append(opts, parquet.KeyValueMetadata(k, meta[k]))
	}
	w := parquet.NewGenericWriter[T](cw, opts...)
	if _, err := w.Write(rows); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return FileInfo{}, err
	}
	if err := w.Close(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return FileInfo{}, err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return FileInfo{}, err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return FileInfo{}, err
	}
	final := filepath.Join(dir, name)
	if err := os.Rename(tmp, final); err != nil {
		_ = os.Remove(tmp)
		return FileInfo{}, err
	}
	syncDir(dir)
	return FileInfo{Path: filepath.ToSlash(filepath.Join(rel, name)), Rows: int64(len(rows)), Bytes: cw.n, SHA256: hex.EncodeToString(h.Sum(nil))}, nil
}

func syncDir(dir string) {
	if d, err := os.Open(dir); err == nil { //nolint:gosec // lake directory
		_ = d.Sync()
		_ = d.Close()
	}
}

type countWriter struct {
	w io.Writer
	n int64
}

func (c *countWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += int64(n)
	return n, err
}

func datasetOf(partition string) string {
	ds, _, _ := strings.Cut(partition, "/")
	return ds
}

// listFiles returns the visible Parquet files of a partition by kind prefix.
func (l *Lake) listFiles(partition, prefix string) ([]string, error) {
	ents, err := os.ReadDir(filepath.Join(l.Root, partition))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var out []string
	for _, e := range ents {
		n := e.Name()
		if !e.IsDir() && strings.HasPrefix(n, prefix) && strings.HasSuffix(n, ".parquet") {
			out = append(out, n)
		}
	}
	sort.Strings(out)
	return out, nil
}

func readRows[T any](root, partition, name string) ([]T, error) {
	return parquet.ReadFile[T](filepath.Join(root, partition, name))
}

func (l *Lake) remove(ctx context.Context, partition, name string) error {
	p := filepath.Join(l.Root, partition, name)
	if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
		return err
	}
	if l.Manifest != nil {
		return l.Manifest.Remove(ctx, filepath.ToSlash(filepath.Join(partition, name)))
	}
	return nil
}

func (l *Lake) record(ctx context.Context, fi FileInfo, partition, kind string) error {
	fi.Dataset, fi.Partition, fi.Kind = datasetOf(partition), partition, kind
	if l.Manifest == nil {
		return nil
	}
	return l.Manifest.Record(ctx, fi)
}

// VerifyResult lists manifest/disk inconsistencies.
type VerifyResult struct {
	Checked   int
	Missing   []string // in manifest, not on disk
	Corrupt   []string // checksum mismatch
	Untracked []string // on disk, not in manifest
}

// OK reports a clean verification.
func (v VerifyResult) OK() bool { return len(v.Missing)+len(v.Corrupt)+len(v.Untracked) == 0 }

// Verify recomputes checksums of every live manifest file and finds untracked files (ST-003).
func (l *Lake) Verify(ctx context.Context) (VerifyResult, error) {
	var res VerifyResult
	live, err := l.Manifest.Live(ctx)
	if err != nil {
		return res, err
	}
	known := map[string]bool{}
	for _, f := range live {
		known[f.Path] = true
		res.Checked++
		b, err := os.ReadFile(filepath.Join(l.Root, filepath.FromSlash(f.Path))) //nolint:gosec // manifest path under the lake root
		if err != nil {
			res.Missing = append(res.Missing, f.Path)
			continue
		}
		sum := sha256.Sum256(b)
		if hex.EncodeToString(sum[:]) != f.SHA256 {
			res.Corrupt = append(res.Corrupt, f.Path)
		}
	}
	err = filepath.WalkDir(l.Root, func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(p, ".parquet") || strings.HasPrefix(d.Name(), ".") {
			return err
		}
		rel, _ := filepath.Rel(l.Root, p)
		rel = filepath.ToSlash(rel)
		if strings.HasPrefix(rel, "snapshots/") || strings.HasPrefix(rel, "spikes/") {
			return nil // written by the feature materializer / spike tools, not the market-data writer
		}
		if !known[rel] {
			res.Untracked = append(res.Untracked, rel)
		}
		return nil
	})
	return res, err
}
