package gateway

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	signalsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/signals/v1"
)

// SignalBus reads recent signals from the JetStream SIGNALS stream and publishes TradingView
// webhook signals into it. Subjects are sig.<strategy_id>.<instrument_id> (UI-049, D68).
type SignalBus interface {
	// Recent returns up to limit signals for the instrument with a stream time at or after since,
	// oldest first.
	Recent(ctx context.Context, instrumentID string, since time.Time, limit int) ([]*signalsv1.Signal, error)
	// Publish stores one signal with a JetStream message ID for deduplication.
	Publish(ctx context.Context, subject, msgID string, sig *signalsv1.Signal) error
}

// SignalSubject is the bus subject of a signal: sig.<strategy_id>.<instrument_id>.
func SignalSubject(strategyID, instrumentID string) string {
	return "sig." + strategyID + "." + instrumentID
}

const maxSignalLookback = 30 * 24 * time.Hour

// handleSignals returns the recent model and webhook signals of one instrument for chart overlays
// (UI-049). Signals are never orders; the chart only draws them.
func (s *Server) handleSignals(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id := r.URL.Query().Get("instrument_id")
	since, ok := qNs(r, "since")
	if !idRe.MatchString(id) || !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "instrument_id is required; since must be UTC nanoseconds")
		return
	}
	if !s.readGuard(w, rc) {
		return
	}
	if s.signals == nil {
		writeError(w, http.StatusServiceUnavailable, "bus_unavailable", "signals unavailable")
		return
	}
	now := s.now()
	from := now.Add(-24 * time.Hour)
	if since > 0 {
		from = time.Unix(0, since)
	}
	if from.Before(now.Add(-maxSignalLookback)) {
		from = now.Add(-maxSignalLookback)
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	sigs, err := s.signals.Recent(ctx, id, from, int(qInt(r, "limit", 500, 2000)))
	if err != nil {
		s.log.WarnContext(r.Context(), "signals read failed", "error", err)
		writeError(w, http.StatusServiceUnavailable, "bus_unavailable", "signals unavailable")
		return
	}
	out := make([]json.RawMessage, 0, len(sigs))
	for _, sg := range sigs {
		b, err := pj.Marshal(sg)
		if err == nil {
			out = append(out, b)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"signals": out})
}
