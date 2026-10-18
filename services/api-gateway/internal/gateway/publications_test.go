package gateway

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/illyanyc/strawberry/libs/go/audit"
)

func publicationServer(t *testing.T) *Server {
	t.Helper()
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil))})
	if err != nil {
		t.Fatal(err)
	}
	srv.appendAudit = func(context.Context, audit.Event) (int64, string, error) { return 1, "h", nil }
	return srv
}

func publicationBody(extra string) string {
	body := `{
		"display_name": "Ada",
		"title": "Opening range",
		"definition": "Buy the open when the first five-minute range breaks.",
		"window_start": "2020-01-02",
		"window_end": "2024-12-31",
		"bar_size": "5m",
		"universe": "Point-in-time large-cap names, about 500"
	}`
	if extra == "" {
		return body
	}
	return strings.TrimRight(strings.TrimSpace(body), "}") + "," + extra + "}"
}

func (s *Server) publicationCall(method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	if id := strings.TrimPrefix(req.URL.Path, "/api/v1/publications/"); id != req.URL.Path && id != "" {
		req.SetPathValue("id", id)
	}
	rec := httptest.NewRecorder()
	rc := &reqCtx{session: &Session{OwnerID: "owner"}}
	switch {
	case method == http.MethodPost:
		s.handlePublicationCreate(rec, req, rc)
	case method == http.MethodDelete:
		s.handlePublicationWithdraw(rec, req, rc)
	case strings.Contains(path, "/api/v1/publications/") && method == http.MethodGet:
		s.handlePublicationGet(rec, req, rc)
	default:
		s.handlePublicationsList(rec, req, rc)
	}
	return rec
}

// UI-052: performance fields and result sorts are rejected; a stored entry has no profit,
// return, or rank, and the word "buy" inside a rules description is still just text.
func TestUI052PublicationsRejectPerformanceAndBadSort(t *testing.T) {
	srv := publicationServer(t)
	for _, extra := range []string{
		`"pnl": "1000"`,
		`"sharpe": 1.2`,
		`"return_pct": 12`,
		`"rank": 1`,
		`"drawdown": 0.2`,
		`"win_rate": 0.6`,
		`"signal": "buy"`,
		`"order": {"side": "buy"}`,
	} {
		rec := srv.publicationCall(http.MethodPost, "/api/v1/publications", publicationBody(extra))
		if rec.Code != http.StatusBadRequest {
			t.Errorf("extra %s: got %d %s", extra, rec.Code, rec.Body)
		}
	}
	rec := srv.publicationCall(http.MethodGet, "/api/v1/publications?sort=return", "")
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("sort=return: %d %s", rec.Code, rec.Body)
	}
	rec = srv.publicationCall(http.MethodGet, "/api/v1/publications?sort=pnl", "")
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("sort=pnl: %d", rec.Code)
	}

	rec = srv.publicationCall(http.MethodPost, "/api/v1/publications", publicationBody(""))
	if rec.Code != http.StatusCreated {
		t.Fatalf("create: %d %s", rec.Code, rec.Body)
	}
	var created struct {
		Notice      string         `json:"notice"`
		Publication map[string]any `json:"publication"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if created.Notice != publicationNotice || created.Publication["label"] != publicationLabel {
		t.Fatalf("notice/label: %+v", created)
	}
	for _, banned := range []string{"pnl", "sharpe", "return", "return_pct", "rank", "drawdown", "win_rate", "profit", "score"} {
		if _, ok := created.Publication[banned]; ok {
			t.Fatalf("response has %s", banned)
		}
	}
	if !strings.Contains(created.Publication["definition"].(string), "Buy") {
		t.Fatalf("definition dropped the rules text: %v", created.Publication["definition"])
	}
	id, _ := created.Publication["id"].(string)

	later := publicationBody("")
	later = strings.Replace(later, "Opening range", "Zebra rules", 1)
	rec = srv.publicationCall(http.MethodPost, "/api/v1/publications", later)
	if rec.Code != http.StatusCreated {
		t.Fatalf("second create: %d %s", rec.Code, rec.Body)
	}

	rec = srv.publicationCall(http.MethodGet, "/api/v1/publications", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("list: %d %s", rec.Code, rec.Body)
	}
	var list struct {
		Sort         string           `json:"sort"`
		Publications []map[string]any `json:"publications"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil {
		t.Fatal(err)
	}
	if list.Sort != "published_at" || len(list.Publications) != 2 {
		t.Fatalf("list: %+v", list)
	}
	if list.Publications[0]["title"] != "Zebra rules" {
		t.Fatalf("newest first: %v", list.Publications[0]["title"])
	}
	raw := rec.Body.String()
	for _, banned := range []string{`"pnl"`, `"sharpe"`, `"drawdown"`, `"win_rate"`, `"rank"`, `"return_pct"`} {
		if strings.Contains(raw, banned) {
			t.Fatalf("list contains %s: %s", banned, raw)
		}
	}

	rec = srv.publicationCall(http.MethodGet, "/api/v1/publications?sort=name", "")
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil {
		t.Fatal(err)
	}
	if list.Sort != "name" || list.Publications[0]["title"] != "Opening range" {
		t.Fatalf("name order: %+v", list.Publications)
	}

	rec = srv.publicationCall(http.MethodGet, "/api/v1/publications/"+id, "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"label":"user_content"`) {
		t.Fatalf("get: %d %s", rec.Code, rec.Body)
	}
	rec = srv.publicationCall(http.MethodDelete, "/api/v1/publications/"+id, "")
	if rec.Code != http.StatusNoContent {
		t.Fatalf("withdraw: %d %s", rec.Code, rec.Body)
	}
	rec = srv.publicationCall(http.MethodGet, "/api/v1/publications/"+id, "")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("withdrawn still visible: %d", rec.Code)
	}

	for _, rt := range srv.Routes() {
		if strings.Contains(rt.Pattern, "/publications") && (strings.Contains(rt.Pattern, "deploy") || strings.Contains(rt.Pattern, "order") || strings.Contains(rt.Pattern, "copy")) {
			t.Fatalf("publication route looks like an order path: %s %s", rt.Method, rt.Pattern)
		}
	}
}
