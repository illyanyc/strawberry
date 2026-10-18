package webpush

import (
	"context"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func mustB64(t *testing.T, s string) []byte {
	t.Helper()
	b, err := b64.DecodeString(s)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// RFC 8291 Appendix A: the exact message from the specification's worked example.
func TestSE060RFC8291TestVector(t *testing.T) {
	curve := ecdh.P256()
	asPriv, err := curve.NewPrivateKey(mustB64(t, "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"))
	if err != nil {
		t.Fatal(err)
	}
	if got := b64.EncodeToString(asPriv.PublicKey().Bytes()); got != "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8" {
		t.Fatalf("as_public = %s", got)
	}
	uaPub, err := curve.NewPublicKey(mustB64(t, "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"))
	if err != nil {
		t.Fatal(err)
	}
	body, err := encrypt(uaPub, asPriv, mustB64(t, "DGv6ra1nlYgDCS1FRnbzlw"), mustB64(t, "BTBZMqHH6r4Tts7J_aSIgg"), []byte("When I grow up, I want to be a watermelon"))
	if err != nil {
		t.Fatal(err)
	}
	want := "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
	if got := b64.EncodeToString(body); got != want {
		t.Fatalf("body mismatch:\n got %s\nwant %s", got, want)
	}
}

func testKeys(t *testing.T) *Keys {
	t.Helper()
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKCS8PrivateKey(priv)
	if err != nil {
		t.Fatal(err)
	}
	k, err := ParseKeys(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}))
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func verifyJWT(pub []byte, token string) bool {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return false
	}
	key, err := ecdsa.ParseUncompressedPublicKey(elliptic.P256(), pub)
	if err != nil {
		return false
	}
	sig, err := b64.DecodeString(parts[2])
	if err != nil || len(sig) != 64 {
		return false
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	return ecdsa.Verify(key, digest[:], new(big.Int).SetBytes(sig[:32]), new(big.Int).SetBytes(sig[32:]))
}

func TestSE060VAPIDHeaderIsAValidES256Token(t *testing.T) {
	k := testKeys(t)
	h, err := k.VAPIDHeader("https://fcm.googleapis.com/fcm/send/abc", "mailto:owner@example.invalid", time.Unix(1_800_000_000, 0))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(h, "vapid t=") || !strings.HasSuffix(h, ", k="+k.PublicKeyB64()) {
		t.Fatalf("header %q", h)
	}
	tok := strings.TrimSuffix(strings.TrimPrefix(h, "vapid t="), ", k="+k.PublicKeyB64())
	if !verifyJWT(k.Public, tok) {
		t.Fatal("VAPID JWT does not verify")
	}
	claims, _ := b64.DecodeString(strings.Split(tok, ".")[1])
	if !strings.Contains(string(claims), `"aud":"https://fcm.googleapis.com"`) {
		t.Fatalf("aud must be the push service origin: %s", claims)
	}
	if _, err := k.VAPIDHeader("http://fcm.googleapis.com/x", "mailto:x", time.Now()); err == nil {
		t.Fatal("plain http endpoint accepted")
	}
}

func TestSE061EndpointAllowlistBlocksSSRF(t *testing.T) {
	cases := map[string]bool{
		"https://fcm.googleapis.com/fcm/send/x":                true,
		"https://web.push.apple.com/QK1x":                      true,
		"https://updates.push.services.mozilla.com/wpush/v2/x": true,
		"https://db5p.notify.windows.com/w/?token=x":           true,
		"http://fcm.googleapis.com/fcm/send/x":                 false,
		"https://fcm.googleapis.com:8443/x":                    false,
		"https://evilfcm.googleapis.com.attacker.test/x":       false,
		"https://user@fcm.googleapis.com/x":                    false,
		"https://169.254.169.254/latest/meta-data":             false,
		"https://postgres:5432/":                               false,
		"https://notfcm.googleapis.co/x":                       false,
	}
	for ep, want := range cases {
		if got := AllowedEndpoint(ep, DefaultAllowedHosts); got != want {
			t.Errorf("AllowedEndpoint(%q) = %v, want %v", ep, got, want)
		}
	}
}

// A fake push service that decrypts with the subscription's private key, so the round trip is
// checked end to end (headers, encryption, status mapping).
func TestSE060SendRoundTripAndGoneMapping(t *testing.T) {
	uaPriv, _ := ecdh.P256().GenerateKey(rand.Reader)
	auth := make([]byte, 16)
	_, _ = rand.Read(auth)
	var gotBody []byte
	var gotHdr http.Header
	status := http.StatusCreated
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotHdr = r.Header.Clone()
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(status)
	}))
	defer srv.Close()
	k := testKeys(t)
	s := &Sender{Keys: k, Subject: "mailto:owner@example.invalid", Client: srv.Client()}
	sub := Subscription{Endpoint: srv.URL + "/push/abc", P256dh: uaPriv.PublicKey().Bytes(), Auth: auth}
	if err := s.Send(context.Background(), sub, Message{Payload: []byte(`{"web_push":8030}`), TTL: time.Minute, Urgency: UrgencyHigh, Topic: "crit"}); err != nil {
		t.Fatal(err)
	}
	if gotHdr.Get("Content-Encoding") != "aes128gcm" || gotHdr.Get("TTL") != "60" || gotHdr.Get("Urgency") != "high" || gotHdr.Get("Topic") != "crit" {
		t.Fatalf("headers %v", gotHdr)
	}
	if plain := decrypt(t, uaPriv, auth, gotBody); string(plain) != `{"web_push":8030}` {
		t.Fatalf("decrypted %q", plain)
	}
	status = http.StatusGone
	if err := s.Send(context.Background(), sub, Message{Payload: []byte("x")}); !errors.Is(err, ErrGone) {
		t.Fatalf("410 -> %v, want ErrGone", err)
	}
	status = http.StatusTooManyRequests
	if err := s.Send(context.Background(), sub, Message{Payload: []byte("x")}); err == nil || strings.Contains(err.Error(), "/push/abc") {
		t.Fatalf("429 -> %v (must fail without echoing the endpoint)", err)
	}
	if _, err := Encrypt(sub, make([]byte, MaxPayload+1), rand.Reader); err == nil {
		t.Fatal("oversized payload accepted")
	}
}
