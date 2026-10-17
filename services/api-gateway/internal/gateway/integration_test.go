package gateway

// End-to-end auth tests against real Postgres (TB_TEST_PG_SUPER_DSN; skipped when unset) with real
// WebAuthn ceremonies from libs/go/webauthntest. Covers SE-003..SE-011, SE-014, SE-020..SE-025.

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pquerna/otp/totp"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/webauthntest"
)

type fakeCreds struct {
	mu   sync.Mutex
	puts []*brokerv1.PutCredentialRequest
}

func (f *fakeCreds) Put(_ context.Context, r *brokerv1.PutCredentialRequest) (*brokerv1.CredentialStatus, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.puts = append(f.puts, r)
	return &brokerv1.CredentialStatus{BrokerAccountId: r.BrokerAccountId, Broker: r.Broker, Environment: r.Environment, Fields: []string{"api_key_id", "api_secret"}}, nil
}
func (f *fakeCreds) Delete(context.Context, *brokerv1.DeleteCredentialRequest) error { return nil }
func (f *fakeCreds) List(context.Context) ([]*brokerv1.CredentialStatus, error)      { return nil, nil }

type clock struct {
	mu     sync.Mutex
	offset time.Duration
}

func (c *clock) now() time.Time          { c.mu.Lock(); defer c.mu.Unlock(); return time.Now().Add(c.offset) }
func (c *clock) advance(d time.Duration) { c.mu.Lock(); c.offset += d; c.mu.Unlock() }

type harness struct {
	t     *testing.T
	srv   *Server
	ts    *httptest.Server
	base  string
	c     *http.Client
	clock *clock
	creds *fakeCreds
	logs  *bytes.Buffer
	pool  *pgxpool.Pool
}

func newHarness(t *testing.T) *harness {
	super := os.Getenv("TB_TEST_PG_SUPER_DSN")
	if super == "" {
		t.Skip("TB_TEST_PG_SUPER_DSN not set")
	}
	ctx := context.Background()
	const name = "gateway_test"
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
	dsn := db.Config{Host: pc.Host, Port: int(pc.Port), Name: name, User: "svc_api_gateway", SSLMode: "disable"}.DSN(pw["svc_api_gateway"])
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)

	h := &harness{t: t, clock: &clock{}, creds: &fakeCreds{}, logs: &bytes.Buffer{}, pool: pool}
	var srvRef *Server
	h.ts = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { srvRef.Handler(nil).ServeHTTP(w, r) }))
	t.Cleanup(h.ts.Close)
	h.base = strings.Replace(h.ts.URL, "127.0.0.1", "localhost", 1)
	cfg := testConfig(h.base)
	key := bytes.Repeat([]byte{7}, 32)
	srv, err := New(cfg, Deps{Pool: pool, MasterKey: key, Log: slog.New(slog.NewJSONHandler(h.logs, nil)), Creds: h.creds})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = h.clock.now
	srv.rateEvery, srv.rateBurst = time.Millisecond, 1000
	srvRef, h.srv = srv, srv
	jar, _ := cookiejar.New(nil)
	h.c = &http.Client{Jar: jar, Timeout: 10 * time.Second}
	h.do("GET", "/auth/state", nil, 200)
	return h
}

func (h *harness) csrf() string {
	u, _ := url.Parse(h.base)
	for _, c := range h.c.Jar.Cookies(u) {
		if c.Name == "tb_csrf" {
			return c.Value
		}
	}
	return ""
}

func (h *harness) req(method, path string, body any) (int, map[string]any) {
	h.t.Helper()
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	r, _ := http.NewRequest(method, h.base+path, rd)
	r.Header.Set("Origin", h.base)
	r.Header.Set("X-CSRF-Token", h.csrf())
	r.Header.Set("Content-Type", "application/json")
	resp, err := h.c.Do(r)
	if err != nil {
		h.t.Fatal(err)
	}
	defer resp.Body.Close()
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	return resp.StatusCode, out
}

func (h *harness) do(method, path string, body any, want int) map[string]any {
	h.t.Helper()
	code, out := h.req(method, path, body)
	if code != want {
		h.t.Fatalf("%s %s: got %d want %d: %v", method, path, code, want, out)
	}
	return out
}

func (h *harness) totpCode(secret string, at time.Time) string {
	c, err := totp.GenerateCode(secret, at)
	if err != nil {
		h.t.Fatal(err)
	}
	return c
}

func (h *harness) auditActions() []string {
	rows, err := h.pool.Query(context.Background(), `SELECT action FROM audit.events ORDER BY seq`)
	if err != nil {
		h.t.Fatal(err)
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var a string
		_ = rows.Scan(&a)
		out = append(out, a)
	}
	return out
}

