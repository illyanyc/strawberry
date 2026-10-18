package lake

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/parquet-go/parquet-go"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
)

type commonDecimal = commonv1.Decimal

// walkPartitions calls fn for every leaf directory under dataset that contains Parquet files.
func walkPartitions(root, dataset string, fn func(rel string)) error {
	base := filepath.Join(root, dataset)
	if _, err := os.Stat(base); os.IsNotExist(err) {
		return nil
	}
	return filepath.WalkDir(base, func(p string, d os.DirEntry, err error) error {
		if err != nil || !d.IsDir() {
			return err
		}
		ents, err := os.ReadDir(p)
		if err != nil {
			return err
		}
		for _, e := range ents {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".parquet") && !strings.HasPrefix(e.Name(), ".") {
				rel, _ := filepath.Rel(root, p)
				fn(filepath.ToSlash(rel))
				return nil
			}
		}
		return nil
	})
}

// StageContext writes context events (e.g. FMP fundamentals snapshots, one row per fetch) to the
// context dataset. Restated vendor data is never overwritten: every fetch is a new row with its
// own available_at (N2, DF-010).
func (l *Lake) StageContext(ctx context.Context, rows []ContextRow) error {
	byPart := map[string][]ContextRow{}
	for _, r := range rows {
		if r.Kind == "" || r.AvailableAt == 0 || r.IngestedAt == 0 {
			return fmt.Errorf("lake: context row needs kind, available_at, ingested_at")
		}
		if r.PayloadSHA256 == "" {
			s := sha256.Sum256([]byte(r.PayloadJSON))
			r.PayloadSHA256 = hex.EncodeToString(s[:])
		}
		p := ContextPartition(r.Kind, r.EventTime)
		byPart[p] = append(byPart[p], r)
	}
	for p, rs := range byPart {
		lk := l.partLock(p)
		lk.Lock()
		fi, err := writeAtomic(l.Root, p, "staging-"+newULID()+".parquet", rs, nil)
		if err == nil {
			err = l.record(ctx, fi, p, "staging")
		}
		lk.Unlock()
		if err != nil {
			return err
		}
	}
	return nil
}

// WriteRef atomically replaces ref/<table>.parquet (reference-data mirror for Python readers,
// ST-007). The previous file is replaced by rename, so readers see the old or the new table.
func WriteRef[T any](ctx context.Context, l *Lake, table string, rows []T) error {
	lk := l.partLock("ref/" + table)
	lk.Lock()
	defer lk.Unlock()
	raw, err := json.Marshal(rows)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	// Unchanged content keeps the existing file, so periodic exports (and exports right after a
	// restore) do not rewrite identical data.
	if refDigest(filepath.Join(l.Root, "ref", table+".parquet")) == digest {
		return nil
	}
	fi, err := writeAtomic(l.Root, "ref", table+".parquet", rows,
		map[string]string{"tb.exported_at": time.Now().UTC().Format(time.RFC3339), "tb.content_sha256": digest})
	if err != nil {
		return err
	}
	return l.record(ctx, fi, "ref", "ref")
}

func refDigest(path string) string {
	f, err := os.Open(path) //nolint:gosec // path built from the lake root and a fixed table name
	if err != nil {
		return ""
	}
	defer func() { _ = f.Close() }()
	st, err := f.Stat()
	if err != nil {
		return ""
	}
	pf, err := parquet.OpenFile(f, st.Size())
	if err != nil {
		return ""
	}
	v, _ := pf.Lookup("tb.content_sha256")
	return v
}

// PurgeOlderThan deletes partitions of dataset whose `date=YYYY-MM-DD` directory is older than
// keep (tick/quote retention, ST-008). Bars are never purged.
func (l *Lake) PurgeOlderThan(ctx context.Context, dataset string, keep time.Duration, now time.Time) (int, error) {
	if dataset == "bars" || dataset == "bar_revisions" {
		return 0, fmt.Errorf("lake: bars are retained indefinitely (ST-008)")
	}
	cut := now.Add(-keep).UTC().Format("2006-01-02")
	var victims []string
	err := walkPartitions(l.Root, dataset, func(rel string) {
		i := strings.Index(rel, "date=")
		if i >= 0 && rel[i+5:min(len(rel), i+15)] < cut {
			victims = append(victims, rel)
		}
	})
	if err != nil {
		return 0, err
	}
	n := 0
	for _, p := range victims {
		names, err := l.listFiles(p, "")
		if err != nil {
			return n, err
		}
		for _, name := range names {
			if err := l.remove(ctx, p, name); err != nil {
				return n, err
			}
			n++
		}
		_ = os.Remove(filepath.Join(l.Root, p))
	}
	return n, nil
}
