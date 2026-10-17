// Package bus wraps NATS JetStream (D2): authenticated connections, the platform's stream
// definitions (rule OP-030), and proto publishing with correlation and dedupe headers.
package bus

import (
	"context"
	"fmt"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"google.golang.org/protobuf/proto"

	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/secrets"
)

// Config locates the NATS server; the password comes from secret nats_<user>_password.
type Config struct {
	URL  string `env:"TB_NATS_URL" envDefault:"nats://nats:4222" json:"nats_url"`
	User string `env:"TB_NATS_USER" json:"nats_user"`
}

// Connect opens an authenticated connection that reconnects forever with backoff.
func Connect(c Config, store *secrets.Store, name string) (*nats.Conn, error) {
	pw, err := store.GetString("nats_" + c.User + "_password")
	if err != nil {
		return nil, err
	}
	nc, err := nats.Connect(c.URL,
		nats.Name(name),
		nats.UserInfo(c.User, pw),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(time.Second),
		nats.RetryOnFailedConnect(true),
		nats.Timeout(5*time.Second),
	)
	if err != nil {
		return nil, fmt.Errorf("bus: connect: %w", err)
	}
	return nc, nil
}

// Ready is an ops readiness check: the connection must be established.
func Ready(nc *nats.Conn) func(context.Context) error {
	return func(context.Context) error {
		if !nc.IsConnected() {
			return fmt.Errorf("bus: not connected (%s)", nc.Status())
		}
		return nil
	}
}

// Streams are the platform's JetStream streams. Order-path subjects are durable file streams
// (at-least-once, consumers idempotent per OE-006); market data is memory-backed with short retention.
var Streams = []jetstream.StreamConfig{
	{Name: "ORDERS", Subjects: []string{"ord.>", "exec.>", "risk.kill"}, Storage: jetstream.FileStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: 90 * 24 * time.Hour, Replicas: 1, Duplicates: 10 * time.Minute},
	{Name: "LEDGER", Subjects: []string{"acct.snapshot.>", "ledger.update.>", "recon.result.>", "recon.request.>"}, Storage: jetstream.FileStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: 90 * 24 * time.Hour, Replicas: 1, Duplicates: 10 * time.Minute},
	{Name: "SIGNALS", Subjects: []string{"sig.>"}, Storage: jetstream.FileStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: 30 * 24 * time.Hour, Replicas: 1},
	{Name: "ALERTS", Subjects: []string{"alert.>"}, Storage: jetstream.FileStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: 30 * 24 * time.Hour, Replicas: 1},
	{Name: "CONTEXT", Subjects: []string{"ctx.>"}, Storage: jetstream.FileStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: 7 * 24 * time.Hour, Replicas: 1},
	{Name: "MARKETDATA", Subjects: []string{"md.>", "feat.>"}, Storage: jetstream.MemoryStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: time.Hour, MaxBytes: 512 << 20, Discard: jetstream.DiscardOld, Replicas: 1},
	{Name: "SYS", Subjects: []string{"sys.>"}, Storage: jetstream.MemoryStorage,
		Retention: jetstream.LimitsPolicy, MaxAge: 10 * time.Minute, Replicas: 1},
}

// EnsureStreams creates or updates every stream in Streams.
func EnsureStreams(ctx context.Context, nc *nats.Conn) error {
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}
	for _, s := range Streams {
		if _, err := js.CreateOrUpdateStream(ctx, s); err != nil {
			return fmt.Errorf("bus: stream %s: %w", s.Name, err)
		}
	}
	return nil
}

// Publish sends msg on subject through JetStream with Nats-Msg-Id = dedupeID (when set) and the
// context's correlation ID as a header.
func Publish(ctx context.Context, js jetstream.JetStream, subject, dedupeID string, msg proto.Message) error {
	b, err := proto.Marshal(msg)
	if err != nil {
		return err
	}
	m := nats.NewMsg(subject)
	m.Data = b
	if id := logging.CorrelationID(ctx); id != "" {
		m.Header.Set("Tb-Correlation-Id", id)
	}
	opts := []jetstream.PublishOpt{}
	if dedupeID != "" {
		opts = append(opts, jetstream.WithMsgID(dedupeID))
	}
	if _, err := js.PublishMsg(ctx, m, opts...); err != nil {
		return fmt.Errorf("bus: publish %s: %w", subject, err)
	}
	return nil
}
