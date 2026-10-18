// Package cfaccess verifies Cloudflare Access application tokens (the Cf-Access-Jwt-Assertion
// header): RS256 signatures against the team's published keys, the application audience tag, the
// issuer, and the validity window. The gateway uses it so a request that reached the origin through
// the tunnel without passing Access (for example a misconfigured Bypass policy) is refused (SE-064).
package cfaccess

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Header is the request header Cloudflare Access adds for the origin.
const Header = "Cf-Access-Jwt-Assertion"

// Verifier checks tokens for one team and one or more application audiences.
type Verifier struct {
	// Team is the team domain, e.g. "example.cloudflareaccess.com".
	Team string
	// CertsURL overrides https://<Team>/cdn-cgi/access/certs (tests).
	CertsURL string
	Client   *http.Client
	Now      func() time.Time
	// Leeway tolerates clock skew on exp/nbf/iat.
	Leeway time.Duration

	mu        sync.Mutex
	keys      map[string]*rsa.PublicKey
	fetchedAt time.Time
}

// Errors.
var (
	ErrMissing   = errors.New("cfaccess: token missing")
	ErrMalformed = errors.New("cfaccess: token malformed")
	ErrSignature = errors.New("cfaccess: bad signature")
	ErrClaims    = errors.New("cfaccess: claims rejected")
)

type header struct {
	Alg string `json:"alg"`
	Kid string `json:"kid"`
}

type claims struct {
	Aud audience `json:"aud"`
	Iss string   `json:"iss"`
	Exp int64    `json:"exp"`
	Nbf int64    `json:"nbf"`
	Iat int64    `json:"iat"`
}

// audience accepts a string or an array of strings.
type audience []string

func (a *audience) UnmarshalJSON(b []byte) error {
	var one string
	if json.Unmarshal(b, &one) == nil {
		*a = audience{one}
		return nil
	}
	var many []string
	if err := json.Unmarshal(b, &many); err != nil {
		return err
	}
	*a = many
	return nil
}

var b64 = base64.RawURLEncoding

func (v *Verifier) now() time.Time {
	if v.Now != nil {
		return v.Now()
	}
	return time.Now()
}

// Verify checks token for application audience aud.
func (v *Verifier) Verify(ctx context.Context, token, aud string) error {
	if token == "" {
		return ErrMissing
	}
	parts := strings.Split(token, ".")
	if len(parts) != 3 || len(token) > 8192 {
		return ErrMalformed
	}
	var h header
	if err := decodePart(parts[0], &h); err != nil || h.Alg != "RS256" || h.Kid == "" {
		return ErrMalformed
	}
	key, err := v.key(ctx, h.Kid)
	if err != nil {
		return err
	}
	sig, err := b64.DecodeString(parts[2])
	if err != nil {
		return ErrMalformed
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], sig) != nil {
		return ErrSignature
	}
	var c claims
	if err := decodePart(parts[1], &c); err != nil {
		return ErrMalformed
	}
	now := v.now()
	leeway := v.Leeway
	if leeway == 0 {
		leeway = 30 * time.Second
	}
	switch {
	case !contains(c.Aud, aud):
		return fmt.Errorf("%w: audience", ErrClaims)
	case c.Iss != "https://"+v.Team:
		return fmt.Errorf("%w: issuer", ErrClaims)
	case c.Exp == 0 || now.After(time.Unix(c.Exp, 0).Add(leeway)):
		return fmt.Errorf("%w: expired", ErrClaims)
	case c.Nbf != 0 && now.Add(leeway).Before(time.Unix(c.Nbf, 0)):
		return fmt.Errorf("%w: not yet valid", ErrClaims)
	case c.Iat != 0 && now.Add(leeway).Before(time.Unix(c.Iat, 0)):
		return fmt.Errorf("%w: issued in the future", ErrClaims)
	}
	return nil
}

func decodePart(p string, v any) error {
	b, err := b64.DecodeString(p)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

func contains(xs []string, x string) bool {
	for _, s := range xs {
		if s == x {
			return true
		}
	}
	return false
}

// key returns the public key for kid, refreshing the key set hourly or when kid is unknown (at
// most once a minute, so garbage kids cannot make the gateway hammer Cloudflare).
func (v *Verifier) key(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if k, ok := v.keys[kid]; ok && v.now().Sub(v.fetchedAt) < time.Hour {
		return k, nil
	}
	if v.fetchedAt.IsZero() || v.now().Sub(v.fetchedAt) >= time.Minute {
		keys, err := v.fetch(ctx)
		if err != nil {
			if k, ok := v.keys[kid]; ok {
				return k, nil
			}
			return nil, err
		}
		v.keys, v.fetchedAt = keys, v.now()
	}
	if k, ok := v.keys[kid]; ok {
		return k, nil
	}
	return nil, ErrSignature
}

type jwks struct {
	Keys []struct {
		Kid string `json:"kid"`
		Kty string `json:"kty"`
		N   string `json:"n"`
		E   string `json:"e"`
	} `json:"keys"`
}

func (v *Verifier) fetch(ctx context.Context) (map[string]*rsa.PublicKey, error) {
	url := v.CertsURL
	if url == "" {
		url = "https://" + v.Team + "/cdn-cgi/access/certs"
	}
	client := v.Client
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("cfaccess: fetch keys: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("cfaccess: fetch keys: status %d", resp.StatusCode)
	}
	var set jwks
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&set); err != nil {
		return nil, fmt.Errorf("cfaccess: decode keys: %w", err)
	}
	out := map[string]*rsa.PublicKey{}
	for _, k := range set.Keys {
		if k.Kty != "RSA" || k.Kid == "" {
			continue
		}
		n, err1 := b64.DecodeString(k.N)
		e, err2 := b64.DecodeString(k.E)
		if err1 != nil || err2 != nil || len(e) > 4 {
			continue
		}
		out[k.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}
	}
	if len(out) == 0 {
		return nil, errors.New("cfaccess: no RSA keys published")
	}
	return out, nil
}
