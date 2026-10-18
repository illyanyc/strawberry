package universe

import (
	"context"
	"testing"
	"time"

	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
)

func TestDevUniverseAndMembership(t *testing.T) {
	list, err := Load("")
	if err != nil || len(list) < 100 || list[0] != "SPY" {
		t.Fatalf("dev universe: %d %v", len(list), err)
	}
	ctx := context.Background()
	ref := refdata.NewMemory()
	now := time.Date(2026, 3, 2, 15, 0, 0, 0, time.UTC)
	for _, s := range list[:5] {
		_, _, _ = ref.EnsureInstrument(ctx, s, 1, refdata.Instrument{AssetClass: "us_equity", ListedAt: 1}, "t")
	}
	u := Universes{Ref: ref}
	ids, unknown := u.FromTickers(ctx, list[:6], now)
	if len(ids) != 5 || len(unknown) != 1 {
		t.Fatalf("ids %d unknown %v", len(ids), unknown)
	}
	m, err := u.Set(ctx, "dev", ids, now)
	if err != nil || len(m) != 5 || m["SPY"] == "" {
		t.Fatalf("members %v %v", m, err)
	}
}
