package resilience

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"testing"
	"time"
)

func TestBR012BackoffBounds(t *testing.T) {
	b := Backoff{Base: time.Second, Cap: time.Minute, Rand: func() float64 { return 0.999999 }}
	for attempt, want := range []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second, 32 * time.Second, time.Minute, time.Minute} {
		if got := b.Ceiling(attempt); got != want {
			t.Fatalf("ceiling(%d) = %v, want %v", attempt, got, want)
		}
		if d := b.Delay(attempt); d < 0 || d >= want {
			t.Fatalf("delay(%d) = %v outside [0,%v)", attempt, d, want)
		}
	}
	if got := b.Ceiling(1000); got != time.Minute {
		t.Fatalf("huge attempt not capped: %v", got)
	}
	zero := Backoff{Base: time.Second, Cap: time.Minute, Rand: func() float64 { return 0 }}
	if zero.Delay(5) != 0 {
		t.Fatal("full jitter must allow zero delay")
	}
}

func TestRetryStopsOnPermanentAndHonorsRetryAfter(t *testing.T) {
	ctx := context.Background()
	b := Backoff{Base: time.Millisecond, Cap: 2 * time.Millisecond}
	calls := 0
	err := Retry(ctx, b, 5, func(context.Context) error {
		calls++
		return errors.Join(ErrPermanent, errors.New("bad request"))
	})
	if err == nil || calls != 1 {
		t.Fatalf("permanent error retried: calls=%d err=%v", calls, err)
	}
	calls = 0
	start := time.Now()
	err = Retry(ctx, b, 3, func(context.Context) error {
		calls++
		if calls < 2 {
			return &RetryAfterError{Wait: 30 * time.Millisecond, Err: errors.New("429")}
		}
		return nil
	})
	if err != nil || calls != 2 {
		t.Fatalf("retry: calls=%d err=%v", calls, err)
	}
	if time.Since(start) < 30*time.Millisecond {
		t.Fatal("Retry-After not honored")
	}
}

func TestBR016RetryAfter(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	h := http.Header{}
	if RetryAfter(h, now) != 0 {
		t.Fatal("absent header")
	}
	h.Set("Retry-After", "7")
	if RetryAfter(h, now) != 7*time.Second {
		t.Fatal("seconds form")
	}
	h.Set("Retry-After", now.Add(90*time.Second).Format(http.TimeFormat))
	if got := RetryAfter(h, now); got != 90*time.Second {
		t.Fatalf("date form: %v", got)
	}
	l := NewLimiter(1000, time.Second, 10)
	l.Pause(40 * time.Millisecond)
	start := time.Now()
	if err := l.Wait(context.Background(), PriorityQuery); err != nil {
		t.Fatal(err)
	}
	if time.Since(start) < 35*time.Millisecond {
		t.Fatal("Pause not honored by Limiter")
	}
}

func TestBR016PriorityOrdering(t *testing.T) {
	// 20 tokens/s, burst 1: drain the bucket, queue queries first, then a cancel; the cancel must be
	// granted before the queries queued ahead of it.
	l := NewLimiter(20, time.Second, 1)
	ctx := context.Background()
	if err := l.Wait(ctx, PriorityQuery); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var order []string
	var wg sync.WaitGroup
	for range 3 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = l.Wait(ctx, PriorityQuery)
			mu.Lock()
			order = append(order, "query")
			mu.Unlock()
		}()
	}
	time.Sleep(10 * time.Millisecond)
	wg.Add(1)
	go func() {
		defer wg.Done()
		_ = l.Wait(ctx, PriorityCancel)
		mu.Lock()
		order = append(order, "cancel")
		mu.Unlock()
	}()
	wg.Wait()
	if order[0] != "cancel" {
		t.Fatalf("cancel not served first: %v", order)
	}
}

func TestLimiterRate(t *testing.T) {
	l := NewLimiter(100, time.Second, 1)
	ctx := context.Background()
	start := time.Now()
	for range 11 {
		if err := l.Wait(ctx, PrioritySubmit); err != nil {
			t.Fatal(err)
		}
	}
	if el := time.Since(start); el < 90*time.Millisecond {
		t.Fatalf("10 tokens at 100/s took only %v", el)
	}
	cctx, cancel := context.WithCancel(ctx)
	cancel()
	if err := NewLimiter(1, time.Hour, 1).Wait(cctx, PriorityQuery); err != nil {
		// first token is available immediately even with a cancelled context? burst=1 grants it.
		t.Logf("cancelled wait: %v", err)
	}
}

func TestMD008BreakerTransitions(t *testing.T) {
	now := time.Unix(0, 0)
	b := NewBreaker(3, 10*time.Second)
	b.now = func() time.Time { return now }
	var changes []string
	b.OnChange = func(from, to BreakerState) { changes = append(changes, from.String()+">"+to.String()) }
	fail := errors.New("x")
	for range 2 {
		if b.Allow() != nil {
			t.Fatal("closed breaker refused")
		}
		b.Record(fail)
	}
	if b.State() != BreakerClosed {
		t.Fatal("opened too early")
	}
	_ = b.Allow()
	b.Record(fail)
	if b.State() != BreakerOpen || !errors.Is(b.Allow(), ErrOpen) {
		t.Fatal("not open after threshold")
	}
	now = now.Add(10 * time.Second)
	if b.State() != BreakerHalfOpen {
		t.Fatal("not half-open after cooldown")
	}
	if b.Allow() != nil {
		t.Fatal("probe refused")
	}
	if !errors.Is(b.Allow(), ErrOpen) {
		t.Fatal("second concurrent probe allowed")
	}
	b.Record(fail)
	if b.State() != BreakerOpen {
		t.Fatal("failed probe must reopen")
	}
	now = now.Add(10 * time.Second)
	_ = b.Allow()
	b.Record(nil)
	if b.State() != BreakerClosed {
		t.Fatal("successful probe must close")
	}
	want := []string{"closed>open", "open>half_open", "half_open>open", "open>half_open", "half_open>closed"}
	if len(changes) != len(want) {
		t.Fatalf("changes %v", changes)
	}
	for i := range want {
		if changes[i] != want[i] {
			t.Fatalf("changes %v", changes)
		}
	}
}
