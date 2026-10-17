package calendar

import (
	"testing"
	"time"
)

func mustT(s string) time.Time {
	t, err := time.Parse(time.RFC3339, s)
	if err != nil {
		panic(err)
	}
	return t
}

func TestDF011DSTAndHalfDays(t *testing.T) {
	c := XNYS()
	// Winter: 14:30 UTC open; summer (after 2026-03-08 DST): 13:30 UTC.
	w, _ := c.Session("2026-03-06")
	s, _ := c.Session("2026-03-09")
	if w.Open != mustT("2026-03-06T14:30:00Z") || s.Open != mustT("2026-03-09T13:30:00Z") {
		t.Fatalf("DST: winter %v summer %v", w.Open, s.Open)
	}
	h, ok := c.Session("2025-11-28")
	if !ok || !h.EarlyClose || h.Close != mustT("2025-11-28T18:00:00Z") || h.PostClose != mustT("2025-11-28T22:00:00Z") {
		t.Fatalf("half day %+v", h)
	}
	for _, holiday := range []string{"2025-12-25", "2026-01-01", "2025-07-04", "2026-11-26"} {
		if _, ok := c.Session(holiday); ok {
			t.Fatalf("%s is a holiday", holiday)
		}
	}
	if len(h.BarOpens(15*time.Minute, false)) != 14 {
		t.Fatalf("half day 15m bars: %d", len(h.BarOpens(15*time.Minute, false)))
	}
	if n := len(w.BarOpens(time.Minute, false)); n != 390 {
		t.Fatalf("1m bars: %d", n)
	}
	if n := len(w.BarOpens(15*time.Minute, true)); n != 64 {
		t.Fatalf("extended 15m bars: %d", n)
	}
}

func TestSessionLookups(t *testing.T) {
	c := XNYS()
	at := mustT("2026-03-06T15:00:00Z")
	if !c.IsOpen(at) {
		t.Fatal("open at 10:00 ET")
	}
	if _, p, _ := c.SessionAt(mustT("2026-03-06T12:00:00Z")); p != PartPre {
		t.Fatalf("07:00 ET is pre, got %s", p)
	}
	if _, p, _ := c.SessionAt(mustT("2026-03-06T22:00:00Z")); p != PartPost {
		t.Fatalf("17:00 ET is post, got %s", p)
	}
	if c.IsOpen(mustT("2026-03-07T15:00:00Z")) {
		t.Fatal("Saturday")
	}
	n, _ := c.NextOpen(mustT("2026-03-06T21:00:00Z"))
	if n != mustT("2026-03-09T13:30:00Z") {
		t.Fatalf("next open %v", n)
	}
	p, _ := c.PreviousSession(mustT("2026-03-09T13:00:00Z"))
	if p.Date != "2026-03-06" {
		t.Fatalf("previous %s", p.Date)
	}
	if got := len(c.Sessions(mustT("2026-03-02T00:00:00Z"), mustT("2026-03-09T00:00:00Z"))); got != 5 {
		t.Fatalf("week sessions %d", got)
	}
	h, _ := c.Session("2025-11-28")
	if h.BarClose(mustT("2025-11-28T17:45:00Z"), time.Hour) != mustT("2025-11-28T18:00:00Z") {
		t.Fatal("bar must truncate at the early close")
	}
}
