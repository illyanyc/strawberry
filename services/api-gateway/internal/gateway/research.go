package gateway

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/oklog/ulid/v2"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	mlv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ml/v1"
	optv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// ResearchClients are the ML, optimizer, and holdout services behind the training, experiments, and
// tearsheet pages (section 14 Phase 4 slice, UI-023..UI-027). Any may be nil; routes then answer 503.
type ResearchClients struct {
	ML   mlv1.MLServiceClient
	Opt  optv1.OptimizerServiceClient
	Hold optv1.HoldoutServiceClient
}

// DialResearch connects to ml, optimizer, and the holdout service with a service token per call
// (SE-040; the holdout service has its own audience, EX-012). Connections are lazy.
func DialResearch(cfg Config, signer *svcauth.Signer) (*ResearchClients, func(), error) {
	var conns []*grpc.ClientConn
	closeAll := func() {
		for _, c := range conns {
			_ = c.Close()
		}
	}
	dial := func(addr, audience string) (*grpc.ClientConn, error) {
		c, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()),
			grpc.WithUnaryInterceptor(func(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, inv grpc.UnaryInvoker, opts ...grpc.CallOption) error {
				return inv(ctx, method, req, reply, cc, append(opts, signer.PerRPC(audience))...)
			}),
			grpc.WithStreamInterceptor(func(ctx context.Context, desc *grpc.StreamDesc, cc *grpc.ClientConn, method string, st grpc.Streamer, opts ...grpc.CallOption) (grpc.ClientStream, error) {
				return st(ctx, desc, cc, method, append(opts, signer.PerRPC(audience))...)
			}))
		if err == nil {
			conns = append(conns, c)
		}
		return c, err
	}
	ml, err := dial(cfg.MLAddr, "ml")
	if err != nil {
		return nil, closeAll, err
	}
	opt, err := dial(cfg.OptimizerAddr, "optimizer")
	if err != nil {
		closeAll()
		return nil, func() {}, err
	}
	hold, err := dial(cfg.HoldoutAddr, "holdout")
	if err != nil {
		closeAll()
		return nil, func() {}, err
	}
	return &ResearchClients{ML: mlv1.NewMLServiceClient(ml), Opt: optv1.NewOptimizerServiceClient(opt),
		Hold: optv1.NewHoldoutServiceClient(hold)}, closeAll, nil
}

