package gateway

import (
	"context"
	"encoding/json"
	"net/http"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mlv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ml/v1"
	optv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/policy"
)

func (s *Server) handleDeploymentLimits(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	s.rcall(w, r, rc, "optimizer", policy.ActResearchRead, policy.Resource{ID: id}, func(ctx context.Context) (proto.Message, error) {
		return s.research.Opt.GetDeploymentLimits(ctx, &optv1.GetDeploymentLimitsRequest{LineageId: id})
	})
}

// handleDeploymentLimitsPut saves a lineage's deployment limits (EX-062). The optimizer decides
// whether the change raises a limit or the allocation; when it does and the session has no fresh
// passkey step-up, the policy answer for the raise (require_step_up) goes back to the UI (RM-007).
func (s *Server) handleDeploymentLimitsPut(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok {
		return
	}
	var req optv1.SetDeploymentLimitsRequest
	if err := decodeProto(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.authorize(w, rc, policy.ActDeploymentLimitsSet, policy.Resource{ID: id, Mode: "lower"}) || !s.needResearch(w, "optimizer") {
		return
	}
	ctx, cancel := researchCtx(r)
	defer cancel()
	req.LineageId, req.Caller = id, s.researchCaller(rc)
	resp, err := s.research.Opt.SetDeploymentLimits(ctx, &req)
	if status.Code(err) == codes.PermissionDenied {
		if s.authorize(w, rc, policy.ActDeploymentLimitsSet, policy.Resource{ID: id, Mode: "raise"}) {
			writeError(w, http.StatusForbidden, "forbidden", status.Convert(err).Message())
		}
		return
	}
	if err != nil {
		s.upstreamError(w, r, "optimizer", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// deployEntry is the part of a candidate's registry entry (strategies.json format) that execution
// registration needs.
type deployEntry struct {
	StrategyID string            `json:"strategy_id"`
	ShortID    string            `json:"short_id"`
	Name       string            `json:"name"`
	Horizon    string            `json:"horizon"`
	Account    string            `json:"broker_account_id"`
	Mode       string            `json:"mode"`
	Allocation string            `json:"allocation"`
	Version    string            `json:"version"`
	CodeCommit string            `json:"code_commit"`
	ParamsHash string            `json:"params_hash"`
	Limits     map[string]string `json:"limits"`
}

// handleCandidateRegister registers a shadow- or paper-stage candidate with execution so it can
// trade on paper (SM-016, EX-064): RegisterStrategy (new entries start in the candidate's stage mode;
// never live), the allocation through the ledger, and the deployment limits in the strategy scope
// (RM-019). It needs a fresh passkey (strategy.register); execution re-checks the step-up for raised
// limits and the ledger for the allocation (RM-007).
func (s *Server) handleCandidateRegister(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.pathID(w, r)
	if !ok || !s.authorize(w, rc, policy.ActStrategyRegister, policy.Resource{ID: id}) || !s.needResearch(w, "optimizer") || !s.needExec(w) {
		return
	}
	ctx, cancel := researchCtx(r)
	defer cancel()
	got, err := s.research.Opt.GetCandidate(ctx, &optv1.GetCandidateRequest{CandidateId: id})
	if err != nil {
		s.upstreamError(w, r, "optimizer", err)
		return
	}
	cand := got.GetCandidate()
	if st := cand.GetStage(); st != mlv1.RegistryStage_REGISTRY_STAGE_PAPER && st != mlv1.RegistryStage_REGISTRY_STAGE_SHADOW {
		writeError(w, http.StatusConflict, "wrong_stage", "only shadow- or paper-stage candidates can be registered (live is the go-live flow)")
		return
	}
	var e deployEntry
	if err := json.Unmarshal([]byte(cand.GetRegistryEntryJson()), &e); err != nil || e.StrategyID == "" || e.Account == "" {
		writeError(w, http.StatusConflict, "no_deployment", "the candidate has no deployment entry")
		return
	}
	mode := ordersv1.StrategyMode_STRATEGY_MODE_PAPER
	if e.Mode == "shadow" {
		mode = ordersv1.StrategyMode_STRATEGY_MODE_SHADOW
	}
	hz := commonv1.Horizon_HORIZON_INTRADAY
	if e.Horizon == "swing" {
		hz = commonv1.Horizon_HORIZON_SWING
	}
	actor := s.execActor(r, rc)
	reg, err := s.trading.Exec.RegisterStrategy(ctx, &ordersv1.RegisterStrategyRequest{Actor: actor, Strategy: &ordersv1.Strategy{
		StrategyId: e.StrategyID, ShortId: e.ShortID, Name: e.Name, Horizon: hz, Mode: mode, BrokerAccountId: e.Account,
		Version: e.Version, CodeCommit: e.CodeCommit, ParamsHash: e.ParamsHash, RuntimeHosted: true}})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	if e.Allocation != "" && s.trading.Ledger != nil {
		if _, err := s.trading.Ledger.SetAllocation(ctx, &ledgerv1.SetAllocationRequest{Owner: e.StrategyID, BrokerAccountId: e.Account,
			Amount: &commonv1.Decimal{Value: e.Allocation}, Actor: "owner"}); err != nil {
			s.upstreamError(w, r, "ledger", err)
			return
		}
	}
	lim := &ordersv1.RiskLimits{}
	for k, v := range e.Limits {
		if !setRiskLimit(lim, k, v) {
			writeError(w, http.StatusConflict, "bad_limits", "unknown limit "+k+" in the deployment entry")
			return
		}
	}
	lr, err := s.trading.Exec.SetRiskLimits(ctx, &ordersv1.SetRiskLimitsRequest{Scope: "strategy:" + e.StrategyID, Limits: lim, Actor: actor})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"strategy_id": reg.GetStrategy().GetStrategyId(), "mode": reg.GetStrategy().GetMode().String(),
		"broker_account_id": e.Account, "allocation": e.Allocation, "limits_raised": lr.GetRaised()})
}

func setRiskLimit(l *ordersv1.RiskLimits, k, v string) bool {
	d := &commonv1.Decimal{Value: v}
	n := int32(0)
	if x, err := json.Number(v).Int64(); err == nil {
		n = int32(x)
	}
	switch k {
	case "max_order_notional":
		l.MaxOrderNotional = d
	case "max_order_qty":
		l.MaxOrderQty = d
	case "max_position_notional":
		l.MaxPositionNotional = d
	case "max_position_pct_allocation":
		l.MaxPositionPctAllocation = d
	case "max_gross_exposure":
		l.MaxGrossExposure = d
	case "max_net_exposure":
		l.MaxNetExposure = d
	case "max_concentration_pct":
		l.MaxConcentrationPct = d
	case "max_daily_loss":
		l.MaxDailyLoss = d
	case "max_drawdown":
		l.MaxDrawdown = d
	case "price_collar_pct":
		l.PriceCollarPct = d
	case "fat_finger_multiple":
		l.FatFingerMultiple = d
	case "max_positions":
		l.MaxPositions = n
	case "max_orders_per_minute":
		l.MaxOrdersPerMinute = n
	case "max_cancels_per_minute":
		l.MaxCancelsPerMinute = n
	case "max_consecutive_losses":
		l.MaxConsecutiveLosses = n
	case "duplicate_window_seconds":
		l.DuplicateWindowSeconds = n
	case "max_open_orders":
		l.MaxOpenOrders = n
	default:
		return false
	}
	return true
}
