// Package credsvc serves tb.broker.v1.CredentialService: write-only credential management for the
// api-gateway (SE-031). Callers are authenticated with service tokens (SE-040); plaintext never
// leaves this process and never appears in responses or logs (SE-012).
package credsvc

import (
	"context"
	"errors"
	"log/slog"
	"regexp"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/credstore"
)

// AllowedCallers is the per-method caller allowlist for the gRPC interceptor.
var AllowedCallers = map[string][]string{
	brokerv1.CredentialService_PutCredential_FullMethodName:        {"api-gateway"},
	brokerv1.CredentialService_DeleteCredential_FullMethodName:     {"api-gateway"},
	brokerv1.CredentialService_ListCredentialStatus_FullMethodName: {"api-gateway"},
}

// Store is the credential store the service needs.
type Store interface {
	Put(ctx context.Context, ownerID, broker, account string, env credstore.Environment, secret map[string]string, ev audit.Event) (credstore.Status, error)
	Delete(ctx context.Context, account string, env credstore.Environment, ev audit.Event) error
	List(ctx context.Context) ([]credstore.Status, error)
}

// Server implements brokerv1.CredentialServiceServer.
type Server struct {
	brokerv1.UnimplementedCredentialServiceServer
	store   Store
	ownerID string
	log     *slog.Logger
}

// New returns the service.
func New(store Store, ownerID string, log *slog.Logger) *Server {
	return &Server{store: store, ownerID: ownerID, log: log}
}

var (
	accountRe = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{1,63}$`)
	fieldRe   = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)
)

func envOf(e brokerv1.Environment) (credstore.Environment, bool) {
	switch e {
	case brokerv1.Environment_ENVIRONMENT_PAPER:
		return credstore.Paper, true
	case brokerv1.Environment_ENVIRONMENT_LIVE:
		return credstore.Live, true
	case brokerv1.Environment_ENVIRONMENT_UNSPECIFIED:
		return "", false
	default:
		return "", false
	}
}

func brokerName(b commonv1.Broker) (string, bool) {
	switch b {
	case commonv1.Broker_BROKER_ALPACA:
		return "alpaca", true
	case commonv1.Broker_BROKER_IBKR:
		return "ibkr", true
	case commonv1.Broker_BROKER_UNSPECIFIED, commonv1.Broker_BROKER_SIMULATOR:
		return "", false
	default:
		return "", false
	}
}

func (s *Server) event(ctx context.Context, action, account, onBehalfOf, corr string) audit.Event {
	actor := "owner"
	if onBehalfOf != "owner" {
		actor = "service:" + svcauth.Caller(ctx)
	}
	return audit.Event{Actor: actor, Action: action, Target: account, CorrelationID: corr,
		Details: map[string]any{"via": "service:" + svcauth.Caller(ctx)}}
}

// PutCredential implements the RPC.
func (s *Server) PutCredential(ctx context.Context, req *brokerv1.PutCredentialRequest) (*brokerv1.PutCredentialResponse, error) {
	for _, v := range req.GetSecret() {
		logging.RegisterSecret(v)
	}
	env, ok := envOf(req.GetEnvironment())
	broker, bok := brokerName(req.GetBroker())
	switch {
	case !accountRe.MatchString(req.GetBrokerAccountId()):
		return nil, status.Error(codes.InvalidArgument, "invalid broker_account_id")
	case !ok:
		return nil, status.Error(codes.InvalidArgument, "environment must be paper or live")
	case !bok:
		return nil, status.Error(codes.InvalidArgument, "broker must be alpaca or ibkr")
	case len(req.GetSecret()) == 0 || len(req.GetSecret()) > 8:
		return nil, status.Error(codes.InvalidArgument, "secret must have 1-8 fields")
	}
	for k, v := range req.GetSecret() {
		if !fieldRe.MatchString(k) || v == "" || len(v) > 4096 {
			return nil, status.Error(codes.InvalidArgument, "secret field names must be snake_case and values non-empty")
		}
	}
	ctx = logging.WithCorrelationID(ctx, req.GetCorrelationId())
	st, err := s.store.Put(ctx, s.ownerID, broker, req.GetBrokerAccountId(), env, req.GetSecret(),
		s.event(ctx, "broker.credentials.put", req.GetBrokerAccountId(), req.GetOnBehalfOf(), req.GetCorrelationId()))
	if err != nil {
		s.log.ErrorContext(ctx, "store credential failed", "account", req.GetBrokerAccountId(), "error", err)
		return nil, status.Error(codes.Internal, "could not store credential")
	}
	return &brokerv1.PutCredentialResponse{Status: toProto(st)}, nil
}

// DeleteCredential implements the RPC.
func (s *Server) DeleteCredential(ctx context.Context, req *brokerv1.DeleteCredentialRequest) (*brokerv1.DeleteCredentialResponse, error) {
	env, ok := envOf(req.GetEnvironment())
	if !ok || !accountRe.MatchString(req.GetBrokerAccountId()) {
		return nil, status.Error(codes.InvalidArgument, "invalid account or environment")
	}
	ctx = logging.WithCorrelationID(ctx, req.GetCorrelationId())
	err := s.store.Delete(ctx, req.GetBrokerAccountId(), env,
		s.event(ctx, "broker.credentials.delete", req.GetBrokerAccountId(), req.GetOnBehalfOf(), req.GetCorrelationId()))
	if errors.Is(err, credstore.ErrNotFound) {
		return nil, status.Error(codes.NotFound, "credential not found")
	}
	if err != nil {
		s.log.ErrorContext(ctx, "delete credential failed", "error", err)
		return nil, status.Error(codes.Internal, "could not delete credential")
	}
	return &brokerv1.DeleteCredentialResponse{}, nil
}

// ListCredentialStatus implements the RPC.
func (s *Server) ListCredentialStatus(ctx context.Context, _ *brokerv1.ListCredentialStatusRequest) (*brokerv1.ListCredentialStatusResponse, error) {
	list, err := s.store.List(ctx)
	if err != nil {
		s.log.ErrorContext(ctx, "list credentials failed", "error", err)
		return nil, status.Error(codes.Internal, "could not list credentials")
	}
	out := &brokerv1.ListCredentialStatusResponse{}
	for _, st := range list {
		out.Credentials = append(out.Credentials, toProto(st))
	}
	return out, nil
}

func toProto(st credstore.Status) *brokerv1.CredentialStatus {
	env := brokerv1.Environment_ENVIRONMENT_PAPER
	if st.Environment == credstore.Live {
		env = brokerv1.Environment_ENVIRONMENT_LIVE
	}
	b := commonv1.Broker_BROKER_UNSPECIFIED
	switch strings.ToLower(st.Broker) {
	case "alpaca":
		b = commonv1.Broker_BROKER_ALPACA
	case "ibkr":
		b = commonv1.Broker_BROKER_IBKR
	}
	return &brokerv1.CredentialStatus{BrokerAccountId: st.BrokerAccountID, Broker: b, Environment: env, Fields: st.Fields,
		CreatedAt: st.CreatedAt.UnixNano(), RotatedAt: st.RotatedAt.UnixNano(), KekId: st.KEKID}
}
