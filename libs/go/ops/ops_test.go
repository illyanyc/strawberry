package ops_test

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/illyanyc/strawberry/libs/go/ops"
)

func TestHealthReadyMetrics(t *testing.T) {
	s := ops.NewServer("svc", slog.New(slog.NewTextHandler(io.Discard, nil)))
	healthy := true
	s.AddCheck("db", func(context.Context) error {
		if healthy {
			return nil
		}
		return errors.New("db down: password=hunter2hunter2")
	})
	ts := httptest.NewServer(s.Handler())
	defer ts.Close()

	get := func(path string) (int, string) {
		resp, err := http.Get(ts.URL + path) //nolint:noctx
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(b)
	}
	if code, _ := get("/healthz"); code != 200 {
		t.Fatalf("healthz %d", code)
	}
	if code, _ := get("/readyz"); code != 200 {
		t.Fatalf("readyz %d", code)
	}
	healthy = false
	code, body := get("/readyz")
	if code != 503 || strings.Contains(body, "hunter2") {
		t.Fatalf("readyz should fail without leaking details: %d %s", code, body)
	}
	if code, body := get("/metrics"); code != 200 || !strings.Contains(body, "tb_build_info") {
		t.Fatalf("metrics %d", code)
	}
}
