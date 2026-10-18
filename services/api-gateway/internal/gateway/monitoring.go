package gateway

import (
	"context"
	"net/http"
	"strconv"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	monitorv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/monitor/v1"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// MonitorClients is the monitor's MonitorService behind the Monitoring page (section 13 Phase 5,
// ADR-0045). Nil answers 503.
type MonitorClients struct {
	Monitor monitorv1.MonitorServiceClient
}

// DialMonitor connects to the monitor with a service token per call (SE-040, audience monitor).
func DialMonitor(cfg Config, signer *svcauth.Signer) (*MonitorClients, func(), error) {
	c, err := grpc.NewClient(cfg.MonitorAddr, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithUnaryInterceptor(func(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, inv grpc.UnaryInvoker, opts ...grpc.CallOption) error {
			return inv(ctx, method, req, reply, cc, append(opts, signer.PerRPC("monitor"))...)
		}))
	if err != nil {
		return nil, func() {}, err
	}
	return &MonitorClients{Monitor: monitorv1.NewMonitorServiceClient(c)}, func() { _ = c.Close() }, nil
}

func (s *Server) registerMonitoringRoutes(priv func(string, string, func(http.ResponseWriter, *http.Request, *reqCtx))) {
	priv("GET", "/api/v1/monitoring/strategies", s.handleMonStrategies)
	priv("GET", "/api/v1/monitoring/strategies/{id}", s.handleMonHealth)
	priv("GET", "/api/v1/monitoring/strategies/{id}/{report}", s.handleMonReport)
	priv("GET", "/api/v1/monitoring/alerts", s.handleMonAlerts)
	priv("POST", "/api/v1/monitoring/alerts/{key}/ack", s.handleMonAck)
	priv("POST", "/api/v1/monitoring/alerts/{key}/silence", s.handleMonSilence)
	priv("GET", "/api/v1/monitoring/daily-reports", s.handleMonDaily)
	priv("GET", "/api/v1/monitoring/containment/actions", s.handleMonActions)
	priv("GET", "/api/v1/monitoring/containment/policies/{id}", s.handleMonPolicy)
	priv("PUT", "/api/v1/monitoring/containment/policies/{id}", s.handleMonPolicyPut)
}

func (s *Server) needMonitor(w http.ResponseWriter) bool {
	if s.monitor == nil || s.monitor.Monitor == nil {
		writeError(w, http.StatusServiceUnavailable, "monitor_unavailable", "monitor unavailable")
		return false
	}
	return true
}

// mcall authorizes, runs one unary monitor call, and writes the protojson response.
func (s *Server) mcall(w http.ResponseWriter, r *http.Request, rc *reqCtx, act policy.Action, res policy.Resource,
	fn func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error)) {
	if !s.authorize(w, rc, act, res) || !s.needMonitor(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	m, err := fn(ctx, s.monitor.Monitor)
	if err != nil {
		s.upstreamError(w, r, "monitor", err)
		return
	}
	writeProto(w, http.StatusOK, m)
}

func (s *Server) handleMonStrategies(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.ListStrategyHealth(ctx, &monitorv1.ListStrategyHealthRequest{})
	})
}

func (s *Server) handleMonHealth(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{ID: id}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.GetStrategyHealth(ctx, &monitorv1.GetStrategyHealthRequest{StrategyId: id})
	})
}

// handleMonReport serves the per-strategy reports: performance, drift, divergence (?date=),
// execution, feedback, graduation (section 15 evidence, MN-040), actions.
func (s *Server) handleMonReport(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	rep := r.PathValue("report")
	calls := map[string]func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error){
		"performance": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.GetPerformanceReport(ctx, &monitorv1.GetPerformanceReportRequest{StrategyId: id})
		},
		"drift": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.GetDriftReport(ctx, &monitorv1.GetDriftReportRequest{StrategyId: id})
		},
		"divergence": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.GetDivergenceReport(ctx, &monitorv1.GetDivergenceReportRequest{StrategyId: id, SessionDate: r.URL.Query().Get("date")})
		},
		"execution": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.GetExecutionQuality(ctx, &monitorv1.GetExecutionQualityRequest{StrategyId: id})
		},
		"feedback": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.GetLiveFeedback(ctx, &monitorv1.GetLiveFeedbackRequest{StrategyId: id})
		},
		"graduation": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.GetGraduationEvidence(ctx, &monitorv1.GetGraduationEvidenceRequest{StrategyId: id})
		},
		"actions": func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
			return m.ListContainmentActions(ctx, &monitorv1.ListContainmentActionsRequest{StrategyId: id, Limit: 100})
		},
	}
	fn, known := calls[rep]
	if !known {
		writeError(w, http.StatusNotFound, "not_found", "unknown report")
		return
	}
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{ID: id}, fn)
}

