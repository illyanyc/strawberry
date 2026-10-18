package cache

import (
	"context"
	"errors"
	"os"
	"sort"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
)

func bar(ts int64, close string) *mdv1.Bar {
	return &mdv1.Bar{InstrumentId: "I1", TsOpen: ts, TsClose: ts + int64(time.Minute), Close: &commonv1.Decimal{Value: close}, IsFinal: true}
}

func TestST009BarKeysRingAndCorrections(t *testing.T) {
	s := miniredis.RunT(t)
	c := &Client{R: redis.NewClient(&redis.Options{Addr: s.Addr()})}
	ctx := context.Background()
	for i := range RingLen + 10 {
		if err := c.PutBar(ctx, "1m", bar(int64(i)*int64(time.Minute), "1")); err != nil {
			t.Fatal(err)
		}
	}
	// Correction of the newest bar replaces the head; an older bar never moves bar:last back.
	if err := c.PutBar(ctx, "1m", bar(int64(RingLen+9)*int64(time.Minute), "2")); err != nil {
		t.Fatal(err)
	}
	if err := c.PutBar(ctx, "1m", bar(0, "9")); err != nil {
		t.Fatal(err)
	}
	last, err := c.LastBar(ctx, "1m", "I1")
	if err != nil || last.GetClose().GetValue() != "2" {
		t.Fatalf("last %+v %v", last, err)
	}
	ring, _ := c.Ring(ctx, "1m", "I1", 1000)
	if len(ring) != RingLen || ring[len(ring)-1].GetClose().GetValue() != "2" {
		t.Fatalf("ring len %d", len(ring))
	}
	if !sort.SliceIsSorted(ring, func(i, j int) bool { return ring[i].GetTsOpen() < ring[j].GetTsOpen() }) {
		t.Fatal("ring must be oldest-first")
	}
	if ttl := s.TTL(BarLastKey("1m", "I1")); ttl != BarTTL {
		t.Fatalf("ttl %v", ttl)
	}
	if _, err := c.LastBar(ctx, "1m", "nope"); !errors.Is(err, ErrMiss) {
		t.Fatal("miss")
	}
	if err := c.SetWatermark(ctx, "1m", "alpaca.sip", 42); err != nil {
		t.Fatal(err)
	}
	if w, _ := c.Watermark(ctx, "1m", "alpaca.sip"); w != 42 {
		t.Fatal("watermark")
	}
}

// TestST009LatestBarReadLatency measures bar:last read latency against a real Valkey/Redis
// (TB_TEST_VALKEY_ADDR, e.g. 127.0.0.1:6379). Target: p99 < 1 ms (section 03 NFR).
func TestST009LatestBarReadLatency(t *testing.T) {
	addr := os.Getenv("TB_TEST_VALKEY_ADDR")
	if addr == "" {
		t.Skip("TB_TEST_VALKEY_ADDR not set")
	}
	c := &Client{R: redis.NewClient(&redis.Options{Addr: addr})}
	ctx := context.Background()
	if err := c.PutBar(ctx, "1m", bar(1, "100.25")); err != nil {
		t.Fatal(err)
	}
	const n = 20000
	lat := make([]time.Duration, n)
	for i := range n {
		s := time.Now()
		if _, err := c.LastBar(ctx, "1m", "I1"); err != nil {
			t.Fatal(err)
		}
		lat[i] = time.Since(s)
	}
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	p50, p99 := lat[n/2], lat[n*99/100]
	t.Logf("bar:last read latency p50=%v p99=%v (n=%d)", p50, p99, n)
	if p99 > time.Millisecond {
		t.Fatalf("p99 %v exceeds 1 ms", p99)
	}
}
