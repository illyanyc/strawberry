package cfaccess

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type signer struct {
	key *rsa.PrivateKey
	kid string
}

func (s signer) token(t *testing.T, alg string, claims map[string]any) string {
	t.Helper()
	h, _ := json.Marshal(map[string]string{"alg": alg, "kid": s.kid, "typ": "JWT"})
	c, _ := json.Marshal(claims)
	signing := b64.EncodeToString(h) + "." + b64.EncodeToString(c)
	d := sha256.Sum256([]byte(signing))
	sig, err := rsa.SignPKCS1v15(rand.Reader, s.key, crypto.SHA256, d[:])
	if err != nil {
		t.Fatal(err)
	}
	return signing + "." + b64.EncodeToString(sig)
}

func setup(t *testing.T) (*Verifier, signer, *int) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	s := signer{key: key, kid: "k1"}
	fetches := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		fetches++
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []map[string]string{{
			"kid": "k1", "kty": "RSA", "alg": "RS256",
			"n": b64.EncodeToString(key.N.Bytes()), "e": b64.EncodeToString(big.NewInt(int64(key.E)).Bytes()),
		}}})
	}))
	t.Cleanup(srv.Close)
	now := time.Unix(1_800_000_000, 0)
	return &Verifier{Team: "team.cloudflareaccess.com", CertsURL: srv.URL, Now: func() time.Time { return now }}, s, &fetches
}

func TestSE064AccessTokenVerification(t *testing.T) {
	v, s, fetches := setup(t)
	ctx := context.Background()
	base := map[string]any{"aud": []string{"tv-aud"}, "iss": "https://team.cloudflareaccess.com", "exp": 1_800_000_600, "iat": 1_800_000_000, "nbf": 1_800_000_000}
	with := func(k string, val any) map[string]any {
		m := map[string]any{}
		for kk, vv := range base {
			m[kk] = vv
		}
		m[k] = val
		return m
	}
	if err := v.Verify(ctx, s.token(t, "RS256", base), "tv-aud"); err != nil {
		t.Fatalf("valid token: %v", err)
	}
	if err := v.Verify(ctx, s.token(t, "RS256", with("aud", "tv-aud")), "tv-aud"); err != nil {
		t.Fatalf("string aud: %v", err)
	}
	cases := map[string]struct {
		tok  string
		want error
	}{
		"missing":          {"", ErrMissing},
		"garbage":          {"a.b", ErrMalformed},
		"wrong audience":   {s.token(t, "RS256", base), ErrClaims},
		"wrong issuer":     {s.token(t, "RS256", with("iss", "https://evil.cloudflareaccess.com")), ErrClaims},
		"expired":          {s.token(t, "RS256", with("exp", 1_799_999_000)), ErrClaims},
		"not yet valid":    {s.token(t, "RS256", with("nbf", 1_800_001_000)), ErrClaims},
		"alg none":         {s.token(t, "none", base), ErrMalformed},
		"tampered payload": {tamper(s.token(t, "RS256", base)), ErrSignature},
	}
	for name, c := range cases {
		aud := "tv-aud"
		if name == "wrong audience" {
			aud = "ui-aud"
		}
		if err := v.Verify(ctx, c.tok, aud); !errors.Is(err, c.want) {
			t.Errorf("%s: got %v, want %v", name, err, c.want)
		}
	}
	other, _ := rsa.GenerateKey(rand.Reader, 2048)
	if err := v.Verify(ctx, signer{key: other, kid: "k1"}.token(t, "RS256", base), "tv-aud"); !errors.Is(err, ErrSignature) {
		t.Errorf("foreign key: %v", err)
	}
	// Unknown kids refresh the key set at most once a minute.
	before := *fetches
	for range 5 {
		_ = v.Verify(ctx, signer{key: other, kid: "unknown"}.token(t, "RS256", base), "tv-aud")
	}
	if *fetches-before > 1 {
		t.Errorf("unknown kid triggered %d fetches", *fetches-before)
	}
}

func tamper(tok string) string {
	b := []byte(tok)
	i := len(b) / 2
	if b[i] == 'A' {
		b[i] = 'B'
	} else {
		b[i] = 'A'
	}
	return string(b)
}
