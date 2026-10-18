package oms

import (
	"context"
	"fmt"
	"math/rand/v2"
	"testing"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

type nopLedger struct{}

func (nopLedger) Constraints(context.Context, string, string, string) (*ledgerv1.OwnershipConstraints, error) {
	return &ledgerv1.OwnershipConstraints{}, nil
}
func (nopLedger) Reserve(context.Context, *ledgerv1.ReserveForOrderRequest) (*ledgerv1.ReserveForOrderResponse, error) {
	return &ledgerv1.ReserveForOrderResponse{Reserved: true}, nil
}
func (nopLedger) Release(context.Context, string) error { return nil }
func (nopLedger) Portfolio(context.Context, string) (*ledgerv1.OwnerView, error) {
	return &ledgerv1.OwnerView{}, nil
}
func (nopLedger) RegisterOwner(context.Context, *ledgerv1.RegisterOwnerRequest) error { return nil }
func (nopLedger) SetAllocation(context.Context, *ledgerv1.SetAllocationRequest) error { return nil }

// OE-006, OE-I2: random orders of broker updates for one order, with duplicates and out-of-order
// partial fills, always converge to the final state and the largest filled quantity; states never
// move backwards.
func TestOE006StateMachineConvergesUnderReordering(t *testing.T) {
	for seed := uint64(1); seed <= 300; seed++ {
		rng := rand.New(rand.NewPCG(seed, 3))
		e, err := New(context.Background(), DefaultConfig(), NewMemory(), nil, nopLedger{}, StaticInstruments{})
		if err != nil {
			t.Fatal(err)
		}
		coid := "tb1-orbaaaa-01J00000000000000000000001-1"
		o := &ordersv1.Order{OrderId: "o1", IntentId: "i1", ClientOrderId: coid, BrokerAccountId: "a", BrokerOrderId: "b1", StrategyId: "orb",
			InstrumentId: "X", Side: commonv1.Side_SIDE_BUY, Quantity: dec("100"), State: ordersv1.OrderState_ORDER_STATE_SUBMITTED, FilledQty: dec("0")}
		e.index(o)
		var ups []*brokerv1.BrokerOrderUpdate
		mk := func(st ordersv1.OrderState, filled string, at int64) *brokerv1.BrokerOrderUpdate {
			return &brokerv1.BrokerOrderUpdate{BrokerAccountId: "a", Order: &brokerv1.BrokerOrder{BrokerOrderId: "b1", ClientOrderId: coid, State: st,
				FilledQty: dec(filled), AvgFillPrice: dec("10"), UpdatedAt: at}}
		}
		ups = append(ups, mk(ordersv1.OrderState_ORDER_STATE_ACCEPTED, "0", 1))
		n := 1 + rng.IntN(5)
		for i := 1; i <= n; i++ {
			st := ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED
			if i == n {
				st = ordersv1.OrderState_ORDER_STATE_FILLED
			}
			ups = append(ups, mk(st, fmt.Sprint(i*100/n), int64(i+1)))
		}
		for i := 0; i < 3; i++ {
			ups = append(ups, ups[rng.IntN(len(ups))])
		}
		rng.Shuffle(len(ups), func(i, j int) { ups[i], ups[j] = ups[j], ups[i] })
		last := rank(o.GetState())
		for _, u := range ups {
			if err := e.OnOrderUpdate(context.Background(), u); err != nil {
				t.Fatal(err)
			}
			if r := rank(o.GetState()); r < last {
				t.Fatalf("seed %d: state regressed to %s", seed, o.GetState())
			} else {
				last = r
			}
		}
		if o.GetState() != ordersv1.OrderState_ORDER_STATE_FILLED || o.GetFilledQty().GetValue() != "100" {
			t.Fatalf("seed %d: final %s filled %s", seed, o.GetState(), o.GetFilledQty().GetValue())
		}
	}
}
