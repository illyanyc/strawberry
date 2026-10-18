package attribution

import (
	"strings"
	"testing"
)

const ulid = "01JABCDEFGHJKMNPQRSTVWXYZ0"

func TestBR003AttributionFormat(t *testing.T) {
	ok := []string{
		"tb1-orb15m-" + ulid + "-1",
		"tb1-abc123-" + ulid + "-0",
		"tb1-abcdefgh-" + ulid + "-9999",
		"tb1-manual-" + ulid + "-1",
	}
	for _, s := range ok {
		id, err := Parse(s, 128)
		if err != nil {
			t.Errorf("%s: %v", s, err)
			continue
		}
		if id.String() != s {
			t.Errorf("round trip %s -> %s", s, id.String())
		}
	}
	bad := map[string]string{
		"empty":              "",
		"no prefix":          "orb15m-" + ulid + "-1",
		"wrong version":      "tb2-orb15m-" + ulid + "-1",
		"short id too short": "tb1-orb-" + ulid + "-1",
		"short id too long":  "tb1-abcdefghi-" + ulid + "-1",
		"uppercase short":    "tb1-ORB15M-" + ulid + "-1",
		"ulid with I":        "tb1-orb15m-01JABCDEFGHIKMNPQRSTVWXYZ0-1",
		"ulid too short":     "tb1-orb15m-01JABCDEF-1",
		"no attempt":         "tb1-orb15m-" + ulid,
		"attempt too long":   "tb1-orb15m-" + ulid + "-12345",
		"trailing junk":      "tb1-orb15m-" + ulid + "-1 ",
		"manual app order":   "5f2a7c10-6a0e-4a9e-9b1e-2c8e6c6b1f00",
	}
	for name, s := range bad {
		if _, err := Parse(s, 128); err == nil {
			t.Errorf("%s: %q accepted", name, s)
		}
	}
	long := "tb1-orb15m-" + ulid + "-1"
	if _, err := Parse(long, len(long)-1); err == nil || !strings.Contains(err.Error(), "limit") {
		t.Errorf("broker length limit not enforced: %v", err)
	}
}

func TestVP001IsPlatformAndReplacement(t *testing.T) {
	if IsPlatform("my-app-order") || !IsPlatform("tb1-x") {
		t.Fatal("IsPlatform")
	}
	cur, _ := Parse("tb1-orb15m-"+ulid+"-1", 0)
	if err := ValidReplacement(cur, cur.Next()); err != nil {
		t.Fatal(err)
	}
	if err := ValidReplacement(cur, cur); err == nil {
		t.Fatal("same attempt accepted")
	}
	other := cur.Next()
	other.StrategyShortID = "other1"
	if err := ValidReplacement(cur, other); err == nil {
		t.Fatal("strategy change accepted")
	}
	if !(ID{StrategyShortID: ManualShortID}).Manual() {
		t.Fatal("manual")
	}
}
