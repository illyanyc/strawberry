// Package resilience holds the retry, rate-limit, and circuit-breaker primitives shared by broker
// adapters and data clients (rules BR-012, BR-016, MD-008).
//
//   - Backoff: full-jitter exponential backoff (AWS architecture blog), bounded by a cap.
//   - Limiter: token bucket with strict priority classes, so cancels never wait behind queries.
//   - Breaker: closed / open / half-open circuit breaker on consecutive failures.
//   - RetryAfter: parses an HTTP Retry-After header.
package resilience

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Backoff computes full-jitter exponential delays: sleep = rand[0, min(Cap, Base·2^attempt)).
type Backoff struct {
	Base time.Duration
	Cap  time.Duration
	// Rand returns a float in [0,1); nil uses math/rand/v2. Tests inject a fixed source.
	Rand func() float64
}

// DefaultBackoff is base 1 s, cap 60 s (BR-012).
var DefaultBackoff = Backoff{Base: time.Second, Cap: time.Minute}

// Ceiling is the upper bound of the jitter window for attempt (0-based).
func (b Backoff) Ceiling(attempt int) time.Duration {
	if attempt < 0 {
		attempt = 0
	}
	if attempt > 30 {
		attempt = 30
	}
	c := b.Base << attempt
	if c <= 0 || c > b.Cap {
		c = b.Cap
	}
	return c
}

// Delay returns the jittered delay for attempt (0-based).
func (b Backoff) Delay(attempt int) time.Duration {
	r := b.Rand
	if r == nil {
		r = rand.Float64
	}
	return time.Duration(r() * float64(b.Ceiling(attempt)))
}

// Sleep waits for d or until ctx is done.
func Sleep(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// ErrPermanent wraps errors that must not be retried.
var ErrPermanent = errors.New("permanent")

// RetryAfterError carries a server-requested wait (HTTP 429/503 with Retry-After).
type RetryAfterError struct {
	Wait time.Duration
	Err  error
}

func (e *RetryAfterError) Error() string { return e.Err.Error() }
func (e *RetryAfterError) Unwrap() error { return e.Err }

// Retry runs fn up to attempts times with full-jitter backoff. It stops on success, on an error
// wrapping ErrPermanent, or when ctx ends. A *RetryAfterError waits at least the requested time.
// Only idempotent operations may be retried (callers decide; order submission never is).
func Retry(ctx context.Context, b Backoff, attempts int, fn func(ctx context.Context) error) error {
	var err error
	for i := range attempts {
		if err = fn(ctx); err == nil || errors.Is(err, ErrPermanent) {
			return err
		}
		if i == attempts-1 {
			break
		}
		d := b.Delay(i)
		var ra *RetryAfterError
		if errors.As(err, &ra) && ra.Wait > d {
			d = ra.Wait
		}
		if serr := Sleep(ctx, d); serr != nil {
			return errors.Join(err, serr)
		}
	}
	return err
}

// RetryAfter parses a Retry-After header (seconds or HTTP date). It returns 0 when absent.
func RetryAfter(h http.Header, now time.Time) time.Duration {
	v := h.Get("Retry-After")
	if v == "" {
		return 0
	}
	if s, err := strconv.Atoi(v); err == nil && s >= 0 {
		return time.Duration(s) * time.Second
	}
	if t, err := http.ParseTime(v); err == nil && t.After(now) {
		return t.Sub(now)
	}
	return 0
}

// Priority classes for Limiter; lower value is served first (BR-016: cancel > submit > query).
type Priority int

const (
	PriorityCancel Priority = iota
	PrioritySubmit
	PriorityQuery
	PriorityBulk
	numPriorities
)

// Limiter is a token bucket refilled continuously at Rate per Period with burst capacity Burst.
// Waiters are served strictly by priority, FIFO within a priority.
type Limiter struct {
	mu      sync.Mutex
	rate    float64 // tokens per second
	burst   float64
	tokens  float64
	last    time.Time
	now     func() time.Time
	waiters [numPriorities][]chan struct{}
	pausedU time.Time // no tokens are granted before this time (Retry-After)
}

// NewLimiter allows perPeriod requests per period with the given burst.
func NewLimiter(perPeriod int, period time.Duration, burst int) *Limiter {
	if burst < 1 {
		burst = 1
	}
	return &Limiter{rate: float64(perPeriod) / period.Seconds(), burst: float64(burst), tokens: float64(burst), now: time.Now}
}

// Pause stops granting tokens until now+d (server asked us to back off, BR-016).
func (l *Limiter) Pause(d time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if t := l.now().Add(d); t.After(l.pausedU) {
		l.pausedU = t
	}
}

func (l *Limiter) refill(now time.Time) {
	if !l.last.IsZero() {
		l.tokens += now.Sub(l.last).Seconds() * l.rate
		if l.tokens > l.burst {
			l.tokens = l.burst
		}
	}
	l.last = now
}

// higherWaiting reports whether anyone with priority strictly higher than p is queued.
func (l *Limiter) higherWaiting(p Priority) bool {
	for q := Priority(0); q < p; q++ {
		if len(l.waiters[q]) > 0 {
			return true
		}
	}
	return false
}

// Wait blocks until a token is available for priority p or ctx ends.
func (l *Limiter) Wait(ctx context.Context, p Priority) error {
	if p < 0 || p >= numPriorities {
		p = PriorityQuery
	}
	for {
		l.mu.Lock()
		now := l.now()
		l.refill(now)
		head := len(l.waiters[p]) == 0
		if !now.Before(l.pausedU) && l.tokens >= 1 && head && !l.higherWaiting(p) {
			l.tokens--
			l.mu.Unlock()
			return nil
		}
		ch := make(chan struct{}, 1)
		l.waiters[p] = append(l.waiters[p], ch)
		wait := time.Duration((1 - l.tokens) / l.rate * float64(time.Second))
		if pu := l.pausedU.Sub(now); pu > wait {
			wait = pu
		}
		if wait < time.Millisecond {
			wait = time.Millisecond
		}
		l.mu.Unlock()

		t := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			t.Stop()
			l.remove(p, ch)
			return ctx.Err()
		case <-t.C:
		case <-ch:
			t.Stop()
		}
		l.mu.Lock()
		now = l.now()
		l.refill(now)
		// Grant only if we are first in our class and nobody more important waits.
		if !now.Before(l.pausedU) && l.tokens >= 1 && len(l.waiters[p]) > 0 && l.waiters[p][0] == ch && !l.higherWaiting(p) {
			l.waiters[p] = l.waiters[p][1:]
			l.tokens--
			l.wakeNext()
			l.mu.Unlock()
			return nil
		}
		l.mu.Unlock()
		l.remove(p, ch)
	}
}

