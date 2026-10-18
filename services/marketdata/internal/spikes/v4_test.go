package spikes

import (
	"context"
	"sync"
	"testing"
	"time"
)

// TestV4AtomicRenameNoPartialReads runs the V4 harness on the local filesystem: one writer, four
// concurrent readers. A reader must never observe a partially written file (ST-002). On the Mac
// Mini, run the same harness across VirtioFS with `make spike-v4` (writer in the container, reader
// native) and record the result in docs/verify-early.md.
func TestV4AtomicRenameNoPartialReads(t *testing.T) {
	dir := t.TempDir()
	ctx := context.Background()
	d := 2 * time.Second
	if testing.Short() {
		d = 500 * time.Millisecond
	}
	var wg sync.WaitGroup
	results := make([]V4Result, 4)
	for i := range results {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r, err := V4Reader(ctx, dir, d)
			if err != nil {
				t.Error(err)
			}
			results[i] = r
		}(i)
	}
	w, err := V4Writer(ctx, dir, d)
	if err != nil {
		t.Fatal(err)
	}
	wg.Wait()
	var reads, partial, vanished int64
	for _, r := range results {
		reads += r.Reads
		partial += r.Partial
		vanished += r.Vanished
		if r.Partial > 0 {
			t.Errorf("partial read: %s", r.Detail)
		}
	}
	t.Logf("V4 local: writes=%d reads=%d partial=%d vanished=%d", w.Writes, reads, partial, vanished)
	if w.Writes < 5 || reads < 5 {
		t.Fatalf("harness did too little work: writes=%d reads=%d", w.Writes, reads)
	}
}
