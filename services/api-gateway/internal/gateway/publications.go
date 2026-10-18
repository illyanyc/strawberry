package gateway

import (
	"context"
	"errors"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/illyanyc/strawberry/libs/go/policy"
)

// UI-053: a publication is a strategy definition plus the non-performance parameters of a
// hypothetical backtest. The index is ordered by publication time or title. It is not a
// leaderboard: there is no profit, return, rank, or signal, and no path from here to an order.
// A cross-user "money made" board, and public return statistics, are refused.

const (
	publicationLabel   = "user_content"
	publicationNotice  = "Each entry is user content. It is not advice from Adwise Partners LLC, not a live signal, and not an instruction to trade."
	publicationCap     = 50
	publicationMaxSpan = 40 * 366 * 24 * time.Hour
)

var (
	errPublicationLimit = errors.New("publication limit")
	displayNameRE       = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$`)
	publicationBars     = map[string]bool{"1m": true, "5m": true, "15m": true, "30m": true, "1h": true, "1d": true}
)

// publication is one active or withdrawn index entry. It has no performance fields (UI-053).
type publication struct {
	ID          string
	OwnerID     string
	DisplayName string
	Title       string
	Definition  string
	WindowStart string
	WindowEnd   string
	BarSize     string
	Universe    string
	PublishedAt time.Time
}

type publicationRequest struct {
	DisplayName string `json:"display_name"`
	Title       string `json:"title"`
	Definition  string `json:"definition"`
	WindowStart string `json:"window_start"`
	WindowEnd   string `json:"window_end"`
	BarSize     string `json:"bar_size"`
	Universe    string `json:"universe"`
}

// publicationView is the only JSON shape a publication is allowed to take (UI-053).
type publicationView struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Title       string `json:"title"`
	Definition  string `json:"definition"`
	WindowStart string `json:"window_start"`
	WindowEnd   string `json:"window_end"`
	BarSize     string `json:"bar_size"`
	Universe    string `json:"universe"`
	PublishedAt string `json:"published_at"`
	Label       string `json:"label"`
	Owned       bool   `json:"owned"`
}

type publicationRepo interface {
	insertPublication(ctx context.Context, p publication) error
	listPublications(ctx context.Context, sortBy string) ([]publication, error)
	publicationByID(ctx context.Context, id string) (publication, error)
	withdrawPublication(ctx context.Context, id, ownerID string) error
}

func (s *Server) handlePublicationsList(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActResearchRead, policy.Resource{}) {
		return
	}
	sortBy, ok := publicationSort(r.URL.Query().Get("sort"))
	if !ok {
		// UI-053: the index is not ranked by results.
		writeError(w, http.StatusBadRequest, "bad_sort", "sort must be published_at or name")
		return
	}
	rows, err := s.pubs.listPublications(r.Context(), sortBy)
	if err != nil {
		s.internalError(w, r, "publications", err)
		return
	}
	out := make([]publicationView, 0, len(rows))
	for _, p := range rows {
		out = append(out, p.view(rc.session.OwnerID))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"sort":         sortBy,
		"notice":       publicationNotice,
		"publications": out,
	})
}

func (s *Server) handlePublicationGet(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActResearchRead, policy.Resource{}) {
		return
	}
	p, err := s.pubs.publicationByID(r.Context(), r.PathValue("id"))
	if errors.Is(err, errNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "publication not found")
		return
	}
	if err != nil {
		s.internalError(w, r, "publication", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"notice": publicationNotice, "publication": p.view(rc.session.OwnerID)})
}

func (s *Server) handlePublicationCreate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActResearchLaunch, policy.Resource{}) {
		return
	}
	var req publicationRequest
	// DisallowUnknownFields rejects profit, return, rank, signal, and order fields (UI-053).
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid publication")
		return
	}
	p, msg := normalizePublication(req, rc.session.OwnerID, s.now())
	if msg != "" {
		writeError(w, http.StatusBadRequest, "bad_request", msg)
		return
	}
	p.ID = newID()
	if err := s.pubs.insertPublication(r.Context(), p); err != nil {
		if errors.Is(err, errPublicationLimit) {
			writeError(w, http.StatusBadRequest, "publication_limit", "too many publications")
			return
		}
		s.internalError(w, r, "publication insert", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "publication.create", p.ID, map[string]any{
		"title": p.Title, "bar_size": p.BarSize, "window_start": p.WindowStart, "window_end": p.WindowEnd,
	}, "", p.ID); err != nil {
		_ = s.pubs.withdrawPublication(r.Context(), p.ID, p.OwnerID)
		s.internalError(w, r, "publication audit", err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"notice": publicationNotice, "publication": p.view(rc.session.OwnerID)})
}

func (s *Server) handlePublicationWithdraw(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActResearchLaunch, policy.Resource{}) {
		return
	}
	id := r.PathValue("id")
	err := s.pubs.withdrawPublication(r.Context(), id, rc.session.OwnerID)
	if errors.Is(err, errNotFound) {
		writeError(w, http.StatusNotFound, "not_found", "publication not found")
		return
	}
	if err != nil {
		s.internalError(w, r, "publication withdraw", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "publication.withdraw", id, nil, id, ""); err != nil {
		s.internalError(w, r, "publication audit", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (p publication) view(reader string) publicationView {
	return publicationView{
		ID: p.ID, DisplayName: p.DisplayName, Title: p.Title, Definition: p.Definition,
		WindowStart: p.WindowStart, WindowEnd: p.WindowEnd, BarSize: p.BarSize, Universe: p.Universe,
		PublishedAt: p.PublishedAt.UTC().Format(time.RFC3339), Label: publicationLabel, Owned: p.OwnerID == reader,
	}
}

// publicationSort accepts only time or name. Any result-based sort is rejected (UI-053).
func publicationSort(v string) (string, bool) {
	switch v {
	case "", "published_at":
		return "published_at", true
	case "name":
		return "name", true
	default:
		return "", false
	}
}

func normalizePublication(req publicationRequest, ownerID string, now time.Time) (publication, string) {
	name, ok := cleanText(req.DisplayName, false)
	if !ok || !displayNameRE.MatchString(name) {
		return publication{}, "display name must be 1-40 letters, digits, spaces, or . _ -"
	}
	title, ok := cleanText(req.Title, false)
	if !ok || title == "" || utf8.RuneCountInString(title) > 120 {
		return publication{}, "title must be 1-120 characters"
	}
	def, ok := cleanText(req.Definition, true)
	if !ok || def == "" || utf8.RuneCountInString(def) > 8000 {
		return publication{}, "definition must be 1-8000 characters"
	}
	universe, ok := cleanText(req.Universe, false)
	if !ok || universe == "" || utf8.RuneCountInString(universe) > 240 {
		return publication{}, "universe description must be 1-240 characters"
	}
	if !publicationBars[req.BarSize] {
		return publication{}, "bar size must be 1m, 5m, 15m, 30m, 1h, or 1d"
	}
	start, err1 := time.Parse(time.DateOnly, req.WindowStart)
	end, err2 := time.Parse(time.DateOnly, req.WindowEnd)
	if err1 != nil || err2 != nil || end.Before(start) || end.Sub(start) > publicationMaxSpan {
		return publication{}, "window must be YYYY-MM-DD dates, start on or before end, spanning at most 40 years"
	}
	return publication{
		OwnerID: ownerID, DisplayName: name, Title: title, Definition: def,
		WindowStart: start.Format(time.DateOnly), WindowEnd: end.Format(time.DateOnly),
		BarSize: req.BarSize, Universe: universe, PublishedAt: now.UTC(),
	}, ""
}

func cleanText(s string, allowBreaks bool) (string, bool) {
	s = strings.TrimSpace(s)
	for _, r := range s {
		if r == '\n' || r == '\r' || r == '\t' {
			if !allowBreaks {
				return "", false
			}
			continue
		}
		if r < 0x20 || r == 0x7f {
			return "", false
		}
	}
	return s, true
}

// memoryPublications is the store used when the gateway has no database (unit tests).
type memoryPublications struct {
	mu   sync.Mutex
	rows []publication
}

func newMemoryPublications() *memoryPublications { return &memoryPublications{} }

func (m *memoryPublications) insertPublication(_ context.Context, p publication) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	n := 0
	for _, row := range m.rows {
		if row.OwnerID == p.OwnerID {
			n++
		}
	}
	if n >= publicationCap {
		return errPublicationLimit
	}
	m.rows = append(m.rows, p)
	return nil
}

func (m *memoryPublications) listPublications(_ context.Context, sortBy string) ([]publication, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := append([]publication(nil), m.rows...)
	sort.Slice(out, func(i, j int) bool {
		if sortBy == "name" {
			a, b := strings.ToLower(out[i].Title), strings.ToLower(out[j].Title)
			if a != b {
				return a < b
			}
			return out[i].ID < out[j].ID
		}
		if !out[i].PublishedAt.Equal(out[j].PublishedAt) {
			return out[i].PublishedAt.After(out[j].PublishedAt)
		}
		return out[i].ID > out[j].ID
	})
	return out, nil
}

func (m *memoryPublications) publicationByID(_ context.Context, id string) (publication, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, row := range m.rows {
		if row.ID == id {
			return row, nil
		}
	}
	return publication{}, errNotFound
}

func (m *memoryPublications) withdrawPublication(_ context.Context, id, ownerID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for i, row := range m.rows {
		if row.ID == id && row.OwnerID == ownerID {
			m.rows = append(m.rows[:i], m.rows[i+1:]...)
			return nil
		}
	}
	return errNotFound
}