// wakeNext nudges the highest-priority head waiter. Caller holds mu.
func (l *Limiter) wakeNext() {
	for q := range numPriorities {
		if len(l.waiters[q]) > 0 {
			select {
			case l.waiters[q][0] <- struct{}{}:
			default:
			}
			return
		}
	}
}

func (l *Limiter) remove(p Priority, ch chan struct{}) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for i, c := range l.waiters[p] {
		if c == ch {
			l.waiters[p] = append(l.waiters[p][:i], l.waiters[p][i+1:]...)
			return
		}
	}
}

// BreakerState of a circuit breaker.
type BreakerState int

const (
	BreakerClosed BreakerState = iota
	BreakerOpen
	BreakerHalfOpen
)

func (s BreakerState) String() string {
	switch s {
	case BreakerClosed:
		return "closed"
	case BreakerOpen:
		return "open"
	case BreakerHalfOpen:
		return "half_open"
	default:
		return "unknown"
	}
}

// ErrOpen is returned by Breaker.Allow while the breaker is open.
var ErrOpen = errors.New("circuit breaker open")

// Breaker opens after Threshold consecutive failures, stays open for Cooldown, then lets one
// probe through (half-open); a successful probe closes it (MD-008).
type Breaker struct {
	mu        sync.Mutex
	Threshold int
	Cooldown  time.Duration
	now       func() time.Time
	state     BreakerState
	failures  int
	openedAt  time.Time
	probing   bool
	OnChange  func(from, to BreakerState)
}

// NewBreaker returns a closed breaker.
func NewBreaker(threshold int, cooldown time.Duration) *Breaker {
	return &Breaker{Threshold: threshold, Cooldown: cooldown, now: time.Now}
}

// State returns the current state (transitioning open → half-open when the cooldown elapsed).
func (b *Breaker) State() BreakerState {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.tick()
	return b.state
}

func (b *Breaker) set(s BreakerState) {
	if s == b.state {
		return
	}
	from := b.state
	b.state = s
	if b.OnChange != nil {
		b.OnChange(from, s)
	}
}

func (b *Breaker) tick() {
	if b.state == BreakerOpen && b.now().Sub(b.openedAt) >= b.Cooldown {
		b.set(BreakerHalfOpen)
		b.probing = false
	}
}

// Allow reports whether a call may proceed. In half-open state only one probe is allowed.
func (b *Breaker) Allow() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.tick()
	switch b.state {
	case BreakerOpen:
		return ErrOpen
	case BreakerHalfOpen:
		if b.probing {
			return ErrOpen
		}
		b.probing = true
	}
	return nil
}

// Record reports the outcome of an allowed call.
func (b *Breaker) Record(err error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if err == nil {
		b.failures = 0
		b.probing = false
		b.set(BreakerClosed)
		return
	}
	b.failures++
	if b.state == BreakerHalfOpen || b.failures >= b.Threshold {
		b.openedAt = b.now()
		b.probing = false
		b.set(BreakerOpen)
	}
}
