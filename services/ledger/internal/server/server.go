// Package server implements tb.ledger.v1.LedgerService over the engine (section 06).
package server

import (
	"context"
	"errors"

	"github.com/shopspring/decimal"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/ledger/internal/book"
	"github.com/illyanyc/strawberry/services/ledger/internal/engine"
)

const svc = "/tb.ledger.v1.LedgerService/"

// AllowedCallers is the per-method caller allowlist (SE-040): execution uses the order-path
// queries; api-gateway the views and owner actions (step-up enforced there, RM-007).
var AllowedCallers = map[string][]string{
	svc + "ListAccounts":              {"api-gateway", "execution"},
	svc + "GetAccountView":            {"api-gateway", "execution"},
	svc + "GetStrategyPortfolio":      {"api-gateway", "execution"},
	svc + "GetOwnershipConstraints":   {"execution", "api-gateway"},
	svc + "GetCapitalAvailability":    {"execution", "api-gateway"},
	svc + "ReserveForOrder":           {"execution"},
	svc + "ReleaseReservation":        {"execution"},
	svc + "RegisterOwner":             {"execution"},
	svc + "SetAllocation":             {"api-gateway"},
	svc + "Reconcile":                 {"api-gateway", "execution"},
	svc + "ListBreaks":                {"api-gateway", "execution"},
	svc + "ResolveBreak":              {"api-gateway"},
	svc + "GetEquityCurve":            {"api-gateway"},
	svc + "ListLots":                  {"api-gateway"},
	svc + "ListWashSaleFlags":         {"api-gateway"},
	svc + "ListReconciliationReports": {"api-gateway"},
}

// Server is the gRPC service.
type Server struct {
	ledgerv1.UnimplementedLedgerServiceServer
	E *engine.Engine
}

func d(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }

func pd(x *commonv1.Decimal) (decimal.Decimal, error) {
	if x == nil || x.GetValue() == "" {
		return decimal.Zero, nil
	}
	return decimal.NewFromString(x.GetValue())
}

func grpcErr(err error) error {
	switch {
	case err == nil:
		return nil
	case errors.Is(err, engine.ErrNotFound):
		return status.Error(codes.NotFound, err.Error())
	case errors.Is(err, engine.ErrRejected):
		return status.Error(codes.FailedPrecondition, err.Error())
	case engine.IsBadInput(err):
		return status.Error(codes.InvalidArgument, err.Error())
	default:
		return status.Error(codes.Internal, "ledger error")
	}
}

func actor(ctx context.Context, given string) string {
	if given != "" {
		return given
	}
	if c := svcauth.Caller(ctx); c != "" {
		return "service:" + c
	}
	return "unknown"
}

// ListAccounts implements LedgerService.
func (s *Server) ListAccounts(context.Context, *ledgerv1.ListAccountsRequest) (*ledgerv1.ListAccountsResponse, error) {
	return &ledgerv1.ListAccountsResponse{Accounts: s.E.Accounts()}, nil
}

// GetAccountView implements LedgerService.
func (s *Server) GetAccountView(_ context.Context, r *ledgerv1.GetAccountViewRequest) (*ledgerv1.GetAccountViewResponse, error) {
	v := s.E.AccountView(r.GetBrokerAccountId())
	if v == nil {
		return nil, status.Error(codes.NotFound, "unknown account")
	}
	return &ledgerv1.GetAccountViewResponse{View: v}, nil
}

// GetStrategyPortfolio implements LedgerService.
func (s *Server) GetStrategyPortfolio(_ context.Context, r *ledgerv1.GetStrategyPortfolioRequest) (*ledgerv1.GetStrategyPortfolioResponse, error) {
	acct, v := s.E.StrategyPortfolio(r.GetStrategyId(), "")
	if v == nil {
		return nil, status.Error(codes.NotFound, "unknown strategy")
	}
	return &ledgerv1.GetStrategyPortfolioResponse{BrokerAccountId: acct, Portfolio: v}, nil
}

