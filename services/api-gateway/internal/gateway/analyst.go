package gateway

import (
	"context"
	"errors"
	"net/http"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"

	analystv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/analyst/v1"
	mlv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ml/v1"
	optimizerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// AnalystClients is the assistant's AnalystService behind the AI analyst page (section 14 Phase 4
// slice, ADR-0041). Owner actions the analyst may not take itself go to the research clients: raising
// autonomy (AN-061, the research autonomy route) and launching approved actions at level 0 (AN-060).
// A nil client makes the analyst routes answer 503.
type AnalystClients struct {
	Analyst analystv1.AnalystServiceClient
}

// DialAnalyst connects lazily with a service token per call (SE-040).
func DialAnalyst(cfg Config, signer *svcauth.Signer) (*AnalystClients, func(), error) {
	c, err := grpc.NewClient(cfg.AssistantAddr, grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithUnaryInterceptor(func(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, inv grpc.UnaryInvoker, opts ...grpc.CallOption) error {
			return inv(ctx, method, req, reply, cc, append(opts, signer.PerRPC("assistant"))...)
		}))
	if err != nil {
		return nil, func() {}, err
	}
	return &AnalystClients{Analyst: analystv1.NewAnalystServiceClient(c)}, func() { _ = c.Close() }, nil
}

func (s *Server) registerAnalystRoutes(priv func(string, string, func(http.ResponseWriter, *http.Request, *reqCtx))) {
	priv("GET", "/api/v1/analyst/lineages", s.handleAnalystLineages)
	priv("GET", "/api/v1/analyst/lineages/{id}", s.handleAnalystStatus)
	priv("GET", "/api/v1/analyst/lineages/{id}/journal", s.handleAnalystJournal)
	priv("GET", "/api/v1/analyst/lineages/{id}/trials", s.handleAnalystTrials)
	priv("POST", "/api/v1/analyst/lineages/{id}/pause", s.handleAnalystPause)
	priv("POST", "/api/v1/analyst/lineages/{id}/analyze", s.handleAnalystAnalyze)
	priv("GET", "/api/v1/analyst/rounds/{id}", s.handleAnalystRound)
	priv("POST", "/api/v1/analyst/rounds/{id}/review", s.handleAnalystReview)
	priv("GET", "/api/v1/analyst/proposals", s.handleAnalystProposals)
	priv("POST", "/api/v1/analyst/proposals/{id}/review", s.handleAnalystProposalReview)
	priv("GET", "/api/v1/assistant/conversations", s.handleChatList)
	priv("POST", "/api/v1/assistant/conversations", s.handleChatCreate)
	priv("GET", "/api/v1/assistant/conversations/{id}", s.handleChatGet)
	priv("POST", "/api/v1/assistant/conversations/{id}/messages", s.handleChatPost)
}

func (s *Server) needAnalyst(w http.ResponseWriter) bool {
	if s.analyst == nil || s.analyst.Analyst == nil {
		writeError(w, http.StatusServiceUnavailable, "assistant_unavailable", "assistant unavailable")
		return false
	}
	return true
}

func (s *Server) idParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid id")
		return "", false
	}
	return id, true
}

