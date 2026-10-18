// Package server implements tb.orders.v1.ExecutionService over the OMS (section 07).
package server

import (
	"context"
	"errors"

	"github.com/shopspring/decimal"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/execution/internal/oms"
)

const svc = "/tb.orders.v1.ExecutionService/"

// AllowedCallers is the per-method allowlist (SE-040). Only api-gateway (owner actions, UI) calls
// execution over gRPC; strategies publish intents on ord.intent. Kill-switch engage and the
// reduce-only DemoteStrategy are also open to monitor (automatic containment, section 13, RM-018).
var AllowedCallers = map[string][]string{
	svc + "SubmitIntent":      {"api-gateway"},
	svc + "PreviewIntent":     {"api-gateway"},
	svc + "CancelIntent":      {"api-gateway"},
	svc + "ListOrders":        {"api-gateway", "monitor"},
	svc + "ListIntents":       {"api-gateway", "monitor"},
	svc + "ListFills":         {"api-gateway", "monitor"},
	svc + "GetTrace":          {"api-gateway"},
	svc + "ListStrategies":    {"api-gateway", "monitor"},
	svc + "RegisterStrategy":  {"api-gateway"},
	svc + "SetStrategyMode":   {"api-gateway"},
	svc + "GetRiskLimits":     {"api-gateway"},
	svc + "SetRiskLimits":     {"api-gateway"},
	svc + "TriggerKillSwitch": {"api-gateway", "monitor"},
	svc + "ReleaseKillSwitch": {"api-gateway"},
	svc + "ListKillSwitches":  {"api-gateway", "monitor"},
	svc + "GetTradingStatus":  {"api-gateway", "monitor"},
	svc + "DemoteStrategy":    {"api-gateway", "monitor"},
}

// Server is the gRPC service.
type Server struct {
	ordersv1.UnimplementedExecutionServiceServer
	E *oms.Engine
}

func grpcErr(err error) error {
	switch {
	case err == nil:
		return nil
	case errors.Is(err, oms.ErrNotFound):
		return status.Error(codes.NotFound, err.Error())
	case errors.Is(err, oms.ErrInvalid):
		return status.Error(codes.InvalidArgument, err.Error())
	case errors.Is(err, oms.ErrStepUp):
		return status.Error(codes.PermissionDenied, err.Error())
	default:
		return status.Error(codes.Internal, "execution error")
	}
}

// actor is the audited actor. Only api-gateway may assert an owner actor and a step-up method (it
// authorized the owner through policy.Authorize); any other caller is recorded as itself.
func actor(ctx context.Context, a *ordersv1.Actor) *ordersv1.Actor {
	if a == nil {
		a = &ordersv1.Actor{}
	}
	if c := svcauth.Caller(ctx); c != "" && c != "api-gateway" {
		return &ordersv1.Actor{Name: "service:" + c}
	}
	if a.GetName() == "" {
		a.Name = "service:" + svcauth.Caller(ctx)
	}
	return a
}

// SubmitIntent implements ExecutionService.
func (s *Server) SubmitIntent(ctx context.Context, r *ordersv1.SubmitIntentRequest) (*ordersv1.SubmitIntentResponse, error) {
	resp, err := s.E.Submit(ctx, r.GetIntent(), actor(ctx, r.GetActor()))
	return resp, grpcErr(err)
}

// PreviewIntent implements ExecutionService.
func (s *Server) PreviewIntent(ctx context.Context, r *ordersv1.PreviewIntentRequest) (*ordersv1.PreviewIntentResponse, error) {
	resp, err := s.E.Preview(ctx, r.GetIntent())
	return resp, grpcErr(err)
}

// CancelIntent implements ExecutionService.
func (s *Server) CancelIntent(ctx context.Context, r *ordersv1.CancelIntentRequest) (*ordersv1.CancelIntentResponse, error) {
	return &ordersv1.CancelIntentResponse{Canceled: int32(s.E.CancelIntent(ctx, r.GetIntentId()))}, nil
}

// ListOrders implements ExecutionService.
func (s *Server) ListOrders(_ context.Context, r *ordersv1.ListOrdersRequest) (*ordersv1.ListOrdersResponse, error) {
	return &ordersv1.ListOrdersResponse{Orders: s.E.Orders(r.GetBrokerAccountId(), r.GetStrategyId(), r.GetOpenOnly(), r.GetSince(), int(r.GetLimit()))}, nil
}

// ListIntents implements ExecutionService.
func (s *Server) ListIntents(_ context.Context, r *ordersv1.ListIntentsRequest) (*ordersv1.ListIntentsResponse, error) {
	return &ordersv1.ListIntentsResponse{Intents: s.E.Intents(r.GetStrategyId(), r.GetRejectedOnly(), r.GetSince(), int(r.GetLimit()))}, nil
}

