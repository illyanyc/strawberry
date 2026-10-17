// Package clock measures host clock skew against NTP and exposes it as `tb_clock_skew_seconds`
// (rule OP-020). Trading services consult Healthy before opening risk (consumed by section 07);
// on a Mac host the Docker VM clock can drift after sleep, which this catches.
package clock

import (
	"context"
	"log/slog"
	"math"
	"sync"
	"time"

	"github.com/beevik/ntp"
	"github.com/prometheus/client_golang/prometheus"
)

// Checker periodically queries an NTP server.
type Checker struct {
	server    string
	interval  time.Duration
	threshold time.Duration
	query     func(string) (time.Duration, error)
	log       *slog.Logger

	mu       sync.RWMutex
	skew     time.Duration
	measured time.Time

	gauge  prometheus.Gauge
	errors prometheus.Counter
}

// NewChecker creates a checker. threshold is the maximum tolerated absolute skew.
func NewChecker(server string, interval, threshold time.Duration, reg prometheus.Registerer, log *slog.Logger) *Checker {
	c := &Checker{
		server: server, interval: interval, threshold: threshold, log: log,
		query: func(s string) (time.Duration, error) {
			r, err := ntp.QueryWithOptions(s, ntp.QueryOptions{Timeout: 3 * time.Second})
			if err != nil {
				return 0, err
			}
			return r.ClockOffset, r.Validate()
		},
		gauge:  prometheus.NewGauge(prometheus.GaugeOpts{Name: "tb_clock_skew_seconds", Help: "Absolute host clock offset vs NTP."}),
		errors: prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_clock_skew_check_errors_total", Help: "Failed NTP queries."}),
	}
	reg.MustRegister(c.gauge, c.errors)
	return c
}

// Run measures until ctx is canceled.
func (c *Checker) Run(ctx context.Context) {
	t := time.NewTicker(c.interval)
	defer t.Stop()
	for {
		c.measure()
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func (c *Checker) measure() {
	off, err := c.query(c.server)
	if err != nil {
		c.errors.Inc()
		c.log.Warn("ntp query failed", "server", c.server, "error", err)
		return
	}
	c.mu.Lock()
	c.skew, c.measured = off, time.Now()
	c.mu.Unlock()
	c.gauge.Set(math.Abs(off.Seconds()))
}

// Healthy reports whether the last measurement is fresh (within 3 intervals) and within threshold.
// No measurement yet counts as unhealthy (fail closed).
func (c *Checker) Healthy() bool {
	c.mu.RLock()
	defer c.mu.RUnlock()
	if c.measured.IsZero() || time.Since(c.measured) > 3*c.interval {
		return false
	}
	return c.skew.Abs() <= c.threshold
}