// GetOwnershipConstraints implements LedgerService (VP-003..VP-005).
func (s *Server) GetOwnershipConstraints(_ context.Context, r *ledgerv1.GetOwnershipConstraintsRequest) (*ledgerv1.GetOwnershipConstraintsResponse, error) {
	c := s.E.Constraints(r.GetBrokerAccountId(), r.GetInstrumentId(), r.GetOwner())
	return &ledgerv1.GetOwnershipConstraintsResponse{Constraints: &ledgerv1.OwnershipConstraints{StrategyQty: d(c.StrategyQty), OtherOwnersQty: d(c.OtherOwnersQty),
		AccountQty: d(c.AccountQty), MaxSellableByStrategy: d(c.MaxSellable), ShortAllowed: c.ShortAllowed, Reason: c.Reason, ManualQty: d(c.ManualQty), Halted: c.Halted}}, nil
}

func capProto(c book.Capital) *ledgerv1.CapitalAvailability {
	return &ledgerv1.CapitalAvailability{Allocation: d(c.Allocation), CashAvailable: d(c.CashAvailable), BuyingPowerAvailable: d(c.BuyingPowerAvailable),
		ReservedForOpenOrders: d(c.Reserved)}
}

// GetCapitalAvailability implements LedgerService.
func (s *Server) GetCapitalAvailability(_ context.Context, r *ledgerv1.GetCapitalAvailabilityRequest) (*ledgerv1.GetCapitalAvailabilityResponse, error) {
	acct := r.GetBrokerAccountId()
	if acct == "" {
		acct = s.E.AccountForOwner(r.GetOwner())
	}
	return &ledgerv1.GetCapitalAvailabilityResponse{Capital: capProto(s.E.Capital(acct, r.GetOwner()))}, nil
}

// ReserveForOrder implements LedgerService (VP-020). A business rejection is a normal response.
func (s *Server) ReserveForOrder(ctx context.Context, r *ledgerv1.ReserveForOrderRequest) (*ledgerv1.ReserveForOrderResponse, error) {
	qty, err1 := pd(r.GetQty())
	px, err2 := pd(r.GetEstPrice())
	if err := errors.Join(err1, err2); err != nil || r.GetIntentId() == "" {
		return nil, status.Error(codes.InvalidArgument, "intent_id, qty, and est_price are required decimals")
	}
	side := r.GetSide()
	res, err := s.E.Reserve(ctx, book.Reservation{IntentID: r.GetIntentId(), Account: r.GetBrokerAccountId(), Owner: r.GetOwner(),
		InstrumentID: r.GetInstrumentId(), Buy: side == commonv1.Side_SIDE_BUY, Qty: qty, EstPrice: px}, side == commonv1.Side_SIDE_SELL_SHORT)
	capital := capProto(s.E.Capital(r.GetBrokerAccountId(), r.GetOwner()))
	if errors.Is(err, engine.ErrRejected) {
		return &ledgerv1.ReserveForOrderResponse{Reserved: false, RejectReason: err.Error(), Capital: capital}, nil
	}
	if err != nil {
		return nil, grpcErr(err)
	}
	return &ledgerv1.ReserveForOrderResponse{Reserved: true, ReservationId: res.ID, Capital: capital}, nil
}

// ReleaseReservation implements LedgerService.
func (s *Server) ReleaseReservation(ctx context.Context, r *ledgerv1.ReleaseReservationRequest) (*ledgerv1.ReleaseReservationResponse, error) {
	ok, err := s.E.Release(ctx, r.GetReservationId(), r.GetIntentId())
	return &ledgerv1.ReleaseReservationResponse{Released: ok}, grpcErr(err)
}

// RegisterOwner implements LedgerService (VP-012).
func (s *Server) RegisterOwner(ctx context.Context, r *ledgerv1.RegisterOwnerRequest) (*ledgerv1.RegisterOwnerResponse, error) {
	if err := s.E.RegisterOwner(ctx, r.GetOwner(), r.GetShortId(), r.GetBrokerAccountId(), r.GetAllowShort(), r.GetDedicatedAccount(), actor(ctx, "")); err != nil {
		return nil, grpcErr(err)
	}
	_, v := s.E.StrategyPortfolio(r.GetOwner(), r.GetBrokerAccountId())
	return &ledgerv1.RegisterOwnerResponse{Owner: v}, nil
}