// ListFills implements ExecutionService.
func (s *Server) ListFills(_ context.Context, r *ordersv1.ListFillsRequest) (*ordersv1.ListFillsResponse, error) {
	return &ordersv1.ListFillsResponse{Fills: s.E.Fills(r.GetBrokerAccountId(), r.GetStrategyId(), r.GetSince(), int(r.GetLimit()))}, nil
}

// GetTrace implements ExecutionService.
func (s *Server) GetTrace(_ context.Context, r *ordersv1.GetTraceRequest) (*ordersv1.GetTraceResponse, error) {
	return s.E.Trace(r.GetCorrelationId()), nil
}

// ListStrategies implements ExecutionService.
func (s *Server) ListStrategies(context.Context, *ordersv1.ListStrategiesRequest) (*ordersv1.ListStrategiesResponse, error) {
	return &ordersv1.ListStrategiesResponse{Strategies: s.E.Strategies()}, nil
}

// RegisterStrategy implements ExecutionService.
func (s *Server) RegisterStrategy(ctx context.Context, r *ordersv1.RegisterStrategyRequest) (*ordersv1.RegisterStrategyResponse, error) {
	st, err := s.E.RegisterStrategy(ctx, r.GetStrategy(), decimal.Zero, actor(ctx, r.GetActor()).GetName())
	return &ordersv1.RegisterStrategyResponse{Strategy: st}, grpcErr(err)
}

// SetStrategyMode implements ExecutionService.
func (s *Server) SetStrategyMode(ctx context.Context, r *ordersv1.SetStrategyModeRequest) (*ordersv1.SetStrategyModeResponse, error) {
	st, err := s.E.SetMode(ctx, r.GetStrategyId(), r.GetMode(), r.GetReason(), actor(ctx, r.GetActor()).GetName())
	return &ordersv1.SetStrategyModeResponse{Strategy: st}, grpcErr(err)
}

// GetRiskLimits implements ExecutionService.
func (s *Server) GetRiskLimits(_ context.Context, r *ordersv1.GetRiskLimitsRequest) (*ordersv1.GetRiskLimitsResponse, error) {
	l, eff := s.E.Limits(r.GetScope())
	return &ordersv1.GetRiskLimitsResponse{Scope: r.GetScope(), Limits: l, Effective: eff}, nil
}

// SetRiskLimits implements ExecutionService.
func (s *Server) SetRiskLimits(ctx context.Context, r *ordersv1.SetRiskLimitsRequest) (*ordersv1.SetRiskLimitsResponse, error) {
	raised, err := s.E.SetLimits(ctx, r.GetScope(), r.GetLimits(), actor(ctx, r.GetActor()))
	return &ordersv1.SetRiskLimitsResponse{Limits: r.GetLimits(), Raised: raised}, grpcErr(err)
}

// DemoteStrategy implements ExecutionService (RM-018): reduce-only automatic containment.
func (s *Server) DemoteStrategy(ctx context.Context, r *ordersv1.DemoteStrategyRequest) (*ordersv1.DemoteStrategyResponse, error) {
	resp, err := s.E.Demote(ctx, r.GetStrategyId(), r.GetReason(), r.GetEvidenceId(), actor(ctx, r.GetActor()))
	return resp, grpcErr(err)
}

// TriggerKillSwitch implements ExecutionService.
func (s *Server) TriggerKillSwitch(ctx context.Context, r *ordersv1.TriggerKillSwitchRequest) (*ordersv1.TriggerKillSwitchResponse, error) {
	resp, err := s.E.Trigger(ctx, r.GetScope(), r.GetTarget(), r.GetAction(), r.GetReason(), actor(ctx, r.GetActor()))
	return resp, grpcErr(err)
}

// ReleaseKillSwitch implements ExecutionService.
func (s *Server) ReleaseKillSwitch(ctx context.Context, r *ordersv1.ReleaseKillSwitchRequest) (*ordersv1.ReleaseKillSwitchResponse, error) {
	k, err := s.E.Release(ctx, r.GetEventId(), r.GetReason(), actor(ctx, r.GetActor()))
	return &ordersv1.ReleaseKillSwitchResponse{KillSwitch: k}, grpcErr(err)
}

// ListKillSwitches implements ExecutionService.
func (s *Server) ListKillSwitches(_ context.Context, r *ordersv1.ListKillSwitchesRequest) (*ordersv1.ListKillSwitchesResponse, error) {
	return &ordersv1.ListKillSwitchesResponse{KillSwitches: s.E.KillSwitches(!r.GetActiveOnly())}, nil
}

// GetTradingStatus implements ExecutionService.
func (s *Server) GetTradingStatus(context.Context, *ordersv1.GetTradingStatusRequest) (*ordersv1.GetTradingStatusResponse, error) {
	return &ordersv1.GetTradingStatusResponse{Status: s.E.Status()}, nil
}
