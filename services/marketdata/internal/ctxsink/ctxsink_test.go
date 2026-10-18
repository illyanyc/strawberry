package ctxsink

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	natsserver "github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/structpb"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	contextv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/context/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
)

func event(t *testing.T, key string, avail time.Time, value string) []byte {
	t.Helper()
	st, _ := structpb.NewStruct(map[string]any{"series_id": "CPIAUCSL", "value": value})
	b, err := proto.Marshal(&contextv1.ContextEvent{
		EventId: key + value, Kind: contextv1.ContextKind_CONTEXT_KIND_MACRO, Source: "alfred", RecordKey: key, Vintage: avail.Format("2006-01-02"),
		Provenance: &commonv1.Provenance{EventTime: time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC).UnixNano(), AvailableAt: avail.UnixNano()},
		Payload:    st,
	})
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// End to end over embedded JetStream: valid events reach the lake and are acked, a redelivered
// event (same Nats-Msg-Id) is deduplicated by the stream, and an invalid event is terminated
// without blocking the batch (CX-001, CX-003).
func TestCX003SinkPersistsContextFromBus(t *testing.T) {
	opts := &natsserver.Options{Host: "127.0.0.1", Port: -1, JetStream: true, StoreDir: t.TempDir(), NoLog: true, NoSigs: true}
	ns, err := natsserver.NewServer(opts)
	if err != nil {
		t.Fatal(err)
	}
	go ns.Start()
	defer ns.Shutdown()
	if !ns.ReadyForConnections(5 * time.Second) {
		t.Fatal("nats not ready")
	}
	nc, err := nats.Connect(ns.ClientURL())
	if err != nil {
		t.Fatal(err)
	}
	defer nc.Close()
	js, _ := jetstream.New(nc)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := bus.EnsureStreams(ctx, nc); err != nil {
		t.Fatal(err)
	}
	t0 := time.Date(2026, 9, 11, 12, 30, 0, 0, time.UTC)
	pub := func(id string, data []byte) {
		if _, err := js.Publish(ctx, "ctx.macro", data, jetstream.WithMsgID(id)); err != nil {
			t.Fatal(err)
		}
	}
	pub("a", event(t, "CPIAUCSL|2026-08-01", t0, "322.1"))
	pub("a", event(t, "CPIAUCSL|2026-08-01", t0, "322.1")) // producer retry: deduplicated by the stream
	pub("b", event(t, "CPIAUCSL|2026-08-01", t0.Add(30*24*time.Hour), "322.4"))
	pub("bad", []byte("not a proto"))

	l := lake.New(t.TempDir(), lake.NewMemoryManifest())
	s := New(l, slog.New(slog.NewTextHandler(io.Discard, nil)), nil)
	s.MaxWait = 200 * time.Millisecond
	runCtx, stop := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() { s.Run(runCtx, js); close(done) }()
	part := lake.ContextPartition("macro", time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC).UnixNano())
	deadline := time.Now().Add(10 * time.Second)
	var rows []lake.ContextRow
	for time.Now().Before(deadline) {
		rows, _ = l.ReadContext(part)
		if len(rows) >= 2 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	stop()
	<-done
	if len(rows) != 2 {
		t.Fatalf("want 2 vintages stored, got %d", len(rows))
	}
	cons, err := js.Consumer(ctx, "CONTEXT", Durable)
	if err != nil {
		t.Fatal(err)
	}
	info, _ := cons.Info(ctx)
	if info.NumAckPending != 0 || info.NumPending != 0 {
		t.Fatalf("messages left unacked: pending=%d ackPending=%d", info.NumPending, info.NumAckPending)
	}
	for _, r := range rows {
		if r.Vintage == "" || r.RecordKey != "CPIAUCSL|2026-08-01" || r.Source != "alfred" {
			t.Fatalf("row %+v", r)
		}
	}
}
