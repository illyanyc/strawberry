package gateway

import (
	"context"
	"errors"
	"net/http"
	"regexp"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/illyanyc/strawberry/libs/go/policy"
)

// SE-067: an FMP key is write-only, sealed, and paper-only. The response and the audit
// record carry a hint, never the secret. Live is refused because this key cannot place an order
// and is not a broker environment. Alpaca live keys stay on the broker credential route and are
// still refused by the hosted connections screen while live trading is off.

const fmpKind = "fmp"

var (
	keyIDPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{4,80}$`)
	errKeyGone   = errors.New("api key not found")
)

type apiKeyRecord struct {
	OwnerID     string
	Kind        string
	Environment string
	KeyHint     string
	Ciphertext  []byte
	CreatedAt   time.Time
	RotatedAt   time.Time
}

type disclosureRecord struct {
	OwnerID    string
	AcceptedAt time.Time
}

type keyRepo interface {
	putAPIKey(ctx context.Context, rec apiKeyRecord) error
	listAPIKeys(ctx context.Context, ownerID string) ([]apiKeyRecord, error)
	deleteAPIKey(ctx context.Context, ownerID, kind, environment string) error
	putDisclosure(ctx context.Context, rec disclosureRecord) error
	getDisclosure(ctx context.Context, ownerID string) (disclosureRecord, error)
}

type memoryKeys struct {
	mu    sync.Mutex
	keys  map[string]apiKeyRecord
	discl map[string]disclosureRecord
}

func newMemoryKeys() *memoryKeys {
	return &memoryKeys{keys: map[string]apiKeyRecord{}, discl: map[string]disclosureRecord{}}
}

func keyMapID(owner, kind, environment string) string {
	return owner + "|" + kind + "|" + environment
}

func (m *memoryKeys) putAPIKey(_ context.Context, rec apiKeyRecord) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	id := keyMapID(rec.OwnerID, rec.Kind, rec.Environment)
	if prev, ok := m.keys[id]; ok {
		rec.CreatedAt = prev.CreatedAt
	}
	m.keys[id] = rec
	return nil
}

func (m *memoryKeys) listAPIKeys(_ context.Context, ownerID string) ([]apiKeyRecord, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]apiKeyRecord, 0)
	for _, rec := range m.keys {
		if rec.OwnerID == ownerID {
			out = append(out, rec)
		}
	}
	return out, nil
}

func (m *memoryKeys) deleteAPIKey(_ context.Context, ownerID, kind, environment string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	id := keyMapID(ownerID, kind, environment)
	if _, ok := m.keys[id]; !ok {
		return errKeyGone
	}
	delete(m.keys, id)
	return nil
}

func (m *memoryKeys) putDisclosure(_ context.Context, rec disclosureRecord) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.discl[rec.OwnerID] = rec
	return nil
}

func (m *memoryKeys) getDisclosure(_ context.Context, ownerID string) (disclosureRecord, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	rec, ok := m.discl[ownerID]
	if !ok {
		return disclosureRecord{}, errNotFound
	}
	return rec, nil
}

type keyPutBody struct {
	KeyID  string `json:"key_id"`
	Secret string `json:"secret"`
}

type disclosureBody struct {
	Accepted bool `json:"accepted"`
}

func keyHint(keyID string) string {
	r := []rune(keyID)
	if len(r) < 4 {
		return "••••"
	}
	return string(r[len(r)-4:])
}

func (s *Server) handleKeysList(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActBrokerCredStatus, policy.Resource{}) {
		return
	}
	rows, err := s.keyStore.listAPIKeys(r.Context(), rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "api keys", err)
		return
	}
	out := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		out = append(out, keyView(row))
	}
	writeJSON(w, http.StatusOK, map[string]any{"keys": out})
}

func (s *Server) handleKeyPut(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	// SE-067: same step-up as a broker credential write. The secret is not logged.
	if !s.authorize(w, rc, policy.ActBrokerCredWrite, policy.Resource{}) {
		return
	}
	if r.PathValue("kind") != fmpKind {
		writeError(w, http.StatusBadRequest, "bad_request", "kind must be fmp")
		return
	}
	var body keyPutBody
	if err := decode(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid key")
		return
	}
	if !keyIDPattern.MatchString(body.KeyID) || utf8.RuneCountInString(body.Secret) < 8 || utf8.RuneCountInString(body.Secret) > 200 {
		writeError(w, http.StatusBadRequest, "bad_request", "key id and secret are not acceptable")
		return
	}
	owner := rc.session.OwnerID
	aad := "tb1|" + owner + "|fmp|paper"
	sealed, err := seal(s.keys.apiKeyEnc, []byte(body.Secret), aad)
	if err != nil {
		s.internalError(w, r, "api key seal", err)
		return
	}
	now := s.now()
	rec := apiKeyRecord{
		OwnerID: owner, Kind: fmpKind, Environment: "paper", KeyHint: keyHint(body.KeyID),
		Ciphertext: sealed, CreatedAt: now, RotatedAt: now,
	}
	if err := s.keyStore.putAPIKey(r.Context(), rec); err != nil {
		s.internalError(w, r, "api key put", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "apikey.put", "fmp", map[string]any{
		"kind": fmpKind, "environment": "paper", "key_hint": rec.KeyHint,
	}, "", "fmp"); err != nil {
		s.internalError(w, r, "api key audit", err)
		return
	}
	writeJSON(w, http.StatusOK, keyView(rec))
}

func (s *Server) handleKeyDelete(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActBrokerCredDelete, policy.Resource{}) {
		return
	}
	if r.PathValue("kind") != fmpKind {
		writeError(w, http.StatusBadRequest, "bad_request", "kind must be fmp")
		return
	}
	if err := s.keyStore.deleteAPIKey(r.Context(), rc.session.OwnerID, fmpKind, "paper"); err != nil {
		if errors.Is(err, errKeyGone) {
			writeError(w, http.StatusNotFound, "not_found", "key not stored")
			return
		}
		s.internalError(w, r, "api key delete", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "apikey.delete", "fmp", map[string]any{
		"kind": fmpKind, "environment": "paper",
	}, "", ""); err != nil {
		s.internalError(w, r, "api key audit", err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func keyView(rec apiKeyRecord) map[string]any {
	return map[string]any{
		"kind": rec.Kind, "environment": rec.Environment, "key_hint": rec.KeyHint,
		"created_at": rec.CreatedAt.UTC().Format(time.RFC3339),
		"rotated_at": rec.RotatedAt.UTC().Format(time.RFC3339),
	}
}

func (s *Server) handleDisclosureGet(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActSettingsRead, policy.Resource{}) {
		return
	}
	rec, err := s.keyStore.getDisclosure(r.Context(), rc.session.OwnerID)
	if errors.Is(err, errNotFound) {
		writeJSON(w, http.StatusOK, map[string]any{"accepted": false})
		return
	}
	if err != nil {
		s.internalError(w, r, "disclosure", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"accepted": true, "accepted_at": rec.AcceptedAt.UTC().Format(time.RFC3339)})
}

func (s *Server) handleDisclosurePost(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActSettingsWrite, policy.Resource{}) {
		return
	}
	var body disclosureBody
	if err := decode(r, &body); err != nil || !body.Accepted {
		writeError(w, http.StatusBadRequest, "bad_request", "accepted must be true")
		return
	}
	now := s.now()
	rec := disclosureRecord{OwnerID: rc.session.OwnerID, AcceptedAt: now}
	if err := s.keyStore.putDisclosure(r.Context(), rec); err != nil {
		s.internalError(w, r, "disclosure", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "risk.disclosure.accept", rc.session.OwnerID, map[string]any{
		"accepted": true,
	}, "", ""); err != nil {
		s.internalError(w, r, "disclosure audit", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"accepted": true, "accepted_at": now.UTC().Format(time.RFC3339)})
}
