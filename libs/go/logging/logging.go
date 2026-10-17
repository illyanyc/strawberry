// Package logging provides the platform's structured JSON logger: every record carries `service`,
// `correlation_id` and `trace_id` (when present in the context), and passes through a redacting
// handler so secrets never reach log output (rule SE-012).
package logging

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"

	"go.opentelemetry.io/otel/trace"
)

type ctxKey struct{}

// WithCorrelationID returns a context carrying the correlation ID used in every log line.
func WithCorrelationID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, ctxKey{}, id)
}

// CorrelationID returns the correlation ID stored in ctx, or "".
func CorrelationID(ctx context.Context) string {
	if v, ok := ctx.Value(ctxKey{}).(string); ok {
		return v
	}
	return ""
}

// New builds a JSON logger for service at level ("debug", "info", "warn", "error") writing to w
// (os.Stdout when nil).
func New(service, level string, w io.Writer) *slog.Logger {
	if w == nil {
		w = os.Stdout
	}
	var lv slog.Level
	if err := lv.UnmarshalText([]byte(level)); err != nil {
		lv = slog.LevelInfo
	}
	base := slog.NewJSONHandler(w, &slog.HandlerOptions{Level: lv})
	return slog.New(&handler{next: base}).With(slog.String("service", service))
}

type handler struct {
	next slog.Handler
}

func (h *handler) Enabled(ctx context.Context, l slog.Level) bool { return h.next.Enabled(ctx, l) }

func (h *handler) Handle(ctx context.Context, r slog.Record) error {
	out := slog.NewRecord(r.Time, r.Level, Redact(r.Message), r.PC)
	r.Attrs(func(a slog.Attr) bool {
		out.AddAttrs(redactAttr(a))
		return true
	})
	if id := CorrelationID(ctx); id != "" {
		out.AddAttrs(slog.String("correlation_id", id))
	}
	if sc := trace.SpanContextFromContext(ctx); sc.HasTraceID() {
		out.AddAttrs(slog.String("trace_id", sc.TraceID().String()))
	}
	return h.next.Handle(ctx, out)
}

func (h *handler) WithAttrs(attrs []slog.Attr) slog.Handler {
	red := make([]slog.Attr, len(attrs))
	for i, a := range attrs {
		red[i] = redactAttr(a)
	}
	return &handler{next: h.next.WithAttrs(red)}
}

func (h *handler) WithGroup(name string) slog.Handler { return &handler{next: h.next.WithGroup(name)} }

func redactAttr(a slog.Attr) slog.Attr {
	if IsSensitiveKey(a.Key) {
		return slog.String(a.Key, Redacted)
	}
	v := a.Value.Resolve()
	switch v.Kind() {
	case slog.KindString:
		return slog.String(a.Key, Redact(v.String()))
	case slog.KindGroup:
		group := v.Group()
		red := make([]any, len(group))
		for i, g := range group {
			red[i] = redactAttr(g)
		}
		return slog.Group(a.Key, red...)
	case slog.KindAny:
		switch x := v.Any().(type) {
		case error:
			return slog.String(a.Key, Redact(x.Error()))
		case fmt.Stringer:
			return slog.String(a.Key, Redact(x.String()))
		case []byte:
			return slog.String(a.Key, Redact(string(x)))
		default:
			return slog.String(a.Key, Redact(fmt.Sprintf("%+v", x)))
		}
	default:
		return slog.Attr{Key: a.Key, Value: v}
	}
}