func (s *Server) registerResearchRoutes(priv func(string, string, func(http.ResponseWriter, *http.Request, *reqCtx))) {
	// Training (section 10).
	priv("GET", "/api/v1/research/families", s.handleFamilies)
	priv("GET", "/api/v1/research/feature-sets", s.handleFeatureSets)
	priv("GET", "/api/v1/research/label-presets", s.handleLabelPresets)
	priv("POST", "/api/v1/research/training/estimate", s.handleTrainingEstimate)
	priv("POST", "/api/v1/research/training/jobs", s.handleTrainingSubmit)
	priv("GET", "/api/v1/research/training/jobs", s.handleTrainingJobs)
	priv("GET", "/api/v1/research/training/jobs/{id}", s.handleTrainingJob)
	priv("POST", "/api/v1/research/training/jobs/{id}/cancel", s.handleTrainingCancel)
	priv("GET", "/api/v1/research/training/jobs/{id}/stream", s.handleTrainingStream)
	priv("GET", "/api/v1/research/runs", s.handleRuns)
	priv("GET", "/api/v1/research/runs/{id}", s.handleRun)
	priv("GET", "/api/v1/research/runs/{id}/diagnostics", s.handleRunDiagnostics)
	priv("GET", "/api/v1/research/runs/{id}/model-card", s.handleRunCard)
	priv("GET", "/api/v1/research/runs/{id}/importance", s.handleRunImportance)
	priv("GET", "/api/v1/research/runs/{id}/tearsheet", s.handleRunTearsheet)
	priv("POST", "/api/v1/research/runs/{id}/register", s.handleRunRegister)
	priv("POST", "/api/v1/research/runs/{id}/ablations", s.handleRunAblations)
	// Experiments, lineages, holdout, registry (section 11).
	priv("GET", "/api/v1/research/lineages", s.handleLineages)
	priv("POST", "/api/v1/research/lineages", s.handleLineageCreate)
	priv("GET", "/api/v1/research/lineages/{id}", s.handleLineage)
	priv("PUT", "/api/v1/research/lineages/{id}/autonomy", s.handleLineageAutonomy)
	priv("POST", "/api/v1/research/lineages/{id}/cycles", s.handleLineageCycle)
	priv("GET", "/api/v1/research/lineages/{id}/ledger", s.handleLineageLedger)
	priv("GET", "/api/v1/research/lineages/{id}/stats", s.handleLineageStats)
	priv("GET", "/api/v1/research/lineages/{id}/holdout", s.handleHoldoutStatus)
	priv("POST", "/api/v1/research/lineages/{id}/holdout/boundary", s.handleHoldoutBoundary)
	priv("PUT", "/api/v1/research/lineages/{id}/retraining", s.handleRetrainingPolicy)
	priv("POST", "/api/v1/research/lineages/{id}/retrain", s.handleRetrain)
	priv("GET", "/api/v1/research/studies", s.handleStudies)
	priv("POST", "/api/v1/research/studies", s.handleStudyCreate)
	priv("GET", "/api/v1/research/studies/{id}", s.handleStudy)
	priv("POST", "/api/v1/research/studies/{id}/{action}", s.handleStudyAction)
	priv("GET", "/api/v1/research/studies/{id}/trials", s.handleStudyTrials)
	priv("GET", "/api/v1/research/studies/{id}/diagnostics", s.handleStudyDiagnostics)
	priv("GET", "/api/v1/research/studies/{id}/stream", s.handleStudyStream)
	priv("GET", "/api/v1/research/candidates", s.handleCandidates)
	priv("GET", "/api/v1/research/candidates/{id}", s.handleCandidate)
	priv("GET", "/api/v1/research/candidates/{id}/gate-report", s.handleGateReport)
	priv("GET", "/api/v1/research/candidates/{id}/evidence", s.handleEvidence)
	priv("GET", "/api/v1/research/candidates/{id}/graph", s.handleCandidateGraph)
	priv("POST", "/api/v1/research/candidates/{id}/check", s.handleCandidateCheck)
	priv("POST", "/api/v1/research/candidates/{id}/promote", s.handleCandidatePromote)
	priv("POST", "/api/v1/research/candidates/{id}/demote", s.handleCandidateDemote)
	priv("POST", "/api/v1/research/candidates/{id}/holdout", s.handleHoldoutEvaluate)
}

// ---- helpers ----

var errBadAction = status.Error(codes.InvalidArgument, "action is start, pause, resume, or stop")

func (s *Server) needResearch(w http.ResponseWriter, which string) bool {
	if s.research == nil || (which == "ml" && s.research.ML == nil) || (which == "optimizer" && s.research.Opt == nil) ||
		(which == "holdout" && s.research.Hold == nil) {
		writeError(w, http.StatusServiceUnavailable, which+"_unavailable", which+" unavailable")
		return false
	}
	return true
}

// researchCaller is the owner as forwarded to ml/optimizer/holdout. The step-up attestation is set
// only while the session's passkey step-up is fresh; the services re-check its age (EX-011, EX-030).
func (s *Server) researchCaller(rc *reqCtx) *mlv1.CallerContext {
	c := &mlv1.CallerContext{Actor: mlv1.Actor_ACTOR_OWNER, ActorId: rc.session.OwnerID,
		CorrelationId: ulid.MustNew(ulid.Timestamp(s.now()), ulid.DefaultEntropy()).String()}
	if rc.session.StepUpUntil != nil && s.now().Before(*rc.session.StepUpUntil) && rc.session.StepUpMethod == string(policy.StepUpPasskey) {
		c.StepUp = &mlv1.StepUp{Method: rc.session.StepUpMethod, VerifiedAt: s.now().UnixNano(), SessionId: truncate(fmt.Sprintf("%x", rc.session.IDHash), 12)}
	}
	return c
}

func researchCtx(r *http.Request) (context.Context, context.CancelFunc) {
	return context.WithTimeout(r.Context(), 15*time.Second)
}

// rcall authorizes, runs one unary research call, and writes the protojson response.
func (s *Server) rcall(w http.ResponseWriter, r *http.Request, rc *reqCtx, svc string, act policy.Action, res policy.Resource,
	fn func(ctx context.Context) (proto.Message, error)) {
	if !s.authorize(w, rc, act, res) || !s.needResearch(w, svc) {
		return
	}
	ctx, cancel := researchCtx(r)
	defer cancel()
	m, err := fn(ctx)
	if err != nil {
		s.upstreamError(w, r, svc, err)
		return
	}
	writeProto(w, http.StatusOK, m)
}

func (s *Server) pathID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid id")
		return "", false
	}
	return id, true
}

