package gateway

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"
)

// statusSnapshot is the trading-status payload shown by the UI's global status bar. In Phase 0 it
// carries only what exists (environment, live flag, session expiry); later phases add broker
// sessions, feed freshness, reconciliation, kill switches, and margin-guard state.
func (s *Server) statusSnapshot(r *http.Request, rc *reqCtx) map[string]any {
	live := false
	if stored, err := s.store.settings(r.Context(), rc.session.OwnerID); err == nil {
		if row, ok := stored["trading.live_enabled"]; ok {
			_ = json.Unmarshal(row.Value, &live)
		}
	}
	return map[string]any{
		"environment":     s.cfg.Environment,
		"live_enabled":    live,
		"mode":            map[bool]string{true: "live", false: "paper"}[live],
		"server_time":     s.now().UTC(),
		"session_expires": rc.session.IdleExpires,
	}
}

func (s *Server) handleSystemStatus(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	writeJSON(w, http.StatusOK, s.statusSnapshot(r, rc))
}

// handleEvents streams server-sent events (D14: SSE first). The session is checked at connect (the
// route is authenticated) and re-checked on every tick, so a revoked or expired session ends the stream.
// Event IDs are monotonic per connection so EventSource can resume with Last-Event-ID.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	rcw := http.NewResponseController(w)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	seq, _ := strconv.ParseInt(r.Header.Get("Last-Event-ID"), 10, 64)
	send := func() bool {
		seq++
		b, _ := json.Marshal(s.statusSnapshot(r, rc))
		if _, err := fmt.Fprintf(w, "id: %d\nevent: trading-status\nretry: 3000\ndata: %s\n\n", seq, b); err != nil {
			return false
		}
		return rcw.Flush() == nil
	}
	if !send() {
		return
	}
	tick := time.NewTicker(s.eventsInterval())
	defer tick.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-tick.C:
			// Re-check without sliding the idle timeout: an open tab is not owner activity.
			sess, err := s.store.peekSession(r.Context(), rc.session.IDHash)
			if err != nil {
				_, _ = fmt.Fprint(w, "event: session-ended\ndata: {}\n\n")
				_ = rcw.Flush()
				return
			}
			rc.session = sess
			if !send() {
				return
			}
		}
	}
}

func (s *Server) eventsInterval() time.Duration {
	if s.cfg.Environment == "test" {
		return 50 * time.Millisecond
	}
	return 15 * time.Second
}
