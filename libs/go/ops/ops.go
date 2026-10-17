// Package ops is the standard service template's operational surface (rule OP-010): an internal
// listener serving /healthz (liveness), /readyz (dependency checks), and /metrics (Prometheus),
// plus HTTP request metrics middleware and a `healthcheck` subcommand for shell-less images.
package ops

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Check reports whether one dependency is usable. It must return quickly (it runs under a timeout).
type Check func(ctx context.Context) error

// Server is the ops listener.
type Server struct {
	service  string
	registry *prometheus.Registry
	mu       sync.RWMutex
	checks   map[string]Check
	log      *slog.Logger
}

// NewServer creates the ops server and a Prometheus registry with Go/process collectors and a
// `tb_build_info` gauge.
func NewServer(service string, log *slog.Logger) *Server {
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	build := prometheus.NewGaugeVec(prometheus.GaugeOpts{Name: "tb_build_info", Help: "Build information."}, []string{"service", "version"})
	build.WithLabelValues(service, Version).Set(1)
	reg.MustRegister(build)
	return &Server{service: service, registry: reg, checks: map[string]Check{}, log: log}
}

// Version is set at build time with -ldflags "-X github.com/illyanyc/strawberry/libs/go/ops.Version=...".
var Version = "dev"

// Registry returns the service's Prometheus registry for custom metrics.
func (s *Server) Registry() *prometheus.Registry { return s.registry }

// AddCheck registers a readiness check.
func (s *Server) AddCheck(name string, c Check) {
	s.mu.Lock()
	s.checks[name] = c
	s.mu.Unlock()
}

// Handler returns the ops mux.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok", "service": s.service})
	})
	mux.HandleFunc("GET /readyz", s.readyz)
	mux.Handle("GET /metrics", promhttp.HandlerFor(s.registry, promhttp.HandlerOpts{Registry: s.registry}))
	return mux
}

func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	s.mu.RLock()
	defer s.mu.RUnlock()
	results := map[string]string{}
	ok := true
	for name, c := range s.checks {
		if err := c(ctx); err != nil {
			ok = false
			// Only the check name and a generic status leave the process; details go to the log.
			results[name] = "fail"
			s.log.WarnContext(ctx, "readiness check failed", "check", name, "error", err)
			continue
		}
		results[name] = "ok"
	}
	status := http.StatusOK
	if !ok {
		status = http.StatusServiceUnavailable
	}
	writeJSON(w, status, map[string]any{"ready": ok, "service": s.service, "checks": results})
}

// ListenAndServe serves the ops handler on addr until ctx is canceled.
func (s *Server) ListenAndServe(ctx context.Context, addr string) error {
	srv := &http.Server{Addr: addr, Handler: s.Handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() { //nolint:gosec // shutdown must outlive the canceled parent context
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("ops: %w", err)
	}
	return nil
}

// HTTPMetrics returns middleware recording tb_http_requests_total and tb_http_request_duration_seconds,
// labelled by route pattern (never raw path, to bound cardinality).
func (s *Server) HTTPMetrics() func(http.Handler) http.Handler {
	reqs := prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "tb_http_requests_total", Help: "HTTP requests by route, method, and status code.",
	}, []string{"route", "method", "code"})
	dur := prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Name: "tb_http_request_duration_seconds", Help: "HTTP request latency.", Buckets: prometheus.DefBuckets,
	}, []string{"route", "method"})
	s.registry.MustRegister(reqs, dur)
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rw := &statusWriter{ResponseWriter: w, code: http.StatusOK}
			next.ServeHTTP(rw, r)
			route := r.Pattern
			if route == "" {
				route = "unmatched"
			}
			reqs.WithLabelValues(route, r.Method, strconv.Itoa(rw.code)).Inc()
			dur.WithLabelValues(route, r.Method).Observe(time.Since(start).Seconds())
		})
	}
}

type statusWriter struct {
	http.ResponseWriter
	code int
}

func (w *statusWriter) WriteHeader(code int) {
	w.code = code
	w.ResponseWriter.WriteHeader(code)
}

func (w *statusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// RunHealthcheckIfRequested implements `<binary> healthcheck [url]` for Docker HEALTHCHECK in
// images without a shell: exits 0 if url (default http://127.0.0.1:8081/healthz) returns 200.
// It returns normally when the subcommand was not requested.
func RunHealthcheckIfRequested() {
	if len(os.Args) < 2 || os.Args[1] != "healthcheck" {
		return
	}
	url := "http://127.0.0.1:8081/healthz"
	if len(os.Args) > 2 {
		url = os.Args[2]
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(url) //nolint:noctx,gosec // one-shot local probe; URL comes from the container's own HEALTHCHECK
	if err != nil {
		os.Exit(1)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		os.Exit(1)
	}
	os.Exit(0)
}