// SetAllocation implements LedgerService (VP-009).
func (s *Server) SetAllocation(ctx context.Context, r *ledgerv1.SetAllocationRequest) (*ledgerv1.SetAllocationResponse, error) {
	amt, err := pd(r.GetAmount())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "amount must be a decimal")
	}
	prev, unalloc, err := s.E.SetAllocation(ctx, r.GetOwner(), r.GetBrokerAccountId(), amt, actor(ctx, r.GetActor()), r.GetCorrelationId())
	if err != nil {
		return nil, grpcErr(err)
	}
	return &ledgerv1.SetAllocationResponse{Allocation: d(amt), Previous: d(prev), Unallocated: d(unalloc)}, nil
}

// Reconcile implements LedgerService.
func (s *Server) Reconcile(ctx context.Context, r *ledgerv1.ReconcileRequest) (*ledgerv1.ReconcileResponse, error) {
	trigger := r.GetTrigger()
	if trigger == "" {
		trigger = "on_demand"
	}
	res, err := s.E.Reconcile(ctx, r.GetBrokerAccountId(), trigger, "")
	return &ledgerv1.ReconcileResponse{Result: res}, grpcErr(err)
}

// ListBreaks implements LedgerService.
func (s *Server) ListBreaks(_ context.Context, r *ledgerv1.ListBreaksRequest) (*ledgerv1.ListBreaksResponse, error) {
	return &ledgerv1.ListBreaksResponse{Breaks: s.E.Breaks(r.GetBrokerAccountId(), r.GetOpenOnly())}, nil
}

// ResolveBreak implements LedgerService (VP-019).
func (s *Server) ResolveBreak(ctx context.Context, r *ledgerv1.ResolveBreakRequest) (*ledgerv1.ResolveBreakResponse, error) {
	qty, err := pd(r.GetQty())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, "qty must be a decimal")
	}
	if _, err := s.E.ResolveBreak(ctx, r.GetBreakId(), r.GetResolution(), r.GetFromOwner(), qty, actor(ctx, r.GetActor()), r.GetNote(), r.GetCorrelationId()); err != nil {
		return nil, grpcErr(err)
	}
	for _, b := range s.E.Breaks("", false) {
		if b.GetBreakId() == r.GetBreakId() {
			return &ledgerv1.ResolveBreakResponse{Resolved: b}, nil
		}
	}
	return &ledgerv1.ResolveBreakResponse{}, nil
}

// GetEquityCurve implements LedgerService.
func (s *Server) GetEquityCurve(ctx context.Context, r *ledgerv1.GetEquityCurveRequest) (*ledgerv1.GetEquityCurveResponse, error) {
	pts, err := s.E.EquityCurve(ctx, r.GetBrokerAccountId(), r.GetOwner(), r.GetStart(), r.GetEnd())
	return &ledgerv1.GetEquityCurveResponse{Points: pts}, grpcErr(err)
}

// ListLots implements LedgerService (VP-021).
func (s *Server) ListLots(_ context.Context, r *ledgerv1.ListLotsRequest) (*ledgerv1.ListLotsResponse, error) {
	return &ledgerv1.ListLotsResponse{Lots: s.E.Lots(r.GetBrokerAccountId(), r.GetOwner(), r.GetInstrumentId())}, nil
}

// ListWashSaleFlags implements LedgerService (VP-022).
func (s *Server) ListWashSaleFlags(ctx context.Context, r *ledgerv1.ListWashSaleFlagsRequest) (*ledgerv1.ListWashSaleFlagsResponse, error) {
	f, err := s.E.WashSales(ctx, r.GetSince())
	return &ledgerv1.ListWashSaleFlagsResponse{Flags: f}, grpcErr(err)
}

// ListReconciliationReports implements LedgerService (VP-025).
func (s *Server) ListReconciliationReports(ctx context.Context, r *ledgerv1.ListReconciliationReportsRequest) (*ledgerv1.ListReconciliationReportsResponse, error) {
	rep, err := s.E.Reports(ctx, r.GetBrokerAccountId(), r.GetSince(), int(r.GetLimit()))
	return &ledgerv1.ListReconciliationReportsResponse{Reports: rep}, grpcErr(err)
}
