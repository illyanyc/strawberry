// Package testkit wires accounts for tests: fast options, an in-memory store, and contract hooks
// for the simulator.
package testkit

import (
	"context"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/execstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
)

// FastOptions shrink every timer so tests run in milliseconds.
func FastOptions() adapter.Options {
	o := adapter.DefaultOptions()
	o.Backoff = resilience.Backoff{Base: 10 * time.Millisecond, Cap: 50 * time.Millisecond}
	o.LookupAttempts = 3
	o.LookupBackoff = resilience.Backoff{Base: 10 * time.Millisecond, Cap: 30 * time.Millisecond}
	o.SnapshotInterval = 100 * time.Millisecond
	return o
}

// Audit records audit events in memory.
type Audit struct {
	mu     sync.Mutex
	Events []audit.Event
	Fail   error
}

// Append implements adapter.Auditor.
func (a *Audit) Append(_ context.Context, e audit.Event) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.Fail != nil {
		return a.Fail
	}
	a.Events = append(a.Events, e)
	return nil
}

// Actions returns the recorded actions.
func (a *Audit) Actions() []string {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]string, 0, len(a.Events))
	for _, e := range a.Events {
		out = append(out, e.Action)
	}
	return out
}

// Run starts acct in the background until the test ends.
func Run(t *testing.T, acct *adapter.Account) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { acct.Run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); <-done })
}

// WaitState waits until the account reaches state.
func WaitState(t *testing.T, acct *adapter.Account, want interface{ String() string }) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if acct.Session().State().String() == want.String() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("account %s stuck in %s, want %s", acct.Info().AccountID, acct.Session().State(), want)
}

// SimAccount builds a running simulator account with an in-memory store.
func SimAccount(t *testing.T, cfg sim.Config) (*sim.Sim, *adapter.Account, *execstore.Memory, *Audit) {
	t.Helper()
	if cfg.AccountID == "" {
		cfg.AccountID = "sim-paper"
	}
	s := sim.New(cfg)
	store := execstore.NewMemory()
	aud := &Audit{}
	acct := adapter.NewAccount(s, store, aud, nil, slog.New(slog.DiscardHandler), nil, FastOptions())
	return s, acct, store, aud
}

// SimHooks implements contract.Hooks for a simulator account.
type SimHooks struct {
	Sim   *sim.Sim
	Store *execstore.Memory
	Acct  string
}

func (h SimHooks) AmbiguousNextSubmit() { h.Sim.FailNextSubmitAfterAccept(1) }
func (h SimHooks) LostNextSubmit()      { h.Sim.FailNextSubmitBeforeAccept(1) }
func (h SimHooks) DropStream()          { h.Sim.Disconnect() }
func (h SimHooks) Fill(symbol string) {
	h.Sim.SetPrice(symbol, decimal.RequireFromString("0.5"))
	h.Sim.SetPrice(symbol, decimal.NewFromInt(100))
}
func (h SimHooks) InjectManualOrder(symbol string) string {
	return h.Sim.InjectManualOrder(symbol, commonv1.Side_SIDE_BUY, decimal.NewFromInt(1))
}
func (h SimHooks) ExecutionsPublished(execID string) int {
	return CountExec(h.Store, h.Acct, execID)
}
func (h SimHooks) ReconciliationRequests() int {
	return len(h.Store.Messages(execstore.ReconSubject(h.Acct)))
}

// CountExec counts exec.<acct> publications of execID.
func CountExec(store *execstore.Memory, acct, execID string) int {
	n := 0
	for _, m := range store.Messages(execstore.ExecSubject(acct)) {
		if strings.HasSuffix(m.MsgID, "|"+execID) {
			n++
		}
	}
	return n
}
