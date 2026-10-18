// Package session is the per-account broker session state machine (rules BR-010, OE-004):
// disconnected → connecting → authenticated → syncing → ready ⇄ degraded, plus stopped.
package session

import (
	"fmt"
	"slices"
	"sync"
	"time"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
)

// State aliases the contract enum.
type State = brokerv1.SessionState

// States.
const (
	Disconnected  = brokerv1.SessionState_SESSION_STATE_DISCONNECTED
	Connecting    = brokerv1.SessionState_SESSION_STATE_CONNECTING
	Authenticated = brokerv1.SessionState_SESSION_STATE_AUTHENTICATED
	Syncing       = brokerv1.SessionState_SESSION_STATE_SYNCING
	Ready         = brokerv1.SessionState_SESSION_STATE_READY
	Degraded      = brokerv1.SessionState_SESSION_STATE_DEGRADED
	Stopped       = brokerv1.SessionState_SESSION_STATE_STOPPED
)

// allowed lists legal transitions (BR-010). Any state may go to Disconnected (connection lost) or
// Stopped (shutdown / live gate closed).
var allowed = map[State][]State{
	Disconnected:  {Connecting},
	Connecting:    {Authenticated},
	Authenticated: {Syncing},
	Syncing:       {Ready, Degraded},
	Ready:         {Degraded},
	Degraded:      {Syncing},
	Stopped:       {Disconnected},
}

// Machine holds one account's session state. It is safe for concurrent use.
type Machine struct {
	mu        sync.Mutex
	account   string
	state     State
	since     time.Time
	reasons   []string
	heartbeat time.Time
	now       func() time.Time
	onChange  []func(from, to State, reasons []string)
}

// New returns a machine in the Disconnected state.
func New(account string, now func() time.Time) *Machine {
	if now == nil {
		now = time.Now
	}
	return &Machine{account: account, state: Disconnected, since: now(), now: now}
}

// OnChange registers a callback run (synchronously, without the lock) after every transition.
func (m *Machine) OnChange(f func(from, to State, reasons []string)) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.onChange = append(m.onChange, f)
}

// To transitions to s with optional reasons. Illegal transitions return an error and leave the
// state unchanged.
func (m *Machine) To(s State, reasons ...string) error {
	m.mu.Lock()
	from := m.state
	if from == s {
		m.reasons = append([]string(nil), reasons...)
		m.mu.Unlock()
		return nil
	}
	if s != Disconnected && s != Stopped && !slices.Contains(allowed[from], s) {
		m.mu.Unlock()
		return fmt.Errorf("session %s: illegal transition %s → %s", m.account, from, s)
	}
	m.state = s
	m.since = m.now()
	m.reasons = append([]string(nil), reasons...)
	cbs := slices.Clone(m.onChange)
	rs := slices.Clone(m.reasons)
	m.mu.Unlock()
	for _, f := range cbs {
		f(from, s, rs)
	}
	return nil
}

// Heartbeat records broker liveness.
func (m *Machine) Heartbeat() {
	m.mu.Lock()
	m.heartbeat = m.now()
	m.mu.Unlock()
}

// State returns the current state.
func (m *Machine) State() State {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.state
}

// CanSubmit reports whether new orders and replaces are allowed (only Ready, OE-004).
func (m *Machine) CanSubmit() bool { return m.State() == Ready }

// CanCancel reports whether cancels are allowed: Ready or Degraded, because cancelling reduces risk
// (BR-010).
func (m *Machine) CanCancel() bool {
	s := m.State()
	return s == Ready || s == Degraded
}

// Status renders the contract message.
func (m *Machine) Status() *brokerv1.SessionStatus {
	m.mu.Lock()
	defer m.mu.Unlock()
	st := &brokerv1.SessionStatus{
		BrokerAccountId: m.account,
		State:           m.state,
		StateSince:      m.since.UnixNano(),
		DegradedReasons: slices.Clone(m.reasons),
		Connected:       m.state == Authenticated || m.state == Syncing || m.state == Ready || m.state == Degraded,
		Authenticated:   m.state == Authenticated || m.state == Syncing || m.state == Ready || m.state == Degraded,
	}
	if !m.heartbeat.IsZero() {
		st.LastHeartbeat = m.heartbeat.UnixNano()
	}
	return st
}
