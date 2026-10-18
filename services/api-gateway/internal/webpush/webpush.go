// Package webpush sends Web Push messages: RFC 8291 message encryption (aes128gcm, RFC 8188) and
// RFC 8292 VAPID authentication, with the standard library only. The gateway uses it to deliver
// Alertmanager notifications to the owner's installed PWA (D13, SE-060..SE-063).
package webpush

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hkdf"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// recordSize is the RFC 8188 record size advertised in the header; payloads fit one record.
const recordSize = 4096

// MaxPayload is the largest plaintext accepted: push services guarantee 4096 bytes of body,
// minus the 86-byte header, the delimiter, and the 16-byte tag.
const MaxPayload = recordSize - 86 - 1 - 16

var b64 = base64.RawURLEncoding

// Subscription is a browser's PushSubscription (endpoint plus its p256dh and auth keys).
type Subscription struct {
	Endpoint string
	P256dh   []byte
	Auth     []byte
}

// Keys is the VAPID key pair.
type Keys struct {
	priv *ecdsa.PrivateKey
	// Public is the uncompressed P-256 point the browser passes as applicationServerKey.
	Public []byte
}

// ParseKeys reads a PEM PKCS#8 (or SEC 1) P-256 private key, as written by `tbctl secrets init`.
func ParseKeys(pemBytes []byte) (*Keys, error) {
	blk, _ := pem.Decode(pemBytes)
	if blk == nil {
		return nil, errors.New("webpush: VAPID key is not PEM")
	}
	var priv *ecdsa.PrivateKey
	if k, err := x509.ParsePKCS8PrivateKey(blk.Bytes); err == nil {
		ec, ok := k.(*ecdsa.PrivateKey)
		if !ok {
			return nil, errors.New("webpush: VAPID key is not ECDSA")
		}
		priv = ec
	} else if ec, err2 := x509.ParseECPrivateKey(blk.Bytes); err2 == nil {
		priv = ec
	} else {
		return nil, fmt.Errorf("webpush: parse VAPID key: %w", err)
	}
	if priv.Curve != elliptic.P256() {
		return nil, errors.New("webpush: VAPID key must be P-256")
	}
	pub, err := priv.PublicKey.ECDH()
	if err != nil {
		return nil, err
	}
	return &Keys{priv: priv, Public: pub.Bytes()}, nil
}

// PublicKeyB64 is the applicationServerKey for PushManager.subscribe (base64url, no padding).
func (k *Keys) PublicKeyB64() string { return b64.EncodeToString(k.Public) }

// Encrypt produces an aes128gcm body for one subscription (RFC 8291 §3.4). rnd supplies the
// ephemeral key and salt (crypto/rand in production; fixed in the RFC test vector).
func Encrypt(sub Subscription, plaintext []byte, rnd io.Reader) ([]byte, error) {
	if len(plaintext) > MaxPayload {
		return nil, fmt.Errorf("webpush: payload %d bytes exceeds %d", len(plaintext), MaxPayload)
	}
	if len(sub.Auth) != 16 {
		return nil, errors.New("webpush: auth secret must be 16 bytes")
	}
	curve := ecdh.P256()
	uaPub, err := curve.NewPublicKey(sub.P256dh)
	if err != nil {
		return nil, fmt.Errorf("webpush: p256dh: %w", err)
	}
	asPriv, err := curve.GenerateKey(rnd)
	if err != nil {
		return nil, err
	}
	salt := make([]byte, 16)
	if _, err := io.ReadFull(rnd, salt); err != nil {
		return nil, err
	}
	return encrypt(uaPub, asPriv, salt, sub.Auth, plaintext)
}

func encrypt(uaPub *ecdh.PublicKey, asPriv *ecdh.PrivateKey, salt, auth, plaintext []byte) ([]byte, error) {
	shared, err := asPriv.ECDH(uaPub)
	if err != nil {
		return nil, err
	}
	asPub := asPriv.PublicKey().Bytes()
	// IKM = HKDF(auth_secret, ecdh_secret, "WebPush: info" || 0x00 || ua_public || as_public, 32).
	keyInfo := append(append([]byte("WebPush: info\x00"), uaPub.Bytes()...), asPub...)
	prkKey := hmacSHA256(auth, shared)
	ikm := hmacSHA256(prkKey, append(keyInfo, 0x01))
	prk, err := hkdf.Extract(sha256.New, ikm, salt)
	if err != nil {
		return nil, err
	}
	cek, err := hkdf.Expand(sha256.New, prk, "Content-Encoding: aes128gcm\x00", 16)
	if err != nil {
		return nil, err
	}
	nonce, err := hkdf.Expand(sha256.New, prk, "Content-Encoding: nonce\x00", 12)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(cek)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	// Single (last) record: plaintext followed by the 0x02 delimiter, no padding.
	padded := append(append([]byte{}, plaintext...), 0x02)
	var hdr bytes.Buffer
	hdr.Write(salt)
	_ = binary.Write(&hdr, binary.BigEndian, uint32(recordSize))
	hdr.WriteByte(byte(len(asPub)))
	hdr.Write(asPub)
	return gcm.Seal(hdr.Bytes(), nonce, padded, nil), nil
}

