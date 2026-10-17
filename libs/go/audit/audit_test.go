package audit

import (
	"encoding/hex"
	"strings"
	"testing"
)

// SE-022: the canonical encoding is length-prefixed, so shifting bytes between fields changes the hash.
func TestSE022CanonicalEncodingIsUnambiguous(t *testing.T) {
	a := Record{PrevHash: GenesisHash, ID: "01", Actor: "ab", Action: "c"}
	b := Record{PrevHash: GenesisHash, ID: "01", Actor: "a", Action: "bc"}
	if hex.EncodeToString(ComputeHash(a)) == hex.EncodeToString(ComputeHash(b)) {
		t.Fatal("field boundary ambiguity")
	}
}

func TestSE022HashDependsOnPrevAndEveryField(t *testing.T) {
	base := Record{PrevHash: GenesisHash, ID: "id", OccurredAt: 1, Actor: "owner", Action: "auth.login.success",
		Target: "t", BeforeHash: "b", AfterHash: "a", IP: "127.0.0.1", Device: "d", CorrelationID: "c", DetailsJSON: "{}"}
	h0 := hex.EncodeToString(ComputeHash(base))
	mutations := []func(r *Record){
		func(r *Record) { r.PrevHash = make([]byte, 32); r.PrevHash[0] = 1 },
		func(r *Record) { r.ID = "id2" },
		func(r *Record) { r.OccurredAt = 2 },
		func(r *Record) { r.Actor = "assistant" },
		func(r *Record) { r.Action = "x" },
		func(r *Record) { r.Target = "u" },
		func(r *Record) { r.BeforeHash = "c" },
		func(r *Record) { r.AfterHash = "b" },
		func(r *Record) { r.IP = "10.0.0.1" },
		func(r *Record) { r.Device = "e" },
		func(r *Record) { r.CorrelationID = "d" },
		func(r *Record) { r.DetailsJSON = `{"k":1}` },
	}
	for i, m := range mutations {
		r := base
		m(&r)
		if hex.EncodeToString(ComputeHash(r)) == h0 {
			t.Fatalf("mutation %d did not change the hash", i)
		}
	}
}

func TestCanonicalDetailsSortedAndRedacted(t *testing.T) {
	got, err := CanonicalDetails(map[string]any{"z": 1, "a": "x", "api_secret": "hunter2hunter2", "note": "token=abcdef123456"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(got, `{"a":"x","api_secret":"[REDACTED]"`) || strings.Contains(got, "hunter2") || strings.Contains(got, "abcdef123456") {
		t.Fatalf("unexpected canonical details: %s", got)
	}
}
