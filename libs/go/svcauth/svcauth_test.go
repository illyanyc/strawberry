package svcauth

import (
	"crypto/ed25519"
	"errors"
	"strings"
	"testing"
	"time"
)

func newPair(t *testing.T, name string) (*Signer, ed25519.PublicKey) {
	t.Helper()
	seed, pub, err := GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	s, err := NewSigner(name, seed)
	if err != nil {
		t.Fatal(err)
	}
	return s, pub
}

// SE-040: only listed callers with valid, fresh tokens addressed to the receiver are accepted.
func TestSE040TokenVerification(t *testing.T) {
	gw, gwPub := newPair(t, "api-gateway")
	evil, _ := newPair(t, "api-gateway") // same name, different key
	v := NewVerifier("broker-gateway", map[string]ed25519.PublicKey{"api-gateway": gwPub})

	tok, _ := gw.Token("broker-gateway")
	if c, err := v.Verify(tok); err != nil || c.Issuer != "api-gateway" {
		t.Fatalf("valid token rejected: %v", err)
	}
	cases := map[string]struct {
		tok  string
		want error
	}{
		"wrong audience": {must(gw.Token("ledger")), ErrAudience},
		"forged key":     {must(evil.Token("broker-gateway")), ErrSignature},
		"garbage":        {"v1.abc", ErrMalformed},
		"tampered":       {strings.Replace(tok, "v1.", "v1.x", 1), nil},
	}
	for name, c := range cases {
		_, err := v.Verify(c.tok)
		if err == nil || (c.want != nil && !errors.Is(err, c.want)) {
			t.Fatalf("%s: expected error %v, got %v", name, c.want, err)
		}
	}

	gw.now = func() time.Time { return time.Now().Add(-5 * time.Minute) }
	old, _ := gw.Token("broker-gateway")
	if _, err := v.Verify(old); !errors.Is(err, ErrExpired) {
		t.Fatalf("expired token accepted: %v", err)
	}
}

func must(s string, err error) string {
	if err != nil {
		panic(err)
	}
	return s
}
