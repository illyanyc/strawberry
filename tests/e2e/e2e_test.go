// Package e2e runs end-to-end scenarios against a running stack (`make up`, then `make e2e`). It needs a
// fresh stack (no owner yet) and a setup token in TB_E2E_SETUP_TOKEN; `scripts/smoke.sh` provides both.
package e2e

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/illyanyc/strawberry/libs/go/webauthntest"
)

type client struct {
	t       *testing.T
	base    string
	http    *http.Client
	cookies map[string]string
}

func newClient(t *testing.T) *client {
	base := os.Getenv("TB_E2E_BASE_URL")
	if base == "" {
		t.Skip("TB_E2E_BASE_URL not set; run via `make e2e` or `make smoke` against a running stack")
	}
	return &client{t: t, base: base, http: &http.Client{Timeout: 20 * time.Second}, cookies: map[string]string{}}
}

// Cookies are managed by hand: the gateway sets __Host- Secure cookies, which browsers (but not Go's
// cookie jar) accept on http://localhost.
func (c *client) do(method, path string, body any, want int) map[string]any {
	c.t.Helper()
	var rd io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, c.base+path, rd)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Origin", c.base)
	for k, v := range c.cookies {
		req.AddCookie(&http.Cookie{Name: k, Value: v})
		if strings.HasSuffix(k, "tb_csrf") {
			req.Header.Set("X-CSRF-Token", v)
		}
	}
	resp, err := c.http.Do(req)
	if err != nil {
		c.t.Fatal(err)
	}
	defer resp.Body.Close()
	for _, ck := range resp.Cookies() {
		if ck.MaxAge < 0 || ck.Value == "" {
			delete(c.cookies, ck.Name)
		} else {
			c.cookies[ck.Name] = ck.Value
		}
	}
	out := map[string]any{}
	_ = json.NewDecoder(resp.Body).Decode(&out)
	if resp.StatusCode != want {
		c.t.Fatalf("%s %s: got %d want %d: %v", method, path, resp.StatusCode, want, out)
	}
	return out
}

// TestPhase0Gate walks the Phase 0 gate over the real stack: unauthenticated rejection, passkey setup,
// a second passkey, logout/login, step-up-guarded credential storage with a leak canary, SSE, and the
// audit chain.
func TestPhase0Gate(t *testing.T) {
	c := newClient(t)
	state := c.do("GET", "/auth/state", nil, 200)
	if state["setup_required"] != true {
		t.Skip("stack already has an owner; run against a fresh stack (make clean && make up)")
	}
	token := os.Getenv("TB_E2E_SETUP_TOKEN")
	if token == "" {
		t.Fatal("TB_E2E_SETUP_TOKEN is required (make setup-token)")
	}
	canary := os.Getenv("TB_E2E_CANARY")
	if canary == "" {
		canary = "e2e-canary-" + time.Now().Format("150405.000000")
	}

	// SE-004: protected routes reject anonymous requests.
	for _, p := range []string{"/auth/me", "/api/v1/settings", "/api/v1/audit", "/api/v1/broker/credentials", "/api/v1/events"} {
		c.do("GET", p, nil, 401)
	}

	laptop := webauthntest.New("localhost", c.base)
	phone := webauthntest.New("localhost", c.base)
	begin := c.do("POST", "/auth/setup/begin", map[string]string{"setup_token": token, "display_name": "E2E Owner"}, 200)
	fin := c.do("POST", "/auth/setup/finish", map[string]any{"setup_token": token, "ceremony_id": begin["ceremony_id"],
		"display_name": "E2E Owner", "passkey_name": "laptop", "credential": laptop.Create(begin["options"].(map[string]any))}, 200)
	if n := len(fin["recovery_codes"].([]any)); n != 10 {
		t.Fatalf("want 10 recovery codes, got %d", n)
	}
	rb := c.do("POST", "/auth/webauthn/register/begin", nil, 200)
	c.do("POST", "/auth/webauthn/register/finish", map[string]any{"ceremony_id": rb["ceremony_id"], "name": "phone",
		"credential": phone.Create(rb["options"].(map[string]any))}, 201)

	// SE-030/SE-031: store a paper credential through api-gateway -> broker-gateway (gRPC + service token).
	st := c.do("PUT", "/api/v1/broker/credentials/alpaca-paper-e2e/paper", map[string]any{"broker": "alpaca",
		"secret": map[string]string{"api_key_id": "PKE2E000000000000000", "api_secret": canary}}, 200)
	if strings.Contains(mustJSON(st), canary) {
		t.Fatal("credential echoed back")
	}
	list := c.do("GET", "/api/v1/broker/credentials", nil, 200)
	if !strings.Contains(mustJSON(list), "alpaca-paper-e2e") || strings.Contains(mustJSON(list), canary) {
		t.Fatalf("credential status: %v", list)
	}

	c.do("POST", "/auth/logout", nil, 204)
	c.do("GET", "/auth/me", nil, 401)
	lb := c.do("POST", "/auth/webauthn/login/begin", nil, 200)
	c.do("POST", "/auth/webauthn/login/finish", map[string]any{"ceremony_id": lb["ceremony_id"], "credential": phone.Get(lb["options"].(map[string]any))}, 200)
	if me := c.do("GET", "/auth/me", nil, 200); len(me["passkeys"].([]any)) != 2 {
		t.Fatalf("expected two passkeys: %v", me)
	}

	// Settings change audited (SE-025).
	c.do("PUT", "/api/v1/settings/ui.theme", map[string]any{"value": "light", "version": 0}, 200)

	// SSE stream delivers a trading-status event (D14).
	req, _ := http.NewRequest("GET", c.base+"/api/v1/events", nil)
	for k, v := range c.cookies {
		req.AddCookie(&http.Cookie{Name: k, Value: v})
	}
	resp, err := c.http.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	sc := bufio.NewScanner(resp.Body)
	got := false
	for i := 0; i < 10 && sc.Scan(); i++ {
		if sc.Text() == "event: trading-status" {
			got = true
			break
		}
	}
	resp.Body.Close()
	if !got {
		t.Fatal("no trading-status SSE event")
	}

	// SE-022: chain verifies and covers the flow.
	v := c.do("GET", "/api/v1/audit/verify", nil, 200)
	if v["ok"] != true || v["count"].(float64) < 6 {
		t.Fatalf("audit verify: %v", v)
	}
	ev := c.do("GET", "/api/v1/audit?limit=100", nil, 200)
	text := mustJSON(ev)
	for _, a := range []string{"auth.setup.complete", "auth.passkey.register", "broker.credentials.put", "auth.logout", "auth.login.success", "settings.update"} {
		if !strings.Contains(text, a) {
			t.Errorf("audit missing %s", a)
		}
	}
	if strings.Contains(text, canary) {
		t.Fatal("canary leaked into the audit log")
	}
	t.Logf("gate flow ok; audit head seq=%v", v["head_seq"])
}

func mustJSON(v any) string { b, _ := json.Marshal(v); return string(b) }
