package contract_test

import (
	"testing"

	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/contract"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/testkit"
)

func TestBR001ContractSimulator(t *testing.T) {
	s, acct, store, _ := testkit.SimAccount(t, sim.Config{AccountID: "sim-paper"})
	testkit.Run(t, acct)
	client := contract.InProcess(t, []*adapter.Account{acct}, "execution")
	contract.Run(t, contract.Target{Client: client, Account: "sim-paper", Symbol: "AAPL", FarLimit: "1.00",
		Hooks: testkit.SimHooks{Sim: s, Store: store, Acct: "sim-paper"}})
}
