package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"time"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// statusSnapshot is the trading-status payload shown by the UI's global status bar: environment,
// live flag, session expiry, and execution's TradingStatus (per-account session, reconciliation,
// margin and day-trade state, active kill switches and halts; UI-013). `trading` is null with
// `trading_error` set when execution cannot be reached, so the UI shows "unknown", never "ok".
func (s *Server) statusSnapshot(r *http.Request, rc *reqCtx) map[string]any {
	live := false
	if stored, err := s.store.settings(r.Context(), rc.session.OwnerID); err == nil {
		if row, ok := stored["trading.live_enabled"]; ok {
			_ = json.Unmarshal(row.Value, &live)
		}
	}
	out := map[string]any{
		"environment":     s.cfg.Environment,
		"live_enabled":    live,
		"mode":            map[bool]string{true: "live", false: "paper"}[live],
		"server_time":     s.now().UTC(),
		"session_expires": rc.session.IdleExpires,
		"trading":         nil,
		"feeds":           nil,
	}
	// Feed freshness (UI-013): null with feeds_error when marketdata cannot be reached, never "ok".
	// Fetched concurrently with execution's status so a slow service delays the snapshot only once.
	type feedResult struct {
		raw json.RawMessage
		err error
	}
	feedCh := make(chan feedResult, 1)
	go func() {
		raw, err := s.feedStatusJSON(r.Context())
		feedCh <- feedResult{raw, err}
	}()
	trading, err := s.tradingStatusJSON(r.Context())
	if f := <-feedCh; f.err != nil {
		out["feeds_error"] = "marketdata_unavailable"
	} else {
		out["feeds"] = f.raw
	}
	if err != nil {
		out["trading_error"] = "execution_unavailable"
		return out
	}
	out["trading"] = trading
	return out
}

func (s *Server) feedStatusJSON(ctx context.Context) (json.RawMessage, error) {
	if s.trading == nil || s.trading.MD == nil {
		return nil, errors.New("marketdata not configured")
	}
	ctx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	st, err := s.trading.MD.GetFeedStatus(ctx, &mdv1.GetFeedStatusRequest{})
	if err != nil {
		return nil, err
	}
	return pj.Marshal(st)
}

func (s *Server) tradingStatusJSON(ctx context.Context) (json.RawMessage, error) {
	if s.trading == nil || s.trading.Exec == nil {
		return nil, errors.New("execution not configured")
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	st, err := s.trading.Exec.GetTradingStatus(ctx, &ordersv1.GetTradingStatusRequest{})
	if err != nil {
		return nil, err
	}
	return pj.Marshal(st.GetStatus())
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
