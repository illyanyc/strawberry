// Package ctxsink persists context events from the bus (ctx.>, stream CONTEXT) to the lake's
// context dataset. marketdata is the lake's only writer (D4), so context-data publishes and this
// sink writes (ADR-0032). Messages are acked only after their rows are durable (ST-002), in batches
// so a backfill does not produce one staging file per event.
package ctxsink

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/protobuf/proto"

	contextv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/context/v1"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

// Durable is the consumer name on stream CONTEXT.
const Durable = "marketdata-context-lake"

// Stager is the lake write used by the sink (lake.Lake in production).
type Stager interface {
	StageContext(ctx context.Context, rows []lake.ContextRow) error
}

// Sink consumes ctx.> and stages rows.
type Sink struct {
	Lake      Stager
	Log       *slog.Logger
	Now       func() time.Time
	BatchSize int           // default 500
	MaxWait   time.Duration // default 1 s
	rows      *prometheus.CounterVec
}

// New builds a sink and registers tb_context_rows_total{kind,outcome}.
func New(l Stager, log *slog.Logger, reg prometheus.Registerer) *Sink {
	s := &Sink{Lake: l, Log: log, Now: time.Now, rows: prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "tb_context_rows_total", Help: "Context events persisted to the lake by kind and outcome."}, []string{"kind", "outcome"})}
	if reg != nil {
		reg.MustRegister(s.rows)
	}
	return s
}

func (s *Sink) count(kind, outcome string, n int) {
	if s.rows != nil {
		s.rows.WithLabelValues(kind, outcome).Add(float64(n))
	}
}

// Decode converts one message body into rows; ErrInvalidContext (wrapped) means poison.
func (s *Sink) Decode(data []byte) ([]lake.ContextRow, error) {
	var ev contextv1.ContextEvent
	if err := proto.Unmarshal(data, &ev); err != nil {
		return nil, errors.Join(lake.ErrInvalidContext, err)
	}
	return lake.ContextRowsFromEvent(&ev, s.Now().UnixNano())
}

// Run consumes until ctx ends, recreating the consumer after errors.
func (s *Sink) Run(ctx context.Context, js jetstream.JetStream) {
	for ctx.Err() == nil {
		if err := s.runOnce(ctx, js); err != nil && ctx.Err() == nil {
			s.Log.Warn("context sink restarting", "err", err)
			select {
			case <-ctx.Done():
			case <-time.After(time.Second):
			}
		}
	}
}

func (s *Sink) runOnce(ctx context.Context, js jetstream.JetStream) error {
	batch, wait := s.BatchSize, s.MaxWait
	if batch <= 0 {
		batch = 500
	}
	if wait <= 0 {
		wait = time.Second
	}
	c, err := js.CreateOrUpdateConsumer(ctx, "CONTEXT", jetstream.ConsumerConfig{
		Durable: Durable, AckPolicy: jetstream.AckExplicitPolicy, AckWait: time.Minute, MaxAckPending: 4 * batch,
		DeliverPolicy: jetstream.DeliverAllPolicy, FilterSubject: "ctx.>",
	})
	if err != nil {
		return err
	}
	for ctx.Err() == nil {
		msgs, err := c.Fetch(batch, jetstream.FetchMaxWait(wait))
		if err != nil {
			return err
		}
		var got []jetstream.Msg
		for m := range msgs.Messages() {
			got = append(got, m)
		}
		if err := msgs.Error(); err != nil && !errors.Is(err, jetstream.ErrNoMessages) && !errors.Is(err, context.DeadlineExceeded) {
			return err
		}
		if len(got) > 0 {
			s.Handle(ctx, got)
		}
	}
	return nil
}

// Message is the subset of jetstream.Msg the sink needs (tests use fakes).
type Message interface {
	Data() []byte
	Subject() string
	Ack() error
	Term() error
	NakWithDelay(time.Duration) error
}

// Handle stages one batch: invalid events are terminated (never redelivered), valid rows are
// written in one StageContext call and then acked; a write failure naks the batch for redelivery
// (duplicates are harmless: compaction drops unchanged versions, CX-003).
func (s *Sink) Handle(ctx context.Context, msgs []jetstream.Msg) {
	ms := make([]Message, len(msgs))
	for i, m := range msgs {
		ms[i] = m
	}
	s.HandleMessages(ctx, ms)
}

// HandleMessages is Handle over the Message interface.
func (s *Sink) HandleMessages(ctx context.Context, msgs []Message) {
	var rows []lake.ContextRow
	var ok []Message
	for _, m := range msgs {
		rs, err := s.Decode(m.Data())
		if err != nil {
			_ = m.Term()
			s.count("unknown", "poison", 1)
			s.Log.Error("context event rejected", "subject", m.Subject(), "err", err)
			continue
		}
		rows = append(rows, rs...)
		ok = append(ok, m)
	}
	if len(rows) == 0 {
		return
	}
	if err := s.Lake.StageContext(ctx, rows); err != nil {
		for _, m := range ok {
			_ = m.NakWithDelay(5 * time.Second)
		}
		s.count("all", "retry", len(ok))
		s.Log.Warn("context stage failed; will retry", "events", len(ok), "err", err)
		return
	}
	for _, m := range ok {
		_ = m.Ack()
	}
	perKind := map[string]int{}
	for _, r := range rows {
		perKind[r.Kind]++
	}
	for k, n := range perKind {
		s.count(k, "stored", n)
	}
}
