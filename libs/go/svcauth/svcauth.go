// Package svcauth authenticates service-to-service calls on the internal network with short-lived
// Ed25519-signed tokens (rule SE-040). Each service holds its own private key (a mounted secret);
// a receiving service holds the public keys of the callers it accepts and an allowlist per method.
//
// Token: "v1.<base64url(claims JSON)>.<base64url(ed25519 signature over 'v1.'+claims)>".
package svcauth

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/oklog/ulid/v2"
)

// TokenTTL bounds replay: tokens are minted per call and expire quickly.
const TokenTTL = 60 * time.Second

// Leeway tolerates small clock differences between containers.
const Leeway = 10 * time.Second

// Claims identify the calling and receiving services.
type Claims struct {
	Issuer   string `json:"iss"`
	Audience string `json:"aud"`
	IssuedAt int64  `json:"iat"`
	Expires  int64  `json:"exp"`
	ID       string `json:"jti"`
}

// Errors returned by Verify.
var (
	ErrMalformed = errors.New("svcauth: malformed token")
	ErrSignature = errors.New("svcauth: bad signature or unknown issuer")
	ErrExpired   = errors.New("svcauth: token expired or not yet valid")
	ErrAudience  = errors.New("svcauth: wrong audience")
)

// Signer mints tokens for one service.
type Signer struct {
	service string
	key     ed25519.PrivateKey
	now     func() time.Time
}

// NewSigner creates a signer from a 32-byte Ed25519 seed.
func NewSigner(service string, seed []byte) (*Signer, error) {
	if len(seed) != ed25519.SeedSize {
		return nil, fmt.Errorf("svcauth: seed must be %d bytes", ed25519.SeedSize)
	}
	return &Signer{service: service, key: ed25519.NewKeyFromSeed(seed), now: time.Now}, nil
}

// Token mints a token addressed to audience.
func (s *Signer) Token(audience string) (string, error) {
	now := s.now()
	c := Claims{
		Issuer: s.service, Audience: audience,
		IssuedAt: now.Unix(), Expires: now.Add(TokenTTL).Unix(),
		ID: ulid.MustNew(ulid.Timestamp(now), rand.Reader).String(),
	}
	payload, err := json.Marshal(c)
	if err != nil {
		return "", err
	}
	body := "v1." + base64.RawURLEncoding.EncodeToString(payload)
	sig := ed25519.Sign(s.key, []byte(body))
	return body + "." + base64.RawURLEncoding.EncodeToString(sig), nil
}

// Verifier checks tokens addressed to one service.
type Verifier struct {
	service string
	keys    map[string]ed25519.PublicKey
	now     func() time.Time
}

// NewVerifier creates a verifier for service with the public keys of trusted callers.
func NewVerifier(service string, callerKeys map[string]ed25519.PublicKey) *Verifier {
	return &Verifier{service: service, keys: callerKeys, now: time.Now}
}

// Verify returns the claims of a valid token addressed to this service.
func (v *Verifier) Verify(token string) (Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 || parts[0] != "v1" {
		return Claims{}, ErrMalformed
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return Claims{}, ErrMalformed
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return Claims{}, ErrMalformed
	}
	var c Claims
	if err := json.Unmarshal(payload, &c); err != nil {
		return Claims{}, ErrMalformed
	}
	key, ok := v.keys[c.Issuer]
	if !ok || !ed25519.Verify(key, []byte(parts[0]+"."+parts[1]), sig) {
		return Claims{}, ErrSignature
	}
	now := v.now()
	if now.After(time.Unix(c.Expires, 0).Add(Leeway)) || now.Before(time.Unix(c.IssuedAt, 0).Add(-Leeway)) ||
		c.Expires-c.IssuedAt > int64(TokenTTL/time.Second) {
		return Claims{}, ErrExpired
	}
	if c.Audience != v.service {
		return Claims{}, ErrAudience
	}
	return c, nil
}

type callerKey struct{}

// WithCaller stores the authenticated calling service in ctx.
func WithCaller(ctx context.Context, service string) context.Context {
	return context.WithValue(ctx, callerKey{}, service)
}

// Caller returns the authenticated calling service, or "".
func Caller(ctx context.Context) string {
	s, _ := ctx.Value(callerKey{}).(string)
	return s
}

// GenerateKey returns a new random seed and its public key (used by `tbctl secrets init`).
func GenerateKey() (seed []byte, pub ed25519.PublicKey, err error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	return priv.Seed(), pub, nil
}