// analystCall wraps one read: policy, availability, a bounded context, protojson out.
func (s *Server) analystRead(w http.ResponseWriter, r *http.Request, rc *reqCtx, fn func(context.Context) (proto.Message, error)) {
	if !s.authorize(w, rc, policy.ActAnalystRead, policy.Resource{}) || !s.needAnalyst(w) {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	resp, err := fn(ctx)
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleAnalystLineages(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	s.analystRead(w, r, rc, func(ctx context.Context) (proto.Message, error) {
		return s.analyst.Analyst.ListLineages(ctx, &analystv1.ListLineagesRequest{})
	})
}

func (s *Server) handleAnalystStatus(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	s.analystRead(w, r, rc, func(ctx context.Context) (proto.Message, error) {
		return s.analyst.Analyst.GetLoopStatus(ctx, &analystv1.GetLoopStatusRequest{LineageId: id})
	})
}

func (s *Server) handleAnalystJournal(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	s.analystRead(w, r, rc, func(ctx context.Context) (proto.Message, error) {
		return s.analyst.Analyst.GetJournal(ctx, &analystv1.GetJournalRequest{LineageId: id, Limit: qInt(r, "limit", 50, 500)})
	})
}

func (s *Server) handleAnalystTrials(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	s.analystRead(w, r, rc, func(ctx context.Context) (proto.Message, error) {
		return s.analyst.Analyst.GetTrialLedger(ctx, &analystv1.GetTrialLedgerRequest{LineageId: id, Limit: qInt(r, "limit", 200, 2000)})
	})
}

func (s *Server) handleAnalystRound(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	s.analystRead(w, r, rc, func(ctx context.Context) (proto.Message, error) {
		return s.analyst.Analyst.GetRound(ctx, &analystv1.GetRoundRequest{RoundId: id})
	})
}

func (s *Server) handleAnalystProposals(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	lineage, ok := qID(r, "lineage")
	if !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid lineage")
		return
	}
	s.analystRead(w, r, rc, func(ctx context.Context) (proto.Message, error) {
		return s.analyst.Analyst.ListCodeProposals(ctx, &analystv1.ListCodeProposalsRequest{LineageId: lineage})
	})
}

// ---- loop control and analysis requests ----

type pauseReq struct {
	Paused bool   `json:"paused"`
	Reason string `json:"reason"`
}

func (s *Server) handleAnalystPause(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	var req pauseReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.authorize(w, rc, policy.ActAnalystLoopPause, policy.Resource{ID: id}) || !s.needAnalyst(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	if err := s.record(ctx, rc, "owner", string(policy.ActAnalystLoopPause), id, map[string]any{"paused": req.Paused, "reason": truncate(req.Reason, 300)}, "", ""); err != nil {
		writeError(w, http.StatusServiceUnavailable, "audit_unavailable", "audit unavailable")
		return
	}
	resp, err := s.analyst.Analyst.SetLoopPaused(ctx, &analystv1.SetLoopPausedRequest{LineageId: id, Paused: req.Paused, Actor: "owner",
		Reason: truncate(req.Reason, 300)})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type analyzeReq struct {
	RefKind  string `json:"refKind"`
	RefID    string `json:"refId"`
	Question string `json:"question"`
}

func (s *Server) handleAnalystAnalyze(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	var req analyzeReq
	if err := decode(r, &req); err != nil || (req.RefID != "" && !idRe.MatchString(req.RefID)) || len(req.Question) > 2000 {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid analysis request")
		return
	}
	if !s.authorize(w, rc, policy.ActAnalystAnalysis, policy.Resource{ID: id}) || !s.needAnalyst(w) {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 150*time.Second)
	defer cancel()
	resp, err := s.analyst.Analyst.RequestAnalysis(ctx, &analystv1.RequestAnalysisRequest{LineageId: id, RefKind: req.RefKind,
		RefId: req.RefID, Question: req.Question, Actor: "owner"})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// ---- approvals (AN-060): at level 0 the owner launches approved actions ----

type reviewReq struct {
	ActionIDs []string `json:"actionIds"`
	Approve   bool     `json:"approve"`
	Reason    string   `json:"reason"`
}

func (s *Server) handleAnalystReview(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	var req reviewReq
	if err := decode(r, &req); err != nil || len(req.ActionIDs) == 0 || len(req.ActionIDs) > 20 {
		writeError(w, http.StatusBadRequest, "bad_request", "actionIds required (at most 20)")
		return
	}
	for _, a := range req.ActionIDs {
		if !idRe.MatchString(a) {
			writeError(w, http.StatusBadRequest, "bad_request", "invalid action id")
			return
		}
	}
	if !s.authorize(w, rc, policy.ActAnalystActionsReview, policy.Resource{ID: id}) || !s.needAnalyst(w) {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	if err := s.record(ctx, rc, "owner", string(policy.ActAnalystActionsReview), id,
		map[string]any{"round_id": id, "action_ids": req.ActionIDs, "approve": req.Approve, "reason": truncate(req.Reason, 300)}, "", ""); err != nil {
		writeError(w, http.StatusServiceUnavailable, "audit_unavailable", "audit unavailable")
		return
	}
	resp, err := s.analyst.Analyst.ReviewActions(ctx, &analystv1.ReviewActionsRequest{RoundId: id, ActionIds: req.ActionIDs,
		Approve: req.Approve, Reason: truncate(req.Reason, 300), Actor: "owner"})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	if len(resp.GetLaunches()) == 0 {
		writeProto(w, http.StatusOK, resp)
		return
	}
	records := make([]*analystv1.LaunchRecord, 0, len(resp.GetLaunches()))
	for _, l := range resp.GetLaunches() {
		ids, err := s.submitPrepared(ctx, rc, id, l)
		rec := &analystv1.LaunchRecord{ActionId: l.GetActionId(), JobIds: ids}
		if err != nil {
			rec.Error = truncate(err.Error(), 300)
		}
		records = append(records, rec)
	}
	out, err := s.analyst.Analyst.RecordLaunches(ctx, &analystv1.RecordLaunchesRequest{RoundId: id, Launches: records, Actor: "owner"})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, &analystv1.ReviewActionsResponse{Plan: out.GetPlan()})
}

var (
	errPrepared            = errors.New("prepared launch does not belong to this round")
	errResearchUnavailable = errors.New("ml or optimizer unavailable")
)

// submitPrepared submits one owner-approved action as the owner. The request content comes from the
// assistant (already validated against the owning service's schema); the caller identity is always
// replaced here, so the analyst can never borrow the owner's identity (EX-030).
func (s *Server) submitPrepared(ctx context.Context, rc *reqCtx, roundID string, l *analystv1.PreparedLaunch) ([]string, error) {
	if l.GetRoundId() != roundID {
		return nil, errPrepared
	}
	if s.research == nil || s.research.ML == nil || s.research.Opt == nil {
		return nil, errResearchUnavailable
	}
	caller := s.researchCaller(rc)
	caller.Reason, caller.AnalystRoundId = "approved analyst action "+l.GetActionId(), roundID
	switch l.GetKind() {
	case "training":
		var req mlv1.SubmitTrainingJobRequest
		if err := proto.Unmarshal(l.GetRequest(), &req); err != nil {
			return nil, err
		}
		req.Caller, req.ConfirmOverBudget = caller, false
		resp, err := s.research.ML.SubmitTrainingJob(ctx, &req)
		if err != nil {
			return nil, err
		}
		return []string{resp.GetJob().GetJobId()}, nil
	case "ablation":
		var req mlv1.SubmitAblationRequest
		if err := proto.Unmarshal(l.GetRequest(), &req); err != nil {
			return nil, err
		}
		req.Caller = caller
		resp, err := s.research.ML.SubmitAblation(ctx, &req)
		if err != nil {
			return nil, err
		}
		ids := make([]string, 0, len(resp.GetJobs()))
		for _, j := range resp.GetJobs() {
			ids = append(ids, j.GetJobId())
		}
		return ids, nil
	case "study":
		var req optimizerv1.CreateStudyRequest
		if err := proto.Unmarshal(l.GetRequest(), &req); err != nil {
			return nil, err
		}
		req.Caller = caller
		created, err := s.research.Opt.CreateStudy(ctx, &req)
		if err != nil {
			return nil, err
		}
		started, err := s.research.Opt.StartStudy(ctx, &optimizerv1.StartStudyRequest{StudyId: created.GetStudy().GetStudyId(),
			Budget: req.GetSpec().GetBudget(), Caller: caller})
		if err != nil {
			return nil, err
		}
		return []string{started.GetStudy().GetStudyId()}, nil
	default:
		return nil, errPrepared
	}
}

// ---- code proposals (AN-073) ----

type proposalReviewReq struct {
	Approve bool   `json:"approve"`
	Note    string `json:"note"`
}

func (s *Server) handleAnalystProposalReview(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	var req proposalReviewReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.authorize(w, rc, policy.ActAnalystCodeReview, policy.Resource{ID: id}) || !s.needAnalyst(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	if err := s.record(ctx, rc, "owner", string(policy.ActAnalystCodeReview), id, map[string]any{"approve": req.Approve, "note": truncate(req.Note, 300)}, "", ""); err != nil {
		writeError(w, http.StatusServiceUnavailable, "audit_unavailable", "audit unavailable")
		return
	}
	resp, err := s.analyst.Analyst.ReviewCodeProposal(ctx, &analystv1.ReviewCodeProposalRequest{ProposalId: id, Approve: req.Approve,
		Note: truncate(req.Note, 1000), Actor: "owner"})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// ---- chat (AN-080) ----

func (s *Server) chatGuard(w http.ResponseWriter, rc *reqCtx) bool {
	return s.authorize(w, rc, policy.ActAssistantChat, policy.Resource{}) && s.needAnalyst(w)
}

func (s *Server) handleChatList(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.chatGuard(w, rc) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.analyst.Analyst.ListConversations(ctx, &analystv1.ListConversationsRequest{})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type chatCreateReq struct {
	Title     string `json:"title"`
	LineageID string `json:"lineageId"`
}

func (s *Server) handleChatCreate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req chatCreateReq
	if err := decode(r, &req); err != nil || (req.LineageID != "" && !idRe.MatchString(req.LineageID)) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid conversation")
		return
	}
	if !s.chatGuard(w, rc) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.analyst.Analyst.CreateConversation(ctx, &analystv1.CreateConversationRequest{Title: truncate(req.Title, 120), LineageId: req.LineageID})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleChatGet(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok || !s.chatGuard(w, rc) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.analyst.Analyst.GetConversation(ctx, &analystv1.GetConversationRequest{ConversationId: id})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type chatPostReq struct {
	Content string `json:"content"`
}

func (s *Server) handleChatPost(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.idParam(w, r)
	if !ok {
		return
	}
	var req chatPostReq
	if err := decode(r, &req); err != nil || req.Content == "" || len(req.Content) > 4000 {
		writeError(w, http.StatusBadRequest, "bad_request", "content required (at most 4000 characters)")
		return
	}
	if !s.chatGuard(w, rc) {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 150*time.Second)
	defer cancel()
	resp, err := s.analyst.Analyst.PostMessage(ctx, &analystv1.PostMessageRequest{ConversationId: id, Content: req.Content, Actor: "owner"})
	if err != nil {
		s.upstreamError(w, r, "assistant", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}
