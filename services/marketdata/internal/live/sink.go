package live

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"time"

	"github.com/nats-io/nats.go/jetstream"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/cache"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

// Subjects (master "Bus subjects"; partial and correction subjects from section 05).
func FinalSubject(b *mdv1.Bar) string {
	return "md.bar." + lake.TimeframeName[b.GetTimeframe()] + "." + b.GetInstrumentId()
}
func PartialSubject(b *mdv1.Bar) string {
	return "md.bar.partial." + lake.TimeframeName[b.GetTimeframe()] + "." + b.GetInstrumentId()
}
func CorrectionSubject(b *mdv1.Bar) string {
	return "md.bar.correction." + lake.TimeframeName[b.GetTimeframe()] + "." + b.GetInstrumentId()
}

// BusSink publishes to JetStream (final and correction bars must be acknowledged; partials are
// fire-and-forget core NATS publishes because they are UI-only and may be dropped, MD-002).
type BusSink struct {
	JS jetstream.JetStream
}

// Final implements Sink.
func (s BusSink) Final(ctx context.Context, b *mdv1.Bar) error {
	return bus.Publish(ctx, s.JS, FinalSubject(b), MsgID(b), b)
}

// FinalBatch implements BatchSink: async publishes, then wait for every acknowledgement.
func (s BusSink) FinalBatch(ctx context.Context, bars []*mdv1.Bar) error {
	futs := make([]jetstream.PubAckFuture, 0, len(bars))
	var errs []error
	for _, b := range bars {
		msg, err := bus.NewMsg(ctx, FinalSubject(b), b)
		if err != nil {
			errs = append(errs, err)
			continue
		}
		f, err := s.JS.PublishMsgAsync(msg, jetstream.WithMsgID(MsgID(b)))
		if err != nil {
			errs = append(errs, err)
			continue
		}
		futs = append(futs, f)
	}
	for _, f := range futs {
		select {
		case <-f.Ok():
		case err := <-f.Err():
			errs = append(errs, err)
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return errors.Join(errs...)
}

// Correction implements Sink.
func (s BusSink) Correction(ctx context.Context, b *mdv1.Bar) error {
	return bus.Publish(ctx, s.JS, CorrectionSubject(b), MsgID(b), b)
}

// Partial implements Sink.
func (s BusSink) Partial(ctx context.Context, b *mdv1.Bar) {
	pctx, cancel := context.WithTimeout(ctx, 200*time.Millisecond)
	defer cancel()
	_ = bus.Publish(pctx, s.JS, PartialSubject(b), "", b)
}

// CacheSink keeps bar:last / bar:ring / md:watermark current (ST-009).
type CacheSink struct {
	C *cache.Client
}

// Final implements Sink.
func (s CacheSink) Final(ctx context.Context, b *mdv1.Bar) error {
	tf := lake.TimeframeName[b.GetTimeframe()]
	return errors.Join(s.C.PutBar(ctx, tf, b), s.C.SetWatermark(ctx, tf, b.GetFeed(), b.GetTsOpen()))
}

// Correction implements Sink.
func (s CacheSink) Correction(ctx context.Context, b *mdv1.Bar) error {
	return s.C.PutBar(ctx, lake.TimeframeName[b.GetTimeframe()], b)
}

// Partial implements Sink (partials are not cached).
func (CacheSink) Partial(context.Context, *mdv1.Bar) {}

// LakeSink buffers final bars and corrections and stages them every Interval (ST-004); partials are
// never written (DF-007).
type LakeSink struct {
	L        *lake.Lake
	Interval time.Duration
	Log      *slog.Logger
	mu       sync.Mutex
	buf      []*mdv1.Bar
}

// Final implements Sink.
func (s *LakeSink) Final(_ context.Context, b *mdv1.Bar) error {
	s.mu.Lock()
	s.buf = append(s.buf, b)
	s.mu.Unlock()
	return nil
}

// Correction implements Sink.
func (s *LakeSink) Correction(ctx context.Context, b *mdv1.Bar) error { return s.Final(ctx, b) }

// Partial implements Sink.
func (*LakeSink) Partial(context.Context, *mdv1.Bar) {}

// Flush stages buffered bars now.
func (s *LakeSink) Flush(ctx context.Context) error {
	s.mu.Lock()
	buf := s.buf
	s.buf = nil
	s.mu.Unlock()
	if len(buf) == 0 {
		return nil
	}
	if _, err := s.L.StageBars(ctx, buf); err != nil {
		s.mu.Lock()
		s.buf = append(buf, s.buf...)
		s.mu.Unlock()
		return err
	}
	return nil
}

// Run flushes every Interval until ctx ends, then flushes once more.
func (s *LakeSink) Run(ctx context.Context) {
	if s.Interval <= 0 {
		s.Interval = 30 * time.Second
	}
	t := time.NewTicker(s.Interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			_ = s.Flush(context.WithoutCancel(ctx))
			return
		case <-t.C:
			if err := s.Flush(ctx); err != nil && s.Log != nil {
				s.Log.Error("lake flush failed (will retry)", "err", err)
			}
		}
	}
}

// FinalBatch implements BatchSink for the cache (pipelined round trips are not needed at local
// latency; one write per bar).
func (s CacheSink) FinalBatch(ctx context.Context, bars []*mdv1.Bar) error {
	var errs []error
	for _, b := range bars {
		errs = append(errs, s.Final(ctx, b))
	}
	return errors.Join(errs...)
}

// Multi fans out to several sinks; the bus is first so publication latency is not delayed by the
// cache or lake buffer.
type Multi []Sink

// Final implements Sink.
func (m Multi) Final(ctx context.Context, b *mdv1.Bar) error {
	var errs []error
	for _, s := range m {
		errs = append(errs, s.Final(ctx, b))
	}
	return errors.Join(errs...)
}

// Correction implements Sink.
func (m Multi) Correction(ctx context.Context, b *mdv1.Bar) error {
	var errs []error
	for _, s := range m {
		errs = append(errs, s.Correction(ctx, b))
	}
	return errors.Join(errs...)
}

// FinalBatch implements BatchSink, using each sink's batch path when it has one.
func (m Multi) FinalBatch(ctx context.Context, bars []*mdv1.Bar) error {
	var errs []error
	for _, s := range m {
		if bs, ok := s.(BatchSink); ok {
			errs = append(errs, bs.FinalBatch(ctx, bars))
			continue
		}
		for _, b := range bars {
			errs = append(errs, s.Final(ctx, b))
		}
	}
	return errors.Join(errs...)
}

// Partial implements Sink.
func (m Multi) Partial(ctx context.Context, b *mdv1.Bar) {
	for _, s := range m {
		s.Partial(ctx, b)
	}
}
