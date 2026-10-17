package gateway

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"
)

func testConfig(origin string) Config {
	cfg := Config{RPID: "localhost", RPDisplayName: "Strawberry", Origins: []string{origin}, CookieSecure: false,
		SessionIdle: 30 * 60e9, SessionAbsolute: 12 * 3600e9, StepUpWindow: 5 * 60e9, MaxFailures: 5,
		LockoutWindow: 15 * 60e9, OwnerID: "owner"}
	cfg.Service, cfg.Environment = "api-gateway", "test"
	return cfg
}

// SE-004: the public allowlist is exactly this set; every other route rejects unauthenticated requests.
func TestSE004RouteInventory(t *testing.T) {
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	wantPublic := []string{
		"GET /auth/state", "GET /healthz", "POST /auth/recovery", "POST /auth/setup/begin", "POST /auth/setup/finish",
		"POST /auth/totp/login", "POST /auth/webauthn/login/begin", "POST /auth/webauthn/login/finish",
	}
	var gotPublic []string
	h := srv.Handler(nil)
	for _, rt := range srv.Routes() {
		if rt.Public {
			gotPublic = append(gotPublic, rt.Method+" "+rt.Pattern)
			continue
		}
		path := strings.NewReplacer("{id}", "x", "{key}", "ui.theme", "{account}", "acct", "{environment}", "paper").Replace(rt.Pattern)
		req := httptest.NewRequest(rt.Method, path, strings.NewReader("{}"))
		// Valid CSRF so the session check (not CSRF) is what rejects the request.
		req.Header.Set("Origin", "http://localhost:8080")
		req.Header.Set("X-CSRF-Token", strings.Repeat("a", 43))
		req.AddCookie(&http.Cookie{Name: "tb_csrf", Value: strings.Repeat("a", 43)})
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Errorf("%s %s without session: got %d, want 401", rt.Method, rt.Pattern, rec.Code)
		}
	}
	sort.Strings(gotPublic)
	if strings.Join(gotPublic, ",") != strings.Join(wantPublic, ",") {
		t.Fatalf("public allowlist changed:\n got %v\nwant %v", gotPublic, wantPublic)
	}
}

// SE-006 + SE-013: CSRF rejection on state-changing requests and security headers on every response.
func TestSE006CSRFAndSE013Headers(t *testing.T) {
	srv, _ := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	h := srv.Handler(nil)
	cases := map[string]func(r *http.Request){
		"no origin":      func(r *http.Request) {},
		"foreign origin": func(r *http.Request) { r.Header.Set("Origin", "https://evil.example") },
		"no token": func(r *http.Request) {
			r.Header.Set("Origin", "http://localhost:8080")
			r.AddCookie(&http.Cookie{Name: "tb_csrf", Value: strings.Repeat("a", 43)})
		},
		"mismatched token": func(r *http.Request) {
			r.Header.Set("Origin", "http://localhost:8080")
			r.Header.Set("X-CSRF-Token", strings.Repeat("b", 43))
			r.AddCookie(&http.Cookie{Name: "tb_csrf", Value: strings.Repeat("a", 43)})
		},
	}
	for name, mut := range cases {
		req := httptest.NewRequest("POST", "/auth/webauthn/login/begin", nil)
		mut(req)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusForbidden {
			t.Errorf("%s: got %d want 403", name, rec.Code)
		}
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/healthz", nil))
	for hdr, want := range map[string]string{
		"Content-Security-Policy": "frame-ancestors 'none'", "X-Content-Type-Options": "nosniff",
		"Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=()",
	} {
		if !strings.Contains(rec.Header().Get(hdr), want) {
			t.Errorf("header %s = %q, want contains %q", hdr, rec.Header().Get(hdr), want)
		}
	}
	if !strings.Contains(rec.Header().Get("Set-Cookie"), "tb_csrf=") || !strings.Contains(rec.Header().Get("Set-Cookie"), "SameSite=Strict") {
		t.Errorf("CSRF cookie not issued: %q", rec.Header().Get("Set-Cookie"))
	}
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/api/v1/nope", nil))
	if rec.Code != http.StatusNotFound {
		t.Errorf("unknown API path: %d", rec.Code)
	}
}
