package sched

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/illyanyc/strawberry/libs/go/calendar"
)

func TestSchedulerRunsOncePerSessionIncludingHalfDays(t *testing.T) {
	now := time.Date(2025, 11, 28, 18, 30, 0, 0, time.UTC) // half day: close 13:00 ET = 18:00 UTC
	var ran []string
	fail := true
	s := &Scheduler{Cal: calendar.XNYS(), Now: func() time.Time { return now }, Jobs: []Job{
		{Name: "eod", Due: AfterClose(15 * time.Minute), Run: func(_ context.Context, s calendar.Session) error { ran = append(ran, "eod "+s.Date); return nil }},
		{Name: "recon", Due: At(7, 0, 1), Run: func(_ context.Context, s calendar.Session) error {
			if fail {
				fail = false
				return errors.New("fmp not ready")
			}
			ran = append(ran, "recon "+s.Date)
			return nil
		}},
	}}
	s.RunDue(context.Background())
	s.RunDue(context.Background())
	// EOD for the half day ran at 13:15 ET; recon for 11-26 is retried after a failure; jobs more than 72 h overdue are skipped.
	want := map[string]bool{"eod 2025-11-28": true, "eod 2025-11-26": true, "eod 2025-11-25": true, "recon 2025-11-26": true, "recon 2025-11-25": true}
	got := map[string]int{}
	for _, r := range ran {
		got[r]++
	}
	for k := range want {
		if got[k] != 1 {
			t.Errorf("%s ran %d times", k, got[k])
		}
	}
	if got["recon 2025-11-28"] != 0 {
		t.Error("recon for today must wait for next morning")
	}
}