var stageNames = map[string]mlv1.RegistryStage{
	"validated": mlv1.RegistryStage_REGISTRY_STAGE_VALIDATED, "shadow": mlv1.RegistryStage_REGISTRY_STAGE_SHADOW,
	"paper": mlv1.RegistryStage_REGISTRY_STAGE_PAPER, "live_eligible": mlv1.RegistryStage_REGISTRY_STAGE_LIVE_ELIGIBLE,
	"retired": mlv1.RegistryStage_REGISTRY_STAGE_RETIRED,
}

func (s *Server) readResearch(w http.ResponseWriter, r *http.Request, rc *reqCtx, svc string, fn func(ctx context.Context) (proto.Message, error)) {
	s.rcall(w, r, rc, svc, policy.ActResearchRead, policy.Resource{}, fn)
}

// ---- training ----

func (s *Server) handleFamilies(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.ListModelFamilies(ctx, &mlv1.ListModelFamiliesRequest{})
	})
}

func (s *Server) handleFeatureSets(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.ListFeatureSets(ctx, &mlv1.ListFeatureSetsRequest{})
	})
}

func (s *Server) handleLabelPresets(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.ListLabelPresets(ctx, &mlv1.ListLabelPresetsRequest{})
	})
}

func (s *Server) handleTrainingEstimate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var cfg mlv1.TrainingConfig
	if err := decodeProto(r, &cfg); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	s.rcall(w, r, rc, "ml", policy.ActResearchRead, policy.Resource{}, func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.EstimateTrainingJob(ctx, &mlv1.EstimateTrainingJobRequest{Config: &cfg, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleTrainingSubmit(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req mlv1.SubmitTrainingJobRequest
	if err := decodeProto(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	req.Caller = s.researchCaller(rc)
	s.rcall(w, r, rc, "ml", policy.ActResearchLaunch, policy.Resource{ID: req.GetConfig().GetFamily()}, func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.SubmitTrainingJob(ctx, &req)
	})
}

func (s *Server) handleTrainingJobs(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	lin, ok := qID(r, "lineage_id")
	if !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid lineage_id")
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.ListJobs(ctx, &mlv1.ListJobsRequest{LineageId: lin, Limit: qInt(r, "limit", 50, 200)})
	})
}

func (s *Server) handleTrainingJob(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.GetJob(ctx, &mlv1.GetJobRequest{JobId: id})
	})
}

