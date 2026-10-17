package gateway

import (
	"context"
	"net/http"
	"regexp"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// CredentialClient is the subset of broker-gateway's CredentialService the gateway uses. There is
// no read-plaintext method (SE-031).
type CredentialClient interface {
	Put(ctx context.Context, req *brokerv1.PutCredentialRequest) (*brokerv1.CredentialStatus, error)
	Delete(ctx context.Context, req *brokerv1.DeleteCredentialRequest) error
	List(ctx context.Context) ([]*brokerv1.CredentialStatus, error)
}

// GRPCCredentialClient calls broker-gateway with a service token on every call (SE-040).
type GRPCCredentialClient struct {
	c      brokerv1.CredentialServiceClient
	signer *svcauth.Signer
}

// DialCredentialClient connects to broker-gateway on the internal network.
func DialCredentialClient(addr string, signer *svcauth.Signer) (*GRPCCredentialClient, *grpc.ClientConn, error) {
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, nil, err
	}
	return &GRPCCredentialClient{c: brokerv1.NewCredentialServiceClient(conn), signer: signer}, conn, nil
}

func (g *GRPCCredentialClient) tok() grpc.CallOption { return g.signer.PerRPC("broker-gateway") }

// Put implements CredentialClient.
func (g *GRPCCredentialClient) Put(ctx context.Context, req *brokerv1.PutCredentialRequest) (*brokerv1.CredentialStatus, error) {
	resp, err := g.c.PutCredential(ctx, req, g.tok())
	if err != nil {
		return nil, err
	}
	return resp.GetStatus(), nil
}

// Delete implements CredentialClient.
func (g *GRPCCredentialClient) Delete(ctx context.Context, req *brokerv1.DeleteCredentialRequest) error {
	_, err := g.c.DeleteCredential(ctx, req, g.tok())
	return err
}

// List implements CredentialClient.
func (g *GRPCCredentialClient) List(ctx context.Context) ([]*brokerv1.CredentialStatus, error) {
	resp, err := g.c.ListCredentialStatus(ctx, &brokerv1.ListCredentialStatusRequest{}, g.tok())
	if err != nil {
		return nil, err
	}
	return resp.GetCredentials(), nil
}

var accountIDRe = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{1,63}$`)

func parseEnv(s string) (brokerv1.Environment, bool) {
	switch s {
	case "paper":
		return brokerv1.Environment_ENVIRONMENT_PAPER, true
	case "live":
		return brokerv1.Environment_ENVIRONMENT_LIVE, true
	default:
		return brokerv1.Environment_ENVIRONMENT_UNSPECIFIED, false
	}
}

func parseBroker(s string) (commonv1.Broker, bool) {
	switch strings.ToLower(s) {
	case "alpaca":
		return commonv1.Broker_BROKER_ALPACA, true
	case "ibkr":
		return commonv1.Broker_BROKER_IBKR, true
	default:
		return commonv1.Broker_BROKER_UNSPECIFIED, false
	}
}

func statusView(c *brokerv1.CredentialStatus) map[string]any {
	env := "paper"
	if c.GetEnvironment() == brokerv1.Environment_ENVIRONMENT_LIVE {
		env = "live"
	}
	return map[string]any{
		"broker_account_id": c.GetBrokerAccountId(), "broker": strings.ToLower(strings.TrimPrefix(c.GetBroker().String(), "BROKER_")),
		"environment": env, "fields": c.GetFields(), "created_at": c.GetCreatedAt(), "rotated_at": c.GetRotatedAt(),
	}
}

func (s *Server) handleCredentialsList(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActBrokerCredStatus, policy.Resource{}) {
		return
	}
	if s.creds == nil {
		writeError(w, http.StatusServiceUnavailable, "broker_gateway_unavailable", "broker-gateway unavailable")
		return
	}
	list, err := s.creds.List(r.Context())
	if err != nil {
		s.grpcError(w, r, err)
		return
	}
	out := make([]map[string]any, 0, len(list))
	for _, c := range list {
		out = append(out, statusView(c))
	}
	writeJSON(w, http.StatusOK, map[string]any{"credentials": out})
}

type credentialPutReq struct {
	Broker string            `json:"broker"`
	Secret map[string]string `json:"secret"`
}

// handleCredentialsPut forwards write-only credential material to broker-gateway. The body is never
// logged; broker-gateway encrypts and audits it (SE-030, SE-031).
func (s *Server) handleCredentialsPut(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	env, ok := parseEnv(r.PathValue("environment"))
	account := r.PathValue("account")
	if !ok || !accountIDRe.MatchString(account) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid account or environment")
		return
	}
	if !s.authorize(w, rc, policy.ActBrokerCredWrite, policy.Resource{ID: account, Mode: r.PathValue("environment")}) {
		return
	}
	var req credentialPutReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid JSON body")
		return
	}
	for _, v := range req.Secret {
		logging.RegisterSecret(v)
	}
	defer func() {
		for _, v := range req.Secret {
			logging.UnregisterSecret(v)
		}
	}()
	broker, ok := parseBroker(req.Broker)
	if !ok || len(req.Secret) == 0 || len(req.Secret) > 8 {
		writeError(w, http.StatusBadRequest, "bad_request", "broker must be alpaca|ibkr and secret must have 1-8 fields")
		return
	}
	if s.creds == nil {
		writeError(w, http.StatusServiceUnavailable, "broker_gateway_unavailable", "broker-gateway unavailable")
		return
	}
	st, err := s.creds.Put(r.Context(), &brokerv1.PutCredentialRequest{BrokerAccountId: account, Broker: broker,
		Environment: env, Secret: req.Secret, OnBehalfOf: "owner", CorrelationId: logging.CorrelationID(r.Context())})
	if err != nil {
		s.grpcError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, statusView(st))
}

func (s *Server) handleCredentialsDelete(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	env, ok := parseEnv(r.PathValue("environment"))
	account := r.PathValue("account")
	if !ok || !accountIDRe.MatchString(account) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid account or environment")
		return
	}
	if !s.authorize(w, rc, policy.ActBrokerCredDelete, policy.Resource{ID: account, Mode: r.PathValue("environment")}) {
		return
	}
	if s.creds == nil {
		writeError(w, http.StatusServiceUnavailable, "broker_gateway_unavailable", "broker-gateway unavailable")
		return
	}
	if err := s.creds.Delete(r.Context(), &brokerv1.DeleteCredentialRequest{BrokerAccountId: account, Environment: env,
		OnBehalfOf: "owner", CorrelationId: logging.CorrelationID(r.Context())}); err != nil {
		s.grpcError(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) grpcError(w http.ResponseWriter, r *http.Request, err error) {
	switch status.Code(err) {
	case codes.InvalidArgument:
		writeError(w, http.StatusBadRequest, "bad_request", status.Convert(err).Message())
	case codes.NotFound:
		writeError(w, http.StatusNotFound, "not_found", "credential not found")
	case codes.Unavailable, codes.DeadlineExceeded:
		writeError(w, http.StatusServiceUnavailable, "broker_gateway_unavailable", "broker-gateway unavailable")
	default:
		s.internalError(w, r, "credential service", err)
	}
}