func TestSEAuthEndToEnd(t *testing.T) {
	h := newHarness(t)
	ctx := context.Background()
	laptop := webauthntest.New("localhost", h.base)
	phone := webauthntest.New("localhost", h.base)

	// SE-007: setup requires a valid token; registration closes afterwards.
	h.do("POST", "/auth/setup/begin", map[string]string{"setup_token": "nope"}, 401)
	tok, err := h.srv.IssueSetupToken(ctx)
	if err != nil {
		t.Fatal(err)
	}
	begin := h.do("POST", "/auth/setup/begin", map[string]string{"setup_token": tok, "display_name": "Illya"}, 200)
	fin := h.do("POST", "/auth/setup/finish", map[string]any{"setup_token": tok, "ceremony_id": begin["ceremony_id"],
		"display_name": "Illya", "passkey_name": "laptop", "credential": laptop.Create(begin["options"].(map[string]any))}, 200)
	codes := fin["recovery_codes"].([]any)
	if len(codes) != 10 {
		t.Fatalf("SE-009: want 10 recovery codes, got %d", len(codes))
	}
	if _, err := h.srv.IssueSetupToken(ctx); err == nil {
		t.Fatal("SE-007: setup token issued after owner exists")
	}
	me := h.do("GET", "/auth/me", nil, 200)
	if me["session"].(map[string]any)["auth_method"] != "passkey" {
		t.Fatalf("unexpected session: %v", me)
	}

	// Second passkey (fresh step-up from setup) — the owner should register two authenticators.
	rb := h.do("POST", "/auth/webauthn/register/begin", nil, 200)
	h.do("POST", "/auth/webauthn/register/finish", map[string]any{"ceremony_id": rb["ceremony_id"], "name": "phone",
		"credential": phone.Create(rb["options"].(map[string]any))}, 201)

	// SE-008: TOTP enrollment and confirmation.
	enroll := h.do("POST", "/auth/totp/enroll", nil, 200)
	secret := enroll["secret"].(string)
	h.do("POST", "/auth/totp/verify", map[string]string{"code": h.totpCode(secret, h.clock.now())}, 200)

	// Logout ends the session.
	h.do("POST", "/auth/logout", nil, 204)
	h.do("GET", "/auth/me", nil, 401)

	// Passkey login from the phone (discoverable credential).
	lb := h.do("POST", "/auth/webauthn/login/begin", nil, 200)
	h.do("POST", "/auth/webauthn/login/finish", map[string]any{"ceremony_id": lb["ceremony_id"], "credential": phone.Get(lb["options"].(map[string]any))}, 200)
	// Replay of the same ceremony is rejected (single use).
	h.do("POST", "/auth/webauthn/login/finish", map[string]any{"ceremony_id": lb["ceremony_id"], "credential": phone.Get(lb["options"].(map[string]any))}, 400)

	// SE-003: after the step-up window, enabling live trading requires a passkey step-up.
	h.clock.advance(6 * time.Minute)
	out := h.do("PUT", "/api/v1/settings/trading.live_enabled", map[string]any{"value": true, "version": 0}, 403)
	if out["error"] != "step_up_required" || out["method"] != "passkey" {
		t.Fatalf("expected passkey step-up: %v", out)
	}
	// Lowering-risk changes need no step-up.
	h.do("PUT", "/api/v1/settings/ui.theme", map[string]any{"value": "light", "version": 0}, 200)
	sb := h.do("POST", "/auth/step-up/begin", nil, 200)
	h.do("POST", "/auth/step-up/finish", map[string]any{"ceremony_id": sb["ceremony_id"], "credential": laptop.Get(sb["options"].(map[string]any))}, 200)
	h.do("PUT", "/api/v1/settings/trading.live_enabled", map[string]any{"value": true, "version": 0}, 200)
	// SE-025: optimistic concurrency.
	h.do("PUT", "/api/v1/settings/trading.live_enabled", map[string]any{"value": false, "version": 0}, 409)

	// SE-031: credentials are write-only; the plaintext goes to broker-gateway and never back.
	put := h.do("PUT", "/api/v1/broker/credentials/alpaca-paper-1/paper", map[string]any{"broker": "alpaca",
		"secret": map[string]string{"api_key_id": "PKTESTKEYID000000001", "api_secret": "very-secret-value-xyz"}}, 200)
	if strings.Contains(mustJSON(put), "very-secret-value-xyz") || len(h.creds.puts) != 1 {
		t.Fatalf("credential put: %v", put)
	}

	// SE-005: list and revoke sessions.
	sessions := h.do("GET", "/auth/sessions", nil, 200)["sessions"].([]any)
	if len(sessions) != 1 || sessions[0].(map[string]any)["current"] != true {
		t.Fatalf("sessions: %v", sessions)
	}

	// SE-014: a TOTP login cannot satisfy a passkey step-up, even after a TOTP step-up.
	h.do("POST", "/auth/logout", nil, 204)
	h.clock.advance(time.Minute)
	h.do("POST", "/auth/totp/login", map[string]string{"code": h.totpCode(secret, h.clock.now())}, 200)
	h.do("PUT", "/api/v1/settings/trading.live_enabled", map[string]any{"value": true, "version": 1}, 403)
	h.clock.advance(time.Minute)
	h.do("POST", "/auth/step-up/totp", map[string]string{"code": h.totpCode(secret, h.clock.now())}, 200)
	out = h.do("PUT", "/api/v1/settings/trading.live_enabled", map[string]any{"value": true, "version": 1}, 403)
	if out["method"] != "passkey" {
		t.Fatalf("SE-014: %v", out)
	}
	// ...but it does allow account recovery (register a new passkey).
	rb = h.do("POST", "/auth/webauthn/register/begin", nil, 200)
	spare := webauthntest.New("localhost", h.base)
	h.do("POST", "/auth/webauthn/register/finish", map[string]any{"ceremony_id": rb["ceremony_id"], "name": "spare",
		"credential": spare.Create(rb["options"].(map[string]any))}, 201)

	// SE-008: TOTP code replay is rejected.
	h.do("POST", "/auth/logout", nil, 204)
	used := h.totpCode(secret, h.clock.now())
	h.do("POST", "/auth/totp/login", map[string]string{"code": used}, 401)

	// SE-009: recovery codes are single use.
	code := codes[0].(string)
	h.do("POST", "/auth/recovery", map[string]string{"code": code}, 200)
	h.do("POST", "/auth/logout", nil, 204)
	h.do("POST", "/auth/recovery", map[string]string{"code": code}, 401)

	// SE-011: lockout after repeated failures (one failure already counted above).
	for i := 0; i < 4; i++ {
		h.req("POST", "/auth/totp/login", map[string]string{"code": "000000"})
	}
	h.do("POST", "/auth/totp/login", map[string]string{"code": h.totpCode(secret, h.clock.now().Add(30*time.Second))}, 429)

	// SE-023: audited events.
	actions := strings.Join(h.auditActions(), ",")
	for _, want := range []string{"auth.setup.complete", "auth.passkey.register", "auth.totp.enroll", "auth.logout",
		"auth.login.success", "auth.step_up.success", "settings.update", "auth.totp.failure", "auth.recovery.failure", "auth.lockout"} {
		if !strings.Contains(actions, want) {
			t.Errorf("audit missing %s in %s", want, actions)
		}
	}
	// SE-012: no credential, TOTP secret, or recovery code in logs or audit details.
	var details string
	_ = h.pool.QueryRow(ctx, `SELECT string_agg(details_json || target, ' ') FROM audit.events`).Scan(&details)
	for _, leak := range []string{"very-secret-value-xyz", "PKTESTKEYID000000001", secret, code, tok} {
		if strings.Contains(h.logs.String(), leak) || strings.Contains(details, leak) {
			t.Errorf("SE-012: %q leaked into logs or audit", leak)
		}
	}
}

func TestSSEStreamRequiresSessionAndEndsOnRevoke(t *testing.T) {
	h := newHarness(t)
	h.do("GET", "/api/v1/events", nil, 401)
	a := webauthntest.New("localhost", h.base)
	tok, _ := h.srv.IssueSetupToken(context.Background())
	b := h.do("POST", "/auth/setup/begin", map[string]string{"setup_token": tok}, 200)
	h.do("POST", "/auth/setup/finish", map[string]any{"setup_token": tok, "ceremony_id": b["ceremony_id"], "credential": a.Create(b["options"].(map[string]any))}, 200)

	r, _ := http.NewRequest("GET", h.base+"/api/v1/events", nil)
	resp, err := h.c.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("content type %q", resp.Header.Get("Content-Type"))
	}
	sc := bufio.NewScanner(resp.Body)
	var sawStatus bool
	for sc.Scan() {
		line := sc.Text()
		if line == "event: trading-status" && !sawStatus {
			sawStatus = true
			h.do("POST", "/auth/logout", nil, 204)
		}
		if line == "event: session-ended" {
			return
		}
	}
	t.Fatalf("stream ended without session-ended (saw status: %v)", sawStatus)
}

func mustJSON(v any) string { b, _ := json.Marshal(v); return string(b) }
