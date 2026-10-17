// Package spikes holds the verify-early spike harnesses owned by marketdata (master prompt V2, V3,
// V4, V7, V9; results in docs/verify-early.md).
package spikes

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math/rand/v2"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/parquet-go/parquet-go"
	"github.com/parquet-go/parquet-go/compress/zstd"
)

// V4Row is the stress-test row: every file holds V4Rows rows with the same Seq.
type V4Row struct {
	Seq   int64   `parquet:"seq"`
	I     int64   `parquet:"i"`
	Value float64 `parquet:"value"`
}

// V4Rows per file (large enough to span many pages).
const V4Rows = 20000

// V4Result summarizes one role's run.
type V4Result struct {
	Role     string `json:"role"`
	Writes   int64  `json:"writes,omitempty"`
	Reads    int64  `json:"reads,omitempty"`
	OK       int64  `json:"ok,omitempty"`
	Vanished int64  `json:"vanished,omitempty"` // file deleted between listing and open (expected for new-name compaction)
	Partial  int64  `json:"partial,omitempty"`  // unreadable or wrong row count/sequence: the failure V4 looks for
	Detail   string `json:"detail,omitempty"`
}

// JSON renders the result.
func (r V4Result) JSON() string {
	b, _ := json.Marshal(r)
	return string(b)
}

// V4Writer alternates the two lake write patterns for duration: (a) write-temp + rename over the
// same name (ref tables), (b) write a new name + delete the old one (compaction).
func V4Writer(ctx context.Context, dir string, duration time.Duration) (V4Result, error) {
	res := V4Result{Role: "writer"}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return res, err
	}
	deadline := time.Now().Add(duration)
	var prev string
	for seq := int64(1); time.Now().Before(deadline) && ctx.Err() == nil; seq++ {
		rows := make([]V4Row, V4Rows)
		for i := range rows {
			rows[i] = V4Row{Seq: seq, I: int64(i), Value: rand.Float64()} //nolint:gosec // test data
		}
		name := "same.parquet"
		if seq%2 == 0 {
			name = fmt.Sprintf("part-%012d.parquet", seq)
		}
		tmp := filepath.Join(dir, "."+name+".tmp")
		f, err := os.Create(tmp) //nolint:gosec // spike directory
		if err != nil {
			return res, err
		}
		w := parquet.NewGenericWriter[V4Row](f, parquet.Compression(&zstd.Codec{}))
		if _, err := w.Write(rows); err != nil {
			return res, err
		}
		if err := w.Close(); err != nil {
			return res, err
		}
		if err := f.Sync(); err != nil {
			return res, err
		}
		if err := f.Close(); err != nil {
			return res, err
		}
		if err := os.Rename(tmp, filepath.Join(dir, name)); err != nil {
			return res, err
		}
		if seq%2 == 0 {
			if prev != "" {
				_ = os.Remove(filepath.Join(dir, prev))
			}
			prev = name
		}
		res.Writes++
	}
	return res, nil
}

// V4Reader repeatedly lists dir and fully reads every visible *.parquet file, checking that each
// file is complete (V4Rows rows, one sequence number).
func V4Reader(ctx context.Context, dir string, duration time.Duration) (V4Result, error) {
	res := V4Result{Role: "reader"}
	var reads, ok, vanished, partial atomic.Int64
	deadline := time.Now().Add(duration)
	for time.Now().Before(deadline) && ctx.Err() == nil {
		ents, err := os.ReadDir(dir)
		if err != nil {
			return res, err
		}
		for _, e := range ents {
			n := e.Name()
			if e.IsDir() || strings.HasPrefix(n, ".") || !strings.HasSuffix(n, ".parquet") {
				continue
			}
			reads.Add(1)
			rows, err := parquet.ReadFile[V4Row](filepath.Join(dir, n))
			switch {
			case errors.Is(err, fs.ErrNotExist):
				vanished.Add(1)
			case err != nil:
				partial.Add(1)
				res.Detail = n + ": " + err.Error()
			case len(rows) != V4Rows || rows[0].Seq != rows[len(rows)-1].Seq:
				partial.Add(1)
				res.Detail = fmt.Sprintf("%s: %d rows", n, len(rows))
			default:
				ok.Add(1)
			}
		}
	}
	res.Reads, res.OK, res.Vanished, res.Partial = reads.Load(), ok.Load(), vanished.Load(), partial.Load()
	return res, nil
}