func hmacSHA256(key, data []byte) []byte {
	m := hmac.New(sha256.New, key)
	m.Write(data)
	return m.Sum(nil)
}

// VAPIDHeader returns the RFC 8292 Authorization header value for an endpoint.
func (k *Keys) VAPIDHeader(endpoint, subject string, now time.Time) (string, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.Host == "" {
		return "", errors.New("webpush: endpoint must be an https URL")
	}
	header := b64.EncodeToString([]byte(`{"typ":"JWT","alg":"ES256"}`))
	claims, err := json.Marshal(map[string]any{"aud": u.Scheme + "://" + u.Host, "exp": now.Add(12 * time.Hour).Unix(), "sub": subject})
	if err != nil {
		return "", err
	}
	signing := header + "." + b64.EncodeToString(claims)
	digest := sha256.Sum256([]byte(signing))
	r, s, err := ecdsa.Sign(rand.Reader, k.priv, digest[:])
	if err != nil {
		return "", err
	}
	sig := make([]byte, 64)
	r.FillBytes(sig[:32])
	s.FillBytes(sig[32:])
	return "vapid t=" + signing + "." + b64.EncodeToString(sig) + ", k=" + k.PublicKeyB64(), nil
}

// Urgency is the RFC 8030 Urgency header.
type Urgency string

// Urgencies.
const (
	UrgencyNormal Urgency = "normal"
	UrgencyHigh   Urgency = "high"
)

// Message is one push to one subscription.
type Message struct {
	Payload []byte
	TTL     time.Duration
	Urgency Urgency
	// Topic replaces an undelivered message with the same topic (≤ 32 URL-safe characters).
	Topic string
}

// Sender posts encrypted messages to push services.
type Sender struct {
	Keys    *Keys
	Subject string
	Client  *http.Client
	Now     func() time.Time
}

// ErrGone means the subscription no longer exists (404/410): delete it.
var ErrGone = errors.New("webpush: subscription gone")

// Send encrypts and posts one message. It returns ErrGone for 404/410 and an error carrying the
// HTTP status for other failures; response bodies are never returned (they may echo the endpoint).
func (s *Sender) Send(ctx context.Context, sub Subscription, m Message) error {
	body, err := Encrypt(sub, m.Payload, rand.Reader)
	if err != nil {
		return err
	}
	now := time.Now
	if s.Now != nil {
		now = s.Now
	}
	auth, err := s.Keys.VAPIDHeader(sub.Endpoint, s.Subject, now())
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, sub.Endpoint, bytes.NewReader(body))
	if err != nil {
		return err
	}
	ttl := int(m.TTL / time.Second)
	if ttl <= 0 {
		ttl = 3600
	}
	req.Header.Set("Authorization", auth)
	req.Header.Set("Content-Encoding", "aes128gcm")
	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("TTL", strconv.Itoa(ttl))
	if m.Urgency != "" {
		req.Header.Set("Urgency", string(m.Urgency))
	}
	if m.Topic != "" {
		req.Header.Set("Topic", m.Topic)
	}
	client := s.Client
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("webpush: post: %w", redactURLError(err))
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	_ = resp.Body.Close()
	switch {
	case resp.StatusCode == http.StatusNotFound || resp.StatusCode == http.StatusGone:
		return ErrGone
	case resp.StatusCode >= 200 && resp.StatusCode < 300:
		return nil
	default:
		return fmt.Errorf("webpush: push service answered %d", resp.StatusCode)
	}
}

// redactURLError drops the URL (a capability: anyone holding it can push to the device) from
// *url.Error values so endpoints never reach logs or the UI.
func redactURLError(err error) error {
	var ue *url.Error
	if errors.As(err, &ue) {
		return fmt.Errorf("%s: %w", ue.Op, ue.Err)
	}
	return err
}

// ServiceHost returns the endpoint's host (shown in the UI instead of the endpoint).
func ServiceHost(endpoint string) string {
	u, err := url.Parse(endpoint)
	if err != nil {
		return ""
	}
	return u.Hostname()
}

// EndpointHash is the hex SHA-256 prefix (16 bytes) of an endpoint: a stable, non-secret ID a
// browser can compute to recognise its own subscription.
func EndpointHash(endpoint string) string {
	sum := sha256.Sum256([]byte(endpoint))
	return fmt.Sprintf("%x", sum[:16])
}

// DefaultAllowedHosts are the push services of Chrome/Edge/Android, Firefox, Safari/iOS, and Windows.
var DefaultAllowedHosts = []string{"fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com", "web.push.apple.com", "notify.windows.com"}

// AllowedEndpoint reports whether an endpoint is an https URL on an allowed push service (the host
// or a subdomain of it), so a subscription can never turn the gateway into an SSRF proxy.
func AllowedEndpoint(endpoint string, hosts []string) bool {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Host == "" || len(endpoint) > 1024 {
		return false
	}
	if p := u.Port(); p != "" && p != "443" {
		return false
	}
	h := strings.ToLower(u.Hostname())
	for _, a := range hosts {
		a = strings.ToLower(strings.TrimSpace(a))
		if a != "" && (h == a || strings.HasSuffix(h, "."+a)) {
			return true
		}
	}
	return false
}
