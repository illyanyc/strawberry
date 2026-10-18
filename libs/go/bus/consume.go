package bus

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
)

// Handler processes one message. Returning nil acks it; ErrPoison terminates it (never redelivered,
// counted); any other error naks it for redelivery after a delay (at-least-once, consumers are
// idempotent per OE-006).
type Handler func(ctx context.Context, m jetstream.Msg) error

// ErrPoison marks a message that can never be processed (e.g. undecodable payload).
var ErrPoison = errors.New("bus: poison message")

// ConsumerSpec describes one durable pull consumer.
type ConsumerSpec struct {
	Stream  string
	Durable string
	Filters []string
	// DeliverNew skips history (live-only consumers such as UI fan-out); default delivers all.
	DeliverNew bool
	// AckWait before redelivery; default 30 s.
	AckWait time.Duration
	// NakDelay before a failed message is redelivered; default 1 s.
	NakDelay time.Duration
}

// ConsumerMetrics counts consumer outcomes (label consumer).
type ConsumerMetrics struct {
	handled *prometheus.CounterVec
}

// NewConsumerMetrics registers tb_bus_consumed_total{consumer,outcome}.
func NewConsumerMetrics(reg prometheus.Registerer) *ConsumerMetrics {
	m := &ConsumerMetrics{handled: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_bus_consumed_total",
		Help: "Bus messages handled by durable consumers."}, []string{"consumer", "outcome"})}
	if reg != nil {
		reg.MustRegister(m.handled)
	}
	return m
}

func (m *ConsumerMetrics) inc(consumer, outcome string) {
	if m != nil {
		m.handled.WithLabelValues(consumer, outcome).Inc()
	}
}

// Consume runs a durable pull consumer until ctx ends, handling messages one at a time in stream
// order. It re-creates the consumer after errors with a short backoff.
func Consume(ctx context.Context, js jetstream.JetStream, spec ConsumerSpec, h Handler, log *slog.Logger, met *ConsumerMetrics) {
	if spec.AckWait == 0 {
		spec.AckWait = 30 * time.Second
	}
	if spec.NakDelay == 0 {
		spec.NakDelay = time.Second
	}
	for ctx.Err() == nil {
		if err := consumeOnce(ctx, js, spec, h, log, met); err != nil && ctx.Err() == nil {
			if log != nil {
				log.Warn("bus consumer restarting", "consumer", spec.Durable, "err", err)
			}
			select {
			case <-ctx.Done():
			case <-time.After(time.Second):
			}
		}
	}
}

func consumeOnce(ctx context.Context, js jetstream.JetStream, spec ConsumerSpec, h Handler, log *slog.Logger, met *ConsumerMetrics) error {
	cfg := jetstream.ConsumerConfig{Durable: spec.Durable, AckPolicy: jetstream.AckExplicitPolicy, AckWait: spec.AckWait,
		MaxAckPending: 1, DeliverPolicy: jetstream.DeliverAllPolicy}
	if spec.DeliverNew {
		cfg.DeliverPolicy = jetstream.DeliverNewPolicy
	}
	if len(spec.Filters) == 1 {
		cfg.FilterSubject = spec.Filters[0]
	} else {
		cfg.FilterSubjects = spec.Filters
	}
	c, err := js.CreateOrUpdateConsumer(ctx, spec.Stream, cfg)
	if err != nil {
		return err
	}
	for ctx.Err() == nil {
		batch, err := c.FetchNoWait(64)
		if err != nil {
			return err
		}
		n := 0
		for m := range batch.Messages() {
			n++
			herr := h(ctx, m)
			switch {
			case herr == nil:
				_ = m.Ack()
				met.inc(spec.Durable, "ok")
			case errors.Is(herr, ErrPoison):
				_ = m.Term()
				met.inc(spec.Durable, "poison")
				if log != nil {
					log.Error("bus: poison message terminated", "consumer", spec.Durable, "subject", m.Subject(), "err", herr)
				}
			default:
				_ = m.NakWithDelay(spec.NakDelay)
				met.inc(spec.Durable, "retry")
				if log != nil {
					log.Warn("bus: handler failed; will retry", "consumer", spec.Durable, "subject", m.Subject(), "err", herr)
				}
			}
		}
		if err := batch.Error(); err != nil && !errors.Is(err, jetstream.ErrNoMessages) {
			return err
		}
		if n == 0 {
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(20 * time.Millisecond):
			}
		}
	}
	return nil
}
