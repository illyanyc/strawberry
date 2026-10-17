package credstore

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/db"
)

func ring(t *testing.T, spec string) *KeyRing {
	t.Helper()
	kr, err := ParseKeyRing([]byte(spec))
	if err != nil {
		t.Fatal(err)
	}
	return kr
}

func key(b byte) string { return base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{b}, 32)) }

func TestParseKeyRing(t *testing.T) {
	kr := ring(t, "# comment\nk2:"+key(2)+"\nk1:"+key(1)+"\n")
	if kr.Active != "k2" || len(kr.keys) != 2 {
		t.Fatalf("%+v", kr)
	}
	for _, bad := range []string{"", "k1", "k1:abc", "k1:" + key(1) + "\nk1:" + key(2)} {
		if _, err := ParseKeyRing([]byte(bad)); err == nil {
			t.Fatalf("expected error for %q", bad)
		}
	}
}

// SE-030: AAD binds account and environment; sealing round-trips; a paper record never opens as live.
func TestSE030EnvelopeAADBinding(t *testing.T) {
	s := &Store{ring: ring(t, "k1:"+key(1))}
	secret := map[string]string{"api_key_id": "PKAAAAAAAAAAAAAAAAAA", "api_secret": "s3cret-value-123"}
	x, err := s.seal("alpaca-1", Paper, secret)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(x.ciphertext, []byte("s3cret-value-123")) {
		t.Fatal("plaintext visible in ciphertext")
	}
	got, err := s.open("alpaca-1", Paper, x)
	if err != nil || got["api_secret"] != "s3cret-value-123" {
		t.Fatalf("round trip: %v %v", got, err)
	}
	if _, err := s.open("alpaca-1", Live, x); !errors.Is(err, ErrDecrypt) {
		t.Fatalf("paper credential opened as live: %v", err)
	}
	if _, err := s.open("alpaca-2", Paper, x); !errors.Is(err, ErrDecrypt) {
		t.Fatalf("credential opened under another account: %v", err)
	}
	other := &Store{ring: ring(t, "k1:"+key(9))}
	if _, err := other.open("alpaca-1", Paper, x); !errors.Is(err, ErrDecrypt) {
		t.Fatalf("opened with the wrong KEK: %v", err)
	}
}

func testPool(t *testing.T) (*pgxpool.Pool, *pgx.Conn) {
	super := os.Getenv("TB_TEST_PG_SUPER_DSN")
	if super == "" {
		t.Skip("TB_TEST_PG_SUPER_DSN not set")
	}
	ctx := context.Background()
	const name = "credstore_test"
	admin, err := pgx.Connect(ctx, super)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = admin.Exec(ctx, "DROP DATABASE IF EXISTS "+name+" WITH (FORCE)")
	_ = admin.Close(ctx)
	pw := map[string]string{}
	for _, r := range append(append([]string{}, db.LoginRoles...), db.AppDatabases...) {
		pw[r] = "pw-" + r
	}
	if err := db.Bootstrap(ctx, super, name, pw); err != nil {
		t.Fatal(err)
	}
	pc, _ := pgx.ParseConfig(super)
	pool, err := pgxpool.New(ctx, db.Config{Host: pc.Host, Port: int(pc.Port), Name: name, User: "svc_broker_gateway", SSLMode: "disable"}.DSN(pw["svc_broker_gateway"]))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	pc.Database = name
	su, err := pgx.ConnectConfig(ctx, pc)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = su.Close(ctx) })
	return pool, su
}

// SE-030..SE-033 against Postgres: storage holds only ciphertext, a row moved from paper to live does
// not decrypt, KEK rotation re-wraps without touching ciphertext, and audit details carry no secrets.
func TestSE030StoreIntegration(t *testing.T) {
	pool, su := testPool(t)
	ctx := context.Background()
	s := New(pool, ring(t, "k1:"+key(1)))
	secret := map[string]string{"api_key_id": "PKBBBBBBBBBBBBBBBBBB", "api_secret": "integration-secret-xyz"}
	ev := audit.Event{Actor: "owner", Action: "broker.credentials.put", Target: "alpaca-paper-1"}
	st, err := s.Put(ctx, "owner", "alpaca", "alpaca-paper-1", Paper, secret, ev)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(st.Fields, ",") != "api_key_id,api_secret" || st.KEKID != "k1" {
		t.Fatalf("status: %+v", st)
	}
	got, err := s.Get(ctx, "alpaca-paper-1", Paper)
	if err != nil || got["api_secret"] != "integration-secret-xyz" {
		t.Fatalf("get: %v %v", got, err)
	}
	var dump string
	if err := su.QueryRow(ctx, `SELECT string_agg(encode(ciphertext,'escape') || encode(wrapped_dek,'escape') || array_to_string(fields, ','), '') FROM secrets.broker_credentials`).Scan(&dump); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(dump, "integration-secret-xyz") || strings.Contains(dump, "PKBBBBBBBBBBBBBBBBBB") {
		t.Fatal("plaintext stored")
	}
	var details string
	_ = su.QueryRow(ctx, `SELECT string_agg(details_json, ' ') FROM audit.events WHERE action='broker.credentials.put'`).Scan(&details)
	if details == "" || strings.Contains(details, "integration-secret-xyz") {
		t.Fatalf("audit details: %q", details)
	}

	// An attacker with DB write access relabels the paper row as live: decryption must fail.
	if _, err := su.Exec(ctx, `UPDATE secrets.broker_credentials SET environment='live' WHERE broker_account_id='alpaca-paper-1'`); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Get(ctx, "alpaca-paper-1", Live); !errors.Is(err, ErrDecrypt) {
		t.Fatalf("relabelled paper credential decrypted as live: %v", err)
	}
	_, _ = su.Exec(ctx, `UPDATE secrets.broker_credentials SET environment='paper' WHERE broker_account_id='alpaca-paper-1'`)

	// KEK rotation: new active key k2, old key retained to unwrap.
	s2 := New(pool, ring(t, "k2:"+key(2)+"\nk1:"+key(1)))
	if n, err := s2.RewrapAll(ctx); err != nil || n != 1 {
		t.Fatalf("rewrap: %d %v", n, err)
	}
	only2 := New(pool, ring(t, "k2:"+key(2)))
	if got, err := only2.Get(ctx, "alpaca-paper-1", Paper); err != nil || got["api_key_id"] != "PKBBBBBBBBBBBBBBBBBB" {
		t.Fatalf("after rewrap: %v %v", got, err)
	}
	if err := s.Delete(ctx, "alpaca-paper-1", Paper, audit.Event{Actor: "owner", Action: "broker.credentials.delete"}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Get(ctx, "alpaca-paper-1", Paper); !errors.Is(err, ErrNotFound) {
		t.Fatalf("after delete: %v", err)
	}
}
