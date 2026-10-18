package contract_test

import (
	"encoding/base64"
	"os"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/contract"
)

// TestBR001ContractRemote runs the suite against any BrokerGatewayService endpoint, e.g. the Python
// IBKR adapter on its paper account. Opt-in:
//
//	TB_BROKER_CONTRACT_ADDR=host:port TB_BROKER_CONTRACT_ACCOUNT=ibkr-paper \
//	TB_BROKER_CONTRACT_SEED_FILE=.secrets/svc_execution_ed25519 [TB_BROKER_CONTRACT_SYMBOL=SPY]
//
// The seed file signs service tokens as "execution" (the only caller allowed to place orders).
func TestBR001ContractRemote(t *testing.T) {
	addr := os.Getenv("TB_BROKER_CONTRACT_ADDR")
	if addr == "" {
		t.Skip("TB_BROKER_CONTRACT_ADDR not set (opt-in remote adapter test)")
	}
	raw, err := os.ReadFile(os.Getenv("TB_BROKER_CONTRACT_SEED_FILE"))
	if err != nil {
		t.Fatalf("TB_BROKER_CONTRACT_SEED_FILE: %v", err)
	}
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil {
		t.Fatal(err)
	}
	signer, err := svcauth.NewSigner("execution", seed)
	if err != nil {
		t.Fatal(err)
	}
	aud := os.Getenv("TB_BROKER_CONTRACT_AUDIENCE")
	if aud == "" {
		aud = "broker-gateway-ibkr"
	}
	conn, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDefaultCallOptions(signer.PerRPC(aud)))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = conn.Close() }()
	sym := os.Getenv("TB_BROKER_CONTRACT_SYMBOL")
	if sym == "" {
		sym = "SPY"
	}
	contract.Run(t, contract.Target{Client: brokerv1.NewBrokerGatewayServiceClient(conn), Account: os.Getenv("TB_BROKER_CONTRACT_ACCOUNT"),
		Symbol: sym, FarLimit: "1.00", ReadyTimeout: 2 * time.Minute})
}
