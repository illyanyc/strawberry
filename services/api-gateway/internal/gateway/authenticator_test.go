package gateway

// A minimal software WebAuthn authenticator (ES256, "none" attestation, UV) so tests drive real
// registration and assertion ceremonies through go-webauthn, exactly as a browser passkey would.

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"testing"

	"github.com/fxamacker/cbor/v2"
)

type softAuthenticator struct {
	t          *testing.T
	rpID       string
	origin     string
	key        *ecdsa.PrivateKey
	credID     []byte
	userHandle []byte
	signCount  uint32
}

func newSoftAuthenticator(t *testing.T, rpID, origin string) *softAuthenticator {
	k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	id := make([]byte, 32)
	_, _ = rand.Read(id)
	return &softAuthenticator{t: t, rpID: rpID, origin: origin, key: k, credID: id}
}

var b64 = base64.RawURLEncoding

func (a *softAuthenticator) clientData(typ, challenge string) []byte {
	b, _ := json.Marshal(map[string]any{"type": typ, "challenge": challenge, "origin": a.origin, "crossOrigin": false})
	return b
}

func (a *softAuthenticator) authData(flags byte, attested []byte) []byte {
	rp := sha256.Sum256([]byte(a.rpID))
	out := append([]byte{}, rp[:]...)
	out = append(out, flags)
	cnt := make([]byte, 4)
	binary.BigEndian.PutUint32(cnt, a.signCount)
	out = append(out, cnt...)
	return append(out, attested...)
}

// create answers PublicKeyCredentialCreationOptions JSON.
func (a *softAuthenticator) create(options map[string]any) json.RawMessage {
	pk := options
	user := pk["user"].(map[string]any)
	handle, err := b64.DecodeString(user["id"].(string))
	if err != nil {
		a.t.Fatal(err)
	}
	a.userHandle = handle
	x := a.key.X.FillBytes(make([]byte, 32))
	y := a.key.Y.FillBytes(make([]byte, 32))
	cose, _ := cbor.Marshal(map[int]any{1: 2, 3: -7, -1: 1, -2: x, -3: y})
	attested := make([]byte, 16) // AAGUID zero
	l := make([]byte, 2)
	binary.BigEndian.PutUint16(l, uint16(len(a.credID)))
	attested = append(attested, l...)
	attested = append(attested, a.credID...)
	attested = append(attested, cose...)
	ad := a.authData(0x45, attested) // UP | UV | AT
	att, _ := cbor.Marshal(map[string]any{"fmt": "none", "attStmt": map[string]any{}, "authData": ad})
	cd := a.clientData("webauthn.create", pk["challenge"].(string))
	out, _ := json.Marshal(map[string]any{
		"id": b64.EncodeToString(a.credID), "rawId": b64.EncodeToString(a.credID), "type": "public-key",
		"authenticatorAttachment": "platform", "clientExtensionResults": map[string]any{},
		"response": map[string]any{"clientDataJSON": b64.EncodeToString(cd), "attestationObject": b64.EncodeToString(att), "transports": []string{"internal"}},
	})
	return out
}

// get answers PublicKeyCredentialRequestOptions.
func (a *softAuthenticator) get(options map[string]any) json.RawMessage {
	pk := options
	a.signCount++
	ad := a.authData(0x05, nil) // UP | UV
	cd := a.clientData("webauthn.get", pk["challenge"].(string))
	h := sha256.Sum256(cd)
	digest := sha256.Sum256(append(append([]byte{}, ad...), h[:]...))
	sig, err := ecdsa.SignASN1(rand.Reader, a.key, digest[:])
	if err != nil {
		a.t.Fatal(err)
	}
	out, _ := json.Marshal(map[string]any{
		"id": b64.EncodeToString(a.credID), "rawId": b64.EncodeToString(a.credID), "type": "public-key",
		"authenticatorAttachment": "platform", "clientExtensionResults": map[string]any{},
		"response": map[string]any{"clientDataJSON": b64.EncodeToString(cd), "authenticatorData": b64.EncodeToString(ad),
			"signature": b64.EncodeToString(sig), "userHandle": b64.EncodeToString(a.userHandle)},
	})
	return out
}
