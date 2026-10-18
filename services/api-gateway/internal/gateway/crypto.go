package gateway

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base32"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"strings"

	"golang.org/x/crypto/hkdf"
)

// keys are derived from the gateway master key (secret gateway_auth_key) with HKDF, one per purpose.
type keys struct {
	totpEnc  []byte // AES-256-GCM key for TOTP secrets (SE-008)
	codeHMAC []byte // HMAC key for recovery codes and setup tokens (SE-007, SE-009)
	pushEnc  []byte // AES-256-GCM key for Web Push subscriptions (SE-061)
}

func deriveKeys(master []byte) (keys, error) {
	if len(master) < 32 {
		return keys{}, errors.New("gateway_auth_key must be at least 32 bytes")
	}
	derive := func(info string) ([]byte, error) {
		k := make([]byte, 32)
		_, err := io.ReadFull(hkdf.New(sha256.New, master, nil, []byte(info)), k)
		return k, err
	}
	t, err := derive("tb1/gateway/totp-enc")
	if err != nil {
		return keys{}, err
	}
	h, err := derive("tb1/gateway/code-hmac")
	if err != nil {
		return keys{}, err
	}
	p, err := derive("tb1/gateway/push-enc")
	if err != nil {
		return keys{}, err
	}
	return keys{totpEnc: t, codeHMAC: h, pushEnc: p}, nil
}

// seal encrypts with AES-256-GCM under key, binding the ciphertext to aad.
func seal(key, plain []byte, aad string) ([]byte, error) {
	gcm, err := newGCM(key)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return gcm.Seal(nonce, nonce, plain, []byte(aad)), nil
}

func openSealed(key, sealed []byte, aad string) ([]byte, error) {
	gcm, err := newGCM(key)
	if err != nil {
		return nil, err
	}
	if len(sealed) < gcm.NonceSize() {
		return nil, errors.New("ciphertext too short")
	}
	return gcm.Open(nil, sealed[:gcm.NonceSize()], sealed[gcm.NonceSize():], []byte(aad))
}

func (k keys) sealTOTP(secret []byte, ownerID string) ([]byte, error) {
	gcm, err := newGCM(k.totpEnc)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return gcm.Seal(nonce, nonce, secret, []byte("tb1-totp|"+ownerID)), nil
}

func (k keys) openTOTP(sealed []byte, ownerID string) ([]byte, error) {
	gcm, err := newGCM(k.totpEnc)
	if err != nil {
		return nil, err
	}
	if len(sealed) < gcm.NonceSize() {
		return nil, errors.New("totp: ciphertext too short")
	}
	return gcm.Open(nil, sealed[:gcm.NonceSize()], sealed[gcm.NonceSize():], []byte("tb1-totp|"+ownerID))
}

func (k keys) codeHash(code string) []byte {
	m := hmac.New(sha256.New, k.codeHMAC)
	m.Write([]byte(normalizeCode(code)))
	return m.Sum(nil)
}

func newGCM(key []byte) (cipher.AEAD, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

// randomToken returns n random bytes encoded as unpadded base64url.
func randomToken(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func sha256Sum(s string) []byte {
	h := sha256.Sum256([]byte(s))
	return h[:]
}

var codeEncoding = base32.NewEncoding("abcdefghijkmnpqrstuvwxyz23456789").WithPadding(base32.NoPadding)

// newRecoveryCode returns a code like "k7m2p-qx9ra" (50 bits of entropy).
func newRecoveryCode() (string, error) {
	b := make([]byte, 7)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	s := codeEncoding.EncodeToString(b)[:10]
	return fmt.Sprintf("%s-%s", s[:5], s[5:]), nil
}

func normalizeCode(code string) string {
	return strings.ToLower(strings.NewReplacer("-", "", " ", "").Replace(strings.TrimSpace(code)))
}
