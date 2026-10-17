package contracts_test

import (
	"testing"

	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// Hand-written (not generated): proves the generated Go package compiles and round-trips.
func TestOrderIntentRoundTrip(t *testing.T) {
	in := &ordersv1.OrderIntent{
		IntentId:        "01JABCDEFGHJKMNPQRSTVWXYZ0",
		StrategyId:      "manual",
		BrokerAccountId: "alpaca-paper-1",
		InstrumentId:    "01JINSTRUMENT0000000000000",
		Side:            commonv1.Side_SIDE_BUY,
		Sizing:          &ordersv1.OrderIntent_Quantity{Quantity: &commonv1.Decimal{Value: "10"}},
		OrderType:       ordersv1.OrderType_ORDER_TYPE_LIMIT,
		LimitPrice:      &commonv1.Decimal{Value: "123.45"},
		Mode:            commonv1.Mode_MODE_PAPER,
	}
	b, err := proto.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	out := &ordersv1.OrderIntent{}
	if err := proto.Unmarshal(b, out); err != nil {
		t.Fatal(err)
	}
	if !proto.Equal(in, out) {
		t.Fatalf("round trip mismatch: %v vs %v", in, out)
	}
}
