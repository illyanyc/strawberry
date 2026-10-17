package credsvc

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"fmt"
	"net"
	"strings"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/grpc/test/bufconn"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/credstore"
)

// failingStore simulates a database error whose message echoes the secret (as some drivers do).
type failingStore struct{}

func (failingStore) Put(_ context.Context, _, _, _ string, _ credstore.Environment, secret map[string]string, _ audit.Event) (credstore.Status, error) {
	return credstore.Status{}, fmt.Errorf("pq: value too long for column: %q", secret["api_secret"])
}
func (failingStore) Delete(context.Context, string, credstore.Environment, audit.Event) error {
	return fmt.Errorf("boom")
}
func (failingStore) List(context.Context) ([]credstore.Status, error) { return nil, nil }

func signer(t *testing.T, name string) (*svcauth.Signer, ed25519.PublicKey) {
	seed, pub, err := svcauth.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	s, err := svcauth.NewSigner(name, seed)
	if err != nil {
		t.Fatal(err)
	}
	return s, pub
}

func start(t *testing.T, store Store, trusted map[string]ed25519.PublicKey, logs *bytes.Buffer) brokerv1.CredentialServiceClient {
	lis := bufconn.Listen(1 << 20)
	v := svcauth.NewVerifier("broker-gateway", trusted)
	gs := grpc.NewServer(grpc.UnaryInterceptor(v.UnaryServerInterceptor(AllowedCallers)))
	brokerv1.RegisterCredentialServiceServer(gs, New(store, "owner", logging.New("broker-gateway", "debug", logs)))
	go func() { _ = gs.Serve(lis) }()
	t.Cleanup(gs.Stop)
	conn, err := grpc.NewClient("passthrough:///buf", grpc.WithContextDialer(func(context.Context, string) (net.Conn, error) { return lis.Dial() }),
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return brokerv1.NewCredentialServiceClient(conn)
}

func putReq(secret string) *brokerv1.PutCredentialRequest {
	return &brokerv1.PutCredentialRequest{BrokerAccountId: "alpaca-paper-1", Broker: commonv1.Broker_BROKER_ALPACA,
		Environment: brokerv1.Environment_ENVIRONMENT_PAPER, OnBehalfOf: "owner",
		Secret: map[string]string{"api_key_id": "PKCCCCCCCCCCCCCCCCCC", "api_secret": secret}}
}

// SE-040: only api-gateway may call the credential service; SE-012: secrets never leak through the
// error path into logs or gRPC errors.
func TestSE040CallerAllowlistAndSE012ErrorPath(t *testing.T) {
	gw, gwPub := signer(t, "api-gateway")
	ledger, ledgerPub := signer(t, "ledger")
	var logs bytes.Buffer
	c := start(t, failingStore{}, map[string]ed25519.PublicKey{"api-gateway": gwPub, "ledger": ledgerPub}, &logs)
	ctx := context.Background()
	const secret = "leak-canary-secret-0042"

	if _, err := c.PutCredential(ctx, putReq(secret)); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("no token: %v", err)
	}
	if _, err := c.PutCredential(ctx, putReq(secret), ledger.PerRPC("broker-gateway")); status.Code(err) != codes.PermissionDenied {
		t.Fatalf("ledger caller: %v", err)
	}
	if _, err := c.ListCredentialStatus(ctx, &brokerv1.ListCredentialStatusRequest{}, gw.PerRPC("ledger")); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("token for another audience: %v", err)
	}
	_, err := c.PutCredential(ctx, putReq(secret), gw.PerRPC("broker-gateway"))
	if status.Code(err) != codes.Internal {
		t.Fatalf("expected internal error, got %v", err)
	}
	for where, text := range map[string]string{"grpc error": err.Error(), "logs": logs.String()} {
		if strings.Contains(text, secret) || strings.Contains(text, "PKCCCCCCCCCCCCCCCCCC") {
			t.Fatalf("SE-012: secret leaked via %s: %s", where, text)
		}
	}
	if !strings.Contains(logs.String(), "store credential failed") {
		t.Fatalf("error not logged: %s", logs.String())
	}
	bad := putReq(secret)
	bad.Environment = brokerv1.Environment_ENVIRONMENT_UNSPECIFIED
	if _, err := c.PutCredential(ctx, bad, gw.PerRPC("broker-gateway")); status.Code(err) != codes.InvalidArgument || strings.Contains(err.Error(), secret) {
		t.Fatalf("invalid env: %v", err)
	}
}
