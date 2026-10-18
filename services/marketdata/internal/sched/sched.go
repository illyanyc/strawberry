// Package sched runs marketdata's calendar-driven jobs (MD-006, MD-010, ST-004): end-of-day
// incremental backfill (source-final bars replace provisional live bars, both versions kept),
// compaction, reference export, next-morning cross-source reconciliation, and the daily reference
// sync. Each job runs at most once per session date; all jobs are idempotent.
package sched

import (
	"context"
	"log/slog"
	"sync"
	"time"

	"github.com/illyanyc/strawberry/libs/go/calendar"
)

// Job is a named function run for a session.
type Job struct {
	Name string
	// Due returns when the job should run for session s.
	Due func(s calendar.Session) time.Time
	Run func(ctx context.Context, s calendar.Session) error
}

// Scheduler checks every Interval which jobs are due.
type Scheduler struct {
	Cal      *calendar.Calendar
	Jobs     []Job
	Interval time.Duration
	Now      func() time.Time
	Log      *slog.Logger
	mu       sync.Mutex
	done     map[string]bool // job|date
}

var ny = func() *time.Location { l, _ := time.LoadLocation("America/New_York"); return l }()

// At returns a Due function for a New York wall-clock time on the session date, offset by days.
func At(hour, minute, dayOffset int) func(calendar.Session) time.Time {
	return func(s calendar.Session) time.Time {
		d, _ := time.ParseInLocation("2006-01-02", s.Date, ny)
		return time.Date(d.Year(), d.Month(), d.Day()+dayOffset, hour, minute, 0, 0, ny)
	}
}

// AfterClose returns a Due function at the session's close (early closes included) plus d.
func AfterClose(d time.Duration) func(calendar.Session) time.Time {
	return func(s calendar.Session) time.Time { return s.Close.Add(d) }
}

// RunDue runs every job whose due time for the last few sessions has passed and that has not run.
func (s *Scheduler) RunDue(ctx context.Context) {
	now := s.Now()
	s.mu.Lock()
	if s.done == nil {
		s.done = map[string]bool{}
	}
	s.mu.Unlock()
	sessions := s.Cal.Sessions(now.Add(-5*24*time.Hour), now.Add(time.Hour))
	for _, sess := range sessions {
		for _, j := range s.Jobs {
			k := j.Name + "|" + sess.Date
			s.mu.Lock()
			ran := s.done[k]
			s.mu.Unlock()
			due := j.Due(sess)
			if ran || now.Before(due) || now.Sub(due) > 72*time.Hour {
				continue
			}
			err := j.Run(ctx, sess)
			if s.Log != nil {
				s.Log.Info("scheduled job", "job", j.Name, "session", sess.Date, "err", err)
			}
			if err == nil {
				s.mu.Lock()
				s.done[k] = true
				s.mu.Unlock()
			}
		}
	}
}

// Run loops until ctx ends.
func (s *Scheduler) Run(ctx context.Context) {
	if s.Interval <= 0 {
		s.Interval = time.Minute
	}
	t := time.NewTicker(s.Interval)
	defer t.Stop()
	for {
		s.RunDue(ctx)
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
