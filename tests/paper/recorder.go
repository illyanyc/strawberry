package paper

import (
	"bufio"
	"os"
	"strings"
	"sync"

	"github.com/nats-io/nats.go"
	"google.golang.org/protobuf/encoding/protodelim"

	backtestv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/backtest/v1"
)

// recordPrefixes are the subjects a parity replay needs (ADR-0031, BT-070): final bars, intents,
// decisions, OMS order states, executions, and ledger updates.
var recordPrefixes = []string{"md.bar.", "ord.", "exec.", "ledger.update."}

// Recorder writes the bus traffic of a paper run as length-delimited tb.backtest.v1.RecordedMessage.
type Recorder struct {
	mu  sync.Mutex
	f   *os.File
	w   *bufio.Writer
	sub *nats.Subscription
	n   int
	now func() int64
	err error
}

// StartRecorder subscribes to every subject on the core connection (one subscription keeps the
// receive order) and records the parity subjects to path.
func StartRecorder(nc *nats.Conn, path string, now func() int64) (*Recorder, error) {
	f, err := os.Create(path) //nolint:gosec // test-provided path
	if err != nil {
		return nil, err
	}
	r := &Recorder{f: f, w: bufio.NewWriter(f), now: now}
	r.sub, err = nc.Subscribe(">", r.on)
	if err != nil {
		_ = f.Close()
		return nil, err
	}
	return r, nc.Flush()
}

func (r *Recorder) on(m *nats.Msg) {
	keep := false
	for _, p := range recordPrefixes {
		keep = keep || strings.HasPrefix(m.Subject, p)
	}
	if !keep {
		return
	}
	rm := &backtestv1.RecordedMessage{Subject: m.Subject, Data: m.Data, RecordedAt: r.now()}
	if m.Header != nil {
		rm.MsgId = m.Header.Get(nats.MsgIdHdr)
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err == nil {
		_, r.err = protodelim.MarshalTo(r.w, rm)
		r.n++
	}
}

// Close stops recording and flushes the file; it returns the number of messages written.
func (r *Recorder) Close() (int, error) {
	_ = r.sub.Unsubscribe()
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err == nil {
		r.err = r.w.Flush()
	}
	if err := r.f.Close(); r.err == nil {
		r.err = err
	}
	return r.n, r.err
}
