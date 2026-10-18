package gateway

import (
	"fmt"
	"net/http"
	"strconv"
	"sync"
	"time"

	"google.golang.org/protobuf/proto"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// MarketBus delivers bus messages to the chart stream (plain NATS subscriptions on subjects that
// JetStream also captures). fn is called from the subscription's goroutine and must not block.
type MarketBus interface {
	Subscribe(subject string, fn func(data []byte)) (unsubscribe func(), err error)
}

// partialEvery caps live partial-bar updates per chart (UI-012: at most 4 Hz, latest wins).
const partialEvery = 250 * time.Millisecond

type sseEvent struct {
	name string
	data []byte
}

// handleMarketStream streams one chart's live data over SSE (D14, UI-012): final and corrected
// bars (`bar`), the coalesced partial bar (`partial`), and executions in the instrument (`fill`).
// The session is re-checked like /api/v1/events. If the client falls behind, the stream ends with
// `resync` so the UI reloads history instead of silently missing bars.
func (s *Server) handleMarketStream(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, tf, ok := s.chartParams(w, r)
	if !ok || !s.readGuard(w, rc) {
		return
	}
	if s.market == nil {
		writeError(w, http.StatusServiceUnavailable, "bus_unavailable", "market stream unavailable")
		return
	}
	events := make(chan sseEvent, 256)
	overflow := make(chan struct{})
	var once sync.Once
	emit := func(name string, m proto.Message) {
		b, err := pj.Marshal(m)
		if err != nil {
			return
		}
		select {
		case events <- sseEvent{name, b}:
		default:
			once.Do(func() { close(overflow) })
		}
	}
	var mu sync.Mutex
	var partial *mdv1.Bar
	bar := func(data []byte) {
		var b mdv1.Bar
		if proto.Unmarshal(data, &b) == nil {
			emit("bar", &b)
		}
	}
	subs := map[string]func([]byte){
		"md.bar." + tf + "." + id:            bar,
		"md.bar.correction." + tf + "." + id: bar,
		"md.bar.partial." + tf + "." + id: func(data []byte) {
			var b mdv1.Bar
			if proto.Unmarshal(data, &b) == nil {
				mu.Lock()
				partial = &b
				mu.Unlock()
			}
		},
		"exec.>": func(data []byte) {
			var e ordersv1.ExecutionReport
			if proto.Unmarshal(data, &e) == nil && e.GetInstrumentId() == id {
				emit("fill", &e)
			}
		},
	}
	for subj, fn := range subs {
		unsub, err := s.market.Subscribe(subj, fn)
		if err != nil {
			s.internalError(w, r, "market stream subscribe", err)
			return
		}
		defer unsub()
	}

	rcw := http.NewResponseController(w)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	seq, _ := strconv.ParseInt(r.Header.Get("Last-Event-ID"), 10, 64)
	send := func(name string, data []byte) bool {
		seq++
		//nolint:gosec // text/event-stream, not HTML; data is protojson or built from regex-validated IDs
		if _, err := fmt.Fprintf(w, "id: %d\nevent: %s\nretry: 3000\ndata: %s\n\n", seq, name, data); err != nil {
			return false
		}
		return rcw.Flush() == nil
	}
	if !send("ready", []byte(`{"instrumentId":"`+id+`","timeframe":"`+tf+`"}`)) {
		return
	}
	flush := time.NewTicker(partialEvery)
	defer flush.Stop()
	check := time.NewTicker(s.eventsInterval())
	defer check.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-overflow:
			_ = send("resync", []byte(`{}`))
			return
		case ev := <-events:
			if !send(ev.name, ev.data) {
				return
			}
		case <-flush.C:
			mu.Lock()
			p := partial
			partial = nil
			mu.Unlock()
			if p != nil {
				b, err := pj.Marshal(p)
				if err == nil && !send("partial", b) {
					return
				}
			}
		case <-check.C:
			sess, err := s.store.peekSession(r.Context(), rc.session.IDHash)
			if err != nil {
				_ = send("session-ended", []byte(`{}`))
				return
			}
			rc.session = sess
			if _, err := fmt.Fprint(w, ": keep-alive\n\n"); err != nil || rcw.Flush() != nil {
				return
			}
		}
	}
}
