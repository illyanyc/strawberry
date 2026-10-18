package gateway

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/illyanyc/strawberry/libs/go/audit"
)

func keyServer(t *testing.T) *Server {
	t.Helper()
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	srv.appendAudit = func(context.Context, audit.Event) (int64, string, error) { return 1, "h", nil }
	return srv
}

func stepped(owner string) *reqCtx {
	until := time.Now().Add(time.Minute)
	return &reqCtx{session: &Session{OwnerID: owner, StepUpMethod: "passkey", StepUpUntil: &until}}
}

func (s *Server) keyCall(method, path, body string, rc *reqCtx) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if strings.Contains(path, "/fmp") {
		req.SetPathValue("kind", "fmp")
	}
	rec := httptest.NewRecorder()
	switch {
	case method == http.MethodPut:
		s.handleKeyPut(rec, req, rc)
	case method == http.MethodDelete:
		s.handleKeyDelete(rec, req, rc)
	default:
		s.handleKeysList(rec, req, rc)
	}
	return rec
}

func TestSE067FMPKeyIsWriteOnlyAndPaperOnly(t *testing.T) {
	srv := keyServer(t)
	secret := "super-secret-value"
	plain := stepped("owner")
	if rec := srv.keyCall(http.MethodPut, "/api/v1/keys/fmp", `{"key_id":"fmp_key_1","secret":"`+secret+`"}`, &reqCtx{session: &Session{OwnerID: "owner"}}); rec.Code != http.StatusForbidden {
		t.Fatalf("without step-up: %d %s", rec.Code, rec.Body)
	}
	rec := srv.keyCall(http.MethodPut, "/api/v1/keys/fmp", `{"key_id":"fmp_key_1","secret":"`+secret+`"}`, plain)
	if rec.Code != http.StatusOK {
		t.Fatalf("put: %d %s", rec.Code, rec.Body)
	}
	if strings.Contains(rec.Body.String(), secret) || strings.Contains(rec.Body.String(), "fmp_key_1") {
		t.Fatalf("response leaked key material: %s", rec.Body)
	}
	if !strings.Contains(rec.Body.String(), "ey_1") {
		t.Fatalf("missing hint: %s", rec.Body)
	}
	other := srv.keyCall(http.MethodGet, "/api/v1/keys", "", stepped("other"))
	if other.Code != http.StatusOK || strings.Contains(other.Body.String(), "ey_1") {
		t.Fatalf("other owner saw the key: %d %s", other.Code, other.Body)
	}
	listed := srv.keyCall(http.MethodGet, "/api/v1/keys", "", plain)
	if strings.Contains(listed.Body.String(), secret) {
		t.Fatalf("list leaked secret: %s", listed.Body)
	}
	bad := srv.keyCall(http.MethodPut, "/api/v1/keys/alpaca", `{"key_id":"abcd","secret":"12345678","environment":"live"}`, plain)
	if bad.Code != http.StatusBadRequest {
		t.Fatalf("live/other kind: %d %s", bad.Code, bad.Body)
	}
	gone := srv.keyCall(http.MethodDelete, "/api/v1/keys/fmp", "", plain)
	if gone.Code != http.StatusNoContent {
		t.Fatalf("delete: %d %s", gone.Code, gone.Body)
	}
}

func TestUI055DisclosureAcceptsOnlyTrue(t *testing.T) {
	srv := keyServer(t)
	rc := &reqCtx{session: &Session{OwnerID: "owner"}}
	req := httptest.NewRequest(http.MethodPost, "/api/v1/risk-disclosure", strings.NewReader(`{"accepted":false}`))
	rec := httptest.NewRecorder()
	srv.handleDisclosurePost(rec, req, rc)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("false: %d", rec.Code)
	}
	req = httptest.NewRequest(http.MethodPost, "/api/v1/risk-disclosure", strings.NewReader(`{"accepted":true}`))
	rec = httptest.NewRecorder()
	srv.handleDisclosurePost(rec, req, rc)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"accepted":true`) {
		t.Fatalf("accept: %d %s", rec.Code, rec.Body)
	}
}
