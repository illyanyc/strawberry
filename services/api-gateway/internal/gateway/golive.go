package gateway

import (
	"net/http"

	"github.com/shopspring/decimal"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/policy"
)

// Go-live BFF (GL-010..GL-040, UI-052). The screen refuses until execution's report says so;
// these handlers only authorize the owner and forward. There is no setting that turns an account live.

func (s *Server) registerLiveRoutes(priv func(string, string, func(http.ResponseWriter, *http.Request, *reqCtx))) {
	priv("GET", "/api/v1/live", s.handleLiveStatus)
	priv("GET", "/api/v1/live/accounts/{account}/readiness", s.handleLiveReadiness)
	priv("PUT", "/api/v1/live/accounts/{account}/readiness/{id}", s.handleLiveReadinessItem)
	priv("GET", "/api/v1/live/strategies/{id}/graduation", s.handleGraduation)
	priv("POST", "/api/v1/live/accounts/{account}/enable", s.handleLiveEnable)
	priv("POST", "/api/v1/live/accounts/{account}/disable", s.handleLiveDisable)
	priv("POST", "/api/v1/live/strategies/{id}/promote", s.handleLivePromote)
	priv("POST", "/api/v1/live/strategies/{id}/demote", s.handleLiveDemote)
	priv("POST", "/api/v1/live/strategies/{id}/cap", s.handleLiveCap)
}

func (s *Server) handleLiveStatus(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.GetLiveStatus(ctx, &ordersv1.GetLiveStatusRequest{})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleLiveReadiness(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.GetLiveReadiness(ctx, &ordersv1.GetLiveReadinessRequest{BrokerAccountId: acct})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type readinessItemReq struct {
	Status      string `json:"status"`
	Note        string `json:"note"`
	EvidenceURL string `json:"evidence_url"`
}

func (s *Server) handleLiveReadinessItem(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok {
		return
	}
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid item")
		return
	}
	var req readinessItemReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.authorize(w, rc, policy.ActTradingLiveEnable, policy.Resource{ID: acct}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.SetReadinessItem(ctx, &ordersv1.SetReadinessItemRequest{BrokerAccountId: acct, ItemId: id,
		Status: req.Status, Note: truncate(req.Note, 300), EvidenceUrl: truncate(req.EvidenceURL, 200), Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleGraduation(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.GetGraduationReport(ctx, &ordersv1.GetGraduationReportRequest{StrategyId: id})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type liveConfirmReq struct {
	Confirmation string `json:"confirmation"`
	Stage        string `json:"stage"`
	Reason       string `json:"reason"`
}

func (s *Server) handleLiveEnable(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok {
		return
	}
	var req liveConfirmReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.authorize(w, rc, policy.ActTradingLiveEnable, policy.Resource{ID: acct}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.EnableLiveAccount(ctx, &ordersv1.EnableLiveAccountRequest{BrokerAccountId: acct,
		Confirmation: req.Confirmation, Stage: req.Stage, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleLiveDisable(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok {
		return
	}
	var req liveConfirmReq
	if err := decode(r, &req); err != nil || req.Reason == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "reason is required")
		return
	}
	if !s.authorize(w, rc, policy.ActTradingLiveDisable, policy.Resource{ID: acct}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.DisableLiveAccount(ctx, &ordersv1.DisableLiveAccountRequest{BrokerAccountId: acct,
		Reason: truncate(req.Reason, 300), Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type promoteReq struct {
	LiveAccountID      string `json:"live_account_id"`
	CapitalCap         string `json:"capital_cap"`
	IntendedAllocation string `json:"intended_allocation"`
	Confirmation       string `json:"confirmation"`
	SkipPaperTwin      bool   `json:"skip_paper_twin"`
}

func (s *Server) handleLivePromote(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok {
		return
	}
	var req promoteReq
	if err := decode(r, &req); err != nil || !idRe.MatchString(req.LiveAccountID) || !decimalRe.MatchString(req.CapitalCap) || !decimalRe.MatchString(req.IntendedAllocation) {
		writeError(w, http.StatusBadRequest, "bad_request", "live account, capital cap, and intended allocation are required")
		return
	}
	if !s.authorize(w, rc, policy.ActStrategyPromoteLive, policy.Resource{ID: id}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: id, LiveAccountId: req.LiveAccountID,
		CapitalCap: &commonv1.Decimal{Value: req.CapitalCap}, IntendedAllocation: &commonv1.Decimal{Value: req.IntendedAllocation},
		Confirmation: req.Confirmation, SkipPaperTwin: req.SkipPaperTwin, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type demoteReq struct {
	Reason         string `json:"reason"`
	PositionPolicy string `json:"position_policy"`
}

func (s *Server) handleLiveDemote(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok {
		return
	}
	var req demoteReq
	if err := decode(r, &req); err != nil || req.Reason == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "reason is required")
		return
	}
	if !s.authorize(w, rc, policy.ActStrategyDemoteLive, policy.Resource{ID: id}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.DemoteFromLive(ctx, &ordersv1.DemoteFromLiveRequest{StrategyId: id, Reason: truncate(req.Reason, 300),
		PositionPolicy: req.PositionPolicy, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleLiveCap(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok {
		return
	}
	var req struct {
		NewCap       string `json:"new_cap"`
		Confirmation string `json:"confirmation"`
	}
	if err := decode(r, &req); err != nil || !decimalRe.MatchString(req.NewCap) {
		writeError(w, http.StatusBadRequest, "bad_request", "new_cap must be a decimal")
		return
	}
	if _, err := decimal.NewFromString(req.NewCap); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "new_cap must be a decimal")
		return
	}
	if !s.authorize(w, rc, policy.ActStrategyPromoteLive, policy.Resource{ID: id}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.RaiseLiveCap(ctx, &ordersv1.RaiseLiveCapRequest{StrategyId: id, NewCap: &commonv1.Decimal{Value: req.NewCap},
		Confirmation: req.Confirmation, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}