func (s *Server) handleMonAlerts(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	q := r.URL.Query()
	since, _ := strconv.ParseInt(q.Get("since"), 10, 64)
	limit, _ := strconv.ParseInt(q.Get("limit"), 10, 32)
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.ListAlerts(ctx, &monitorv1.ListAlertsRequest{Severity: q.Get("severity"), Status: q.Get("status"), Since: since, Limit: int32(limit)})
	})
}

func (s *Server) handleMonAck(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	key := r.PathValue("key")
	var b struct {
		Note string `json:"note"`
	}
	_ = decode(r, &b)
	s.mcall(w, r, rc, policy.ActAlertAck, policy.Resource{ID: key}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.AckAlert(ctx, &monitorv1.AckAlertRequest{DedupeKey: key, Actor: s.execActor(r, rc), Note: truncate(b.Note, 300)})
	})
}

// handleMonSilence creates an Alertmanager silence (MN-042): passkey step-up, at most 24 h, a reason.
func (s *Server) handleMonSilence(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	key := r.PathValue("key")
	var b struct {
		DurationSeconds int64  `json:"durationSeconds"`
		Reason          string `json:"reason"`
	}
	if err := decode(r, &b); err != nil || b.DurationSeconds <= 0 || b.DurationSeconds > 86_400 || b.Reason == "" || len(b.Reason) > 300 {
		writeError(w, http.StatusBadRequest, "bad_request", "durationSeconds (1..86400) and a reason (1-300 characters) are required")
		return
	}
	s.mcall(w, r, rc, policy.ActAlertSilence, policy.Resource{ID: key}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.SilenceAlert(ctx, &monitorv1.SilenceAlertRequest{DedupeKey: key, DurationSeconds: b.DurationSeconds, Reason: b.Reason, Actor: s.execActor(r, rc)})
	})
}

func (s *Server) handleMonDaily(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.GetDailyReport(ctx, &monitorv1.GetDailyReportRequest{Date: r.URL.Query().Get("date")})
	})
}

func (s *Server) handleMonActions(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.ListContainmentActions(ctx, &monitorv1.ListContainmentActionsRequest{StrategyId: r.URL.Query().Get("strategy_id"), Limit: 200})
	})
}

func policyID(id string) string {
	if id == "default" {
		return ""
	}
	return id
}

func (s *Server) handleMonPolicy(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.mcall(w, r, rc, policy.ActMonitorRead, policy.Resource{ID: id}, func(ctx context.Context, m monitorv1.MonitorServiceClient) (proto.Message, error) {
		return m.GetContainmentPolicy(ctx, &monitorv1.GetContainmentPolicyRequest{StrategyId: policyID(id)})
	})
}

// handleMonPolicyPut saves a containment policy. The monitor decides whether the change loosens it
// (MN-036); when it does and the session has no fresh passkey step-up, the policy answer for the
// loosening (require_step_up) goes back to the UI, which retries after the step-up.
func (s *Server) handleMonPolicyPut(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var req monitorv1.SetContainmentPolicyRequest
	if err := decodeProto(r, &req); err != nil || req.GetPolicy() == nil {
		writeError(w, http.StatusBadRequest, "bad_request", "a policy is required")
		return
	}
	if !s.authorize(w, rc, policy.ActContainmentSet, policy.Resource{ID: id, Mode: "lower"}) || !s.needMonitor(w) {
		return
	}
	req.Policy.StrategyId, req.Actor = policyID(id), s.execActor(r, rc)
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.monitor.Monitor.SetContainmentPolicy(ctx, &req)
	if status.Code(err) == codes.PermissionDenied {
		if s.authorize(w, rc, policy.ActContainmentSet, policy.Resource{ID: id, Mode: "raise"}) {
			writeError(w, http.StatusForbidden, "forbidden", status.Convert(err).Message())
		}
		return
	}
	if err != nil {
		s.upstreamError(w, r, "monitor", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}
