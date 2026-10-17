package clock

import (
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

func TestOP020HealthyFailsClosed(t *testing.T) {
	c := NewChecker("ntp.invalid", time.Minute, 250*time.Millisecond, prometheus.NewRegistry(), slog.New(slog.NewTextHandler(io.Discard, nil)))
	if c.Healthy() {
		t.Fatal("no measurement must be unhealthy")
	}
	c.query = func(string) (time.Duration, error) { return 100 * time.Millisecond, nil }
	c.measure()
	if !c.Healthy() {
		t.Fatal("small skew should be healthy")
	}
	c.query = func(string) (time.Duration, error) { return -2 * time.Second, nil }
	c.measure()
	if c.Healthy() {
		t.Fatal("large negative skew must be unhealthy")
	}
	c.query = func(string) (time.Duration, error) { return 0, errors.New("timeout") }
	c.mu.Lock()
	c.measured = time.Now().Add(-time.Hour)
	c.mu.Unlock()
	c.measure()
	if c.Healthy() {
		t.Fatal("stale measurement must be unhealthy")
	}
}