func (s *Server) handleTrainingCancel(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.rcall(w, r, rc, "ml", policy.ActResearchLaunch, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.CancelJob(ctx, &mlv1.CancelJobRequest{JobId: id, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleRuns(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	lin, ok := qID(r, "lineage_id")
	fam, ok2 := qID(r, "family")
	if !ok || !ok2 {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid filter")
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.ListRuns(ctx, &mlv1.ListRunsRequest{LineageId: lin, Family: fam, Limit: qInt(r, "limit", 50, 200)})
	})
}

func (s *Server) handleRun(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.GetRun(ctx, &mlv1.GetRunRequest{RunId: id})
	})
}

func (s *Server) handleRunDiagnostics(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.GetDiagnostics(ctx, &mlv1.GetDiagnosticsRequest{RunId: id, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleRunCard(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.GetModelCard(ctx, &mlv1.GetModelCardRequest{RunId: id})
	})
}

func (s *Server) handleRunImportance(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	method := r.URL.Query().Get("method")
	if !ok {
		return
	}
	if method == "" {
		method = "gain"
	}
	if method != "gain" && method != "permutation" && method != "shap" {
		writeError(w, http.StatusBadRequest, "bad_request", "method is gain, permutation, or shap")
		return
	}
	s.readResearch(w, r, rc, "ml", func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.GetFeatureImportance(ctx, &mlv1.GetFeatureImportanceRequest{RunId: id, Method: method, Caller: s.researchCaller(rc)})
	})
}

// tearsheetCSP lets the static tearsheet render its inline styles and SVG but never run scripts or
// load anything (UI-025): it is opened as its own document, never inlined into the SPA.
const tearsheetCSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox; frame-ancestors 'none'"

func (s *Server) handleRunTearsheet(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok || !s.authorize(w, rc, policy.ActResearchRead, policy.Resource{}) || !s.needResearch(w, "ml") {
		return
	}
	ctx, cancel := researchCtx(r)
	defer cancel()
	resp, err := s.research.ML.GetTearsheet(ctx, &mlv1.GetTearsheetRequest{RunId: id})
	if err != nil {
		s.upstreamError(w, r, "ml", err)
		return
	}
	csp := tearsheetCSP
	if r.URL.Query().Get("embed") == "1" {
		// UI-047: the in-app viewer frames the document from this origin only; the CSP sandbox
		// (no scripts, opaque origin) and the viewer's <iframe sandbox=""> both still apply.
		csp = tearsheetEmbedCSP
		w.Header().Set("X-Frame-Options", "SAMEORIGIN")
	}
	w.Header().Set("Content-Security-Policy", csp)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	_, _ = io.WriteString(w, resp.GetHtml())
}

const tearsheetEmbedCSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox; frame-ancestors 'self'"

func (s *Server) handleRunRegister(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var req mlv1.RegisterModelRequest
	if err := decodeProto(r, &req); err != nil || !idRe.MatchString(req.GetName()) {
		writeError(w, http.StatusBadRequest, "bad_request", "name is required (letters, digits, - and _)")
		return
	}
	s.rcall(w, r, rc, "ml", policy.ActResearchLaunch, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.RegisterModel(ctx, &mlv1.RegisterModelRequest{RunId: id, Name: req.GetName(), Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleRunAblations(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var req mlv1.SubmitAblationRequest
	if err := decodeProto(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	req.RunId, req.Caller = id, s.researchCaller(rc)
	s.rcall(w, r, rc, "ml", policy.ActResearchLaunch, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.ML.SubmitAblation(ctx, &req)
	})
}

// ---- lineages, cycles, holdout ----

func (s *Server) handleLineages(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.ListLineages(ctx, &optv1.ListLineagesRequest{})
	})
}

func (s *Server) handleLineageCreate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var lin optv1.Lineage
	if err := decodeProto(r, &lin); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActResearchLaunch, policy.Resource{ID: lin.GetName()}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.CreateLineage(ctx, &optv1.CreateLineageRequest{Lineage: &lin, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleLineage(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetLineage(ctx, &optv1.GetLineageRequest{LineageId: id})
	})
}

// handleLineageAutonomy raises (passkey step-up) or lowers the analyst's autonomy (EX-033).
func (s *Server) handleLineageAutonomy(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok || !s.needResearch(w, "optimizer") {
		return
	}
	var req optv1.SetLineageAutonomyRequest
	if err := decodeProto(r, &req); err != nil || req.GetLevel() < 0 || req.GetLevel() > 2 {
		writeError(w, http.StatusBadRequest, "bad_request", "level is 0, 1, or 2")
		return
	}
	ctx, cancel := researchCtx(r)
	defer cancel()
	cur, err := s.research.Opt.GetLineage(ctx, &optv1.GetLineageRequest{LineageId: id})
	if err != nil {
		s.upstreamError(w, r, "optimizer", err)
		return
	}
	mode := "lower"
	if req.GetLevel() > cur.GetLineage().GetAutonomyLevel() || req.GetAnalystBudget() != nil || req.GetPromotionLimits() != nil {
		mode = "raise"
	}
	if !s.authorize(w, rc, policy.ActAnalystAutonomySet, policy.Resource{ID: id, Mode: mode}) {
		return
	}
	req.LineageId, req.Caller = id, s.researchCaller(rc)
	resp, err := s.research.Opt.SetLineageAutonomy(ctx, &req)
	if err != nil {
		s.upstreamError(w, r, "optimizer", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleLineageCycle(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var req optv1.OpenCycleRequest
	if err := decodeProto(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActResearchCycleOpen, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.OpenCycle(ctx, &optv1.OpenCycleRequest{LineageId: id, TargetSharpe: req.GetTargetSharpe(), Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleLineageLedger(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetTrialLedger(ctx, &optv1.GetTrialLedgerRequest{LineageId: id, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleLineageStats(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetCumulativeOverfittingStats(ctx, &optv1.GetCumulativeOverfittingStatsRequest{LineageId: id, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleHoldoutStatus(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "holdout", func(ctx context.Context) (proto.Message, error) {
		return s.research.Hold.GetHoldoutStatus(ctx, &optv1.GetHoldoutStatusRequest{LineageId: id})
	})
}

func (s *Server) handleHoldoutBoundary(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var req optv1.SetHoldoutBoundaryRequest
	if err := decodeProto(r, &req); err != nil || req.GetBoundary() <= 0 {
		writeError(w, http.StatusBadRequest, "bad_request", "boundary (UTC ns) is required")
		return
	}
	s.rcall(w, r, rc, "holdout", policy.ActHoldoutSchedule, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Hold.SetHoldoutBoundary(ctx, &optv1.SetHoldoutBoundaryRequest{LineageId: id, Boundary: req.GetBoundary(), Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleRetrainingPolicy(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var p optv1.RetrainingPolicy
	if err := decodeProto(r, &p); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	p.LineageId = id
	s.rcall(w, r, rc, "optimizer", policy.ActResearchLaunch, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.SetRetrainingPolicy(ctx, &optv1.SetRetrainingPolicyRequest{Policy: &p, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleRetrain(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActResearchLaunch, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.TriggerRetrain(ctx, &optv1.TriggerRetrainRequest{LineageId: id, Reason: "owner request", Caller: s.researchCaller(rc)})
	})
}

// ---- studies ----

func (s *Server) handleStudies(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	lin, ok := qID(r, "lineage_id")
	if !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid lineage_id")
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.ListStudies(ctx, &optv1.ListStudiesRequest{LineageId: lin, Limit: qInt(r, "limit", 50, 200)})
	})
}

func (s *Server) handleStudyCreate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var spec optv1.StudySpec
	if err := decodeProto(r, &spec); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActResearchLaunch, policy.Resource{ID: spec.GetLineageId()}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.CreateStudy(ctx, &optv1.CreateStudyRequest{Spec: &spec, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleStudy(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetStudy(ctx, &optv1.GetStudyRequest{StudyId: id})
	})
}

func (s *Server) handleStudyAction(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	action := r.PathValue("action")
	s.rcall(w, r, rc, "optimizer", policy.ActResearchLaunch, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		c := s.researchCaller(rc)
		switch action {
		case "start":
			return s.research.Opt.StartStudy(ctx, &optv1.StartStudyRequest{StudyId: id, Caller: c})
		case "pause":
			return s.research.Opt.PauseStudy(ctx, &optv1.PauseStudyRequest{StudyId: id, Caller: c})
		case "resume":
			return s.research.Opt.ResumeStudy(ctx, &optv1.ResumeStudyRequest{StudyId: id, Caller: c})
		case "stop":
			return s.research.Opt.StopStudy(ctx, &optv1.StopStudyRequest{StudyId: id, Caller: c})
		}
		return nil, errBadAction
	})
}

func (s *Server) handleStudyTrials(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.ListTrials(ctx, &optv1.ListTrialsRequest{StudyId: id, Limit: qInt(r, "limit", 500, 5000)})
	})
}

func (s *Server) handleStudyDiagnostics(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetStudyDiagnostics(ctx, &optv1.GetStudyDiagnosticsRequest{StudyId: id, Caller: s.researchCaller(rc)})
	})
}

// ---- candidates and registry ----

func (s *Server) handleCandidates(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	lin, ok := qID(r, "lineage_id")
	if !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid lineage_id")
		return
	}
	stage := stageNames[r.URL.Query().Get("stage")]
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.ListCandidates(ctx, &optv1.ListCandidatesRequest{LineageId: lin, Stage: stage})
	})
}

func (s *Server) handleCandidate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetCandidate(ctx, &optv1.GetCandidateRequest{CandidateId: id})
	})
}

func (s *Server) handleGateReport(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetGateReport(ctx, &optv1.GetGateReportRequest{CandidateId: id, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleEvidence(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok || !s.authorize(w, rc, policy.ActResearchRead, policy.Resource{}) || !s.needResearch(w, "optimizer") {
		return
	}
	ctx, cancel := researchCtx(r)
	defer cancel()
	resp, err := s.research.Opt.GetEvidencePack(ctx, &optv1.GetEvidencePackRequest{CandidateId: id})
	if err != nil {
		s.upstreamError(w, r, "optimizer", err)
		return
	}
	if r.URL.Query().Get("format") == "html" {
		w.Header().Set("Content-Security-Policy", tearsheetCSP)
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, resp.GetPack().GetHtml())
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleCandidateGraph(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetLineageGraph(ctx, &optv1.GetLineageGraphRequest{CandidateId: id})
	})
}

type stageBody struct {
	ToStage string `json:"toStage"`
	Reason  string `json:"reason"`
}

func (s *Server) stageParam(w http.ResponseWriter, r *http.Request) (stageBody, mlv1.RegistryStage, bool) {
	var b stageBody
	if err := decode(r, &b); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid body")
		return b, 0, false
	}
	st, ok := stageNames[b.ToStage]
	if !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "toStage is validated, shadow, paper, live_eligible, or retired (live is section 15 only)")
		return b, 0, false
	}
	return b, st, true
}

func (s *Server) handleCandidateCheck(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	_, st, ok := s.stageParam(w, r)
	if !ok {
		return
	}
	s.readResearch(w, r, rc, "optimizer", func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.CheckTransition(ctx, &optv1.CheckTransitionRequest{CandidateId: id, ToStage: st, Caller: s.researchCaller(rc)})
	})
}

// handleCandidatePromote applies the permission table and gates in the optimizer (EX-031); promotion
// to paper or live_eligible needs a passkey step-up here (N4); the optimizer audits every transition
// with the evidence-pack hash (EX-050, EX-057).
func (s *Server) handleCandidatePromote(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	b, st, ok := s.stageParam(w, r)
	if !ok {
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActModelPromote, policy.Resource{ID: id, Mode: b.ToStage}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.PromoteCandidate(ctx, &optv1.PromoteCandidateRequest{CandidateId: id, ToStage: st, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleCandidateDemote(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	b, st, ok := s.stageParam(w, r)
	if !ok {
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActModelDemote, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.DemoteCandidate(ctx, &optv1.DemoteCandidateRequest{CandidateId: id, ToStage: st, Reason: b.Reason, Caller: s.researchCaller(rc)})
	})
}

func (s *Server) handleHoldoutEvaluate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var b stageBody
	if err := decode(r, &b); err != nil || b.Reason == "" || len(b.Reason) > 500 {
		writeError(w, http.StatusBadRequest, "bad_request", "a reason (1-500 characters) is required (EX-011)")
		return
	}
	s.rcall(w, r, rc, "holdout", policy.ActHoldoutEvaluate, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Hold.EvaluateOnHoldout(ctx, &optv1.EvaluateOnHoldoutRequest{CandidateId: id, Reason: b.Reason, Caller: s.researchCaller(rc)})
	})
}

// ---- streams (SSE, D14) ----

func (s *Server) handleTrainingStream(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok || !s.authorize(w, rc, policy.ActResearchRead, policy.Resource{}) || !s.needResearch(w, "ml") {
		return
	}
	after, _ := strconv.ParseInt(r.Header.Get("Last-Event-ID"), 10, 64)
	st, err := s.research.ML.StreamJobProgress(r.Context(), &mlv1.StreamJobProgressRequest{JobId: id, AfterSeq: after})
	if err != nil {
		s.upstreamError(w, r, "ml", err)
		return
	}
	s.pipeSSE(w, r, func() (proto.Message, int64, error) {
		m, err := st.Recv()
		if err != nil {
			return nil, 0, err
		}
		return m.GetProgress(), m.GetProgress().GetSeq(), nil
	})
}

func (s *Server) handleStudyStream(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok || !s.authorize(w, rc, policy.ActResearchRead, policy.Resource{}) || !s.needResearch(w, "optimizer") {
		return
	}
	after, _ := strconv.ParseInt(r.Header.Get("Last-Event-ID"), 10, 64)
	st, err := s.research.Opt.StreamStudyProgress(r.Context(), &optv1.StreamStudyProgressRequest{StudyId: id, AfterSeq: after})
	if err != nil {
		s.upstreamError(w, r, "optimizer", err)
		return
	}
	s.pipeSSE(w, r, func() (proto.Message, int64, error) {
		m, err := st.Recv()
		if err != nil {
			return nil, 0, err
		}
		return m.GetProgress(), m.GetProgress().GetSeq(), nil
	})
}

// pipeSSE relays a server stream as `progress` events with the upstream sequence as the event ID
// (resume with Last-Event-ID) and ends with `done` when the upstream stream ends.
func (s *Server) pipeSSE(w http.ResponseWriter, r *http.Request, next func() (proto.Message, int64, error)) {
	rcw := http.NewResponseController(w)
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	_ = rcw.Flush()
	for {
		m, seq, err := next()
		if err != nil {
			//nolint:gosec // text/event-stream with a constant payload
			_, _ = fmt.Fprint(w, "event: done\ndata: {}\n\n")
			_ = rcw.Flush()
			return
		}
		b, err := pj.Marshal(m)
		if err != nil {
			return
		}
		//nolint:gosec // text/event-stream, not HTML; data is protojson
		if _, err := fmt.Fprintf(w, "id: %d\nevent: progress\nretry: 3000\ndata: %s\n\n", seq, b); err != nil || rcw.Flush() != nil {
			return
		}
		if r.Context().Err() != nil {
			return
		}
	}
}
