package session

import "testing"

func TestBR010Transitions(t *testing.T) {
	m := New("sim-paper", nil)
	var seen []State
	m.OnChange(func(_, to State, _ []string) { seen = append(seen, to) })
	for _, s := range []State{Connecting, Authenticated, Syncing, Ready, Degraded, Syncing, Ready} {
		if err := m.To(s); err != nil {
			t.Fatal(err)
		}
	}
	if len(seen) != 7 || !m.CanSubmit() {
		t.Fatalf("seen %v", seen)
	}
	// Illegal shortcuts.
	m2 := New("x", nil)
	for _, s := range []State{Ready, Syncing, Authenticated, Degraded} {
		if err := m2.To(s); err == nil {
			t.Fatalf("disconnected → %s allowed", s)
		}
	}
	_ = m2.To(Connecting)
	if err := m2.To(Ready); err == nil {
		t.Fatal("connecting → ready allowed (must sync first, OE-008)")
	}
	// Any state can drop to disconnected or stopped.
	if err := m.To(Disconnected, "stream closed"); err != nil {
		t.Fatal(err)
	}
	if m.CanSubmit() || m.CanCancel() {
		t.Fatal("disconnected session accepts orders")
	}
	st := m.Status()
	if st.GetState() != Disconnected || len(st.GetDegradedReasons()) != 1 || st.GetConnected() {
		t.Fatalf("status %+v", st)
	}
	if err := m.To(Stopped); err != nil {
		t.Fatal(err)
	}
}

func TestBR010CancelAllowedWhenDegraded(t *testing.T) {
	m := New("a", nil)
	for _, s := range []State{Connecting, Authenticated, Syncing, Ready, Degraded} {
		_ = m.To(s)
	}
	if m.CanSubmit() {
		t.Fatal("degraded accepts submits")
	}
	if !m.CanCancel() {
		t.Fatal("degraded must accept cancels")
	}
}
