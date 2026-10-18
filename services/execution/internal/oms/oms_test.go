package oms

import (
	"context"
	"fmt"
	"math/rand/v2"
	"testing"
	"time"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
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

// BT-003: Compact drops only finished history older than the cut-off; working orders, their intent,
// recent fills, and recent order times stay.
func TestCompactKeepsWorkingAndRecentState(t *testing.T) {
	e, err := New(context.Background(), DefaultConfig(), NewMemory(), nil, nopLedger{}, StaticInstruments{})
	if err != nil {
		t.Fatal(err)
	}
	mk := func(id, intent, parent string, st ordersv1.OrderState, at int64) *ordersv1.Order {
		o := &ordersv1.Order{OrderId: id, IntentId: intent, ParentOrderId: parent, ClientOrderId: "tb1-orbaaaa-" + intent + "-1", BrokerAccountId: "a",
			BrokerOrderId: "b" + id, State: st, UpdatedAt: at}
		e.index(o)
		return o
	}
	filled, canceled, open := ordersv1.OrderState_ORDER_STATE_FILLED, ordersv1.OrderState_ORDER_STATE_CANCELED, ordersv1.OrderState_ORDER_STATE_ACCEPTED
	mk("old", "i1", "", filled, 10)         // finished, old: dropped
	mk("oldleg", "i1", "old", canceled, 10) // finished leg, old: dropped
	mk("entry", "i2", "", filled, 10)       // old but its stop still works: kept
	mk("stop", "i2", "entry", open, 10)     // working: kept
	mk("recent", "i3", "", filled, 1000)    // recent: kept
	e.intents["i1"] = &ordersv1.IntentRecord{State: IntentDone, ReceivedAt: 10}
	e.intents["i2"] = &ordersv1.IntentRecord{State: IntentDone, ReceivedAt: 10}
	e.intents["i4"] = &ordersv1.IntentRecord{State: IntentSubmitted, ReceivedAt: 10}
	e.fills["a|x1"] = &ordersv1.Fill{EventTime: 10}
	e.fills["a|x2"] = &ordersv1.Fill{EventTime: 1000}
	e.orderTimes["orb"] = []int64{10, 1000}
	if n := e.Compact(500); n != 4 {
		t.Fatalf("compacted %d entries, want 4 (two orders, one intent, one fill)", n)
	}
	for _, id := range []string{"entry", "stop", "recent"} {
		if e.orders[id] == nil {
			t.Fatalf("order %s dropped", id)
		}
	}
	if e.orders["old"] != nil || e.byBroker["a|bold"] != "" || len(e.byClient["a|tb1-orbaaaa-i1-1"]) != 0 {
		t.Fatal("old finished order still indexed")
	}
	if e.intents["i1"] != nil || e.intents["i2"] == nil || e.intents["i4"] == nil || e.fills["a|x2"] == nil || len(e.orderTimes["orb"]) != 1 {
		t.Fatal("wrong intents, fills, or order times kept")
	}
}

// RM-004: daily-bar freshness counts trading sessions, so a weekend or holiday is not a missing bar;
// intraday bars keep the wall-clock bound (two intervals plus finality).
func TestRM004DailyFreshnessCountsSessions(t *testing.T) {
	cal := calendar.XNYS()
	day, fin := int64(24*time.Hour), int64(time.Minute)
	closeOf := func(d string) int64 {
		s, ok := cal.Session(d)
		if !ok {
			t.Fatalf("no session %s", d)
		}
		return s.Close.UnixNano()
	}
	openOf := func(d string) int64 {
		s, _ := cal.Session(d)
		return s.Open.UnixNano() + fin
	}
	fresh := func(now, barClose, interval int64) bool { return now-barClose <= maxBarAge(cal, now, interval, fin) }
	cases := []struct {
		name     string
		now, bar int64
		want     bool
	}{
		{"monday open, friday bar", openOf("2026-02-09"), closeOf("2026-02-06"), true},
		{"monday open, thursday bar (one bar missing)", openOf("2026-02-09"), closeOf("2026-02-05"), true},
		{"monday open, wednesday bar (two missing)", openOf("2026-02-09"), closeOf("2026-02-04"), false},
		{"after presidents day, friday bar", openOf("2026-02-17"), closeOf("2026-02-13"), true},
		{"after presidents day, wednesday bar", openOf("2026-02-17"), closeOf("2026-02-11"), false},
		{"mid-week, yesterday's bar", openOf("2026-02-11"), closeOf("2026-02-10"), true},
		{"mid-week, three sessions old", openOf("2026-02-11"), closeOf("2026-02-06"), false},
	}
	for _, c := range cases {
		if got := fresh(c.now, c.bar, day); got != c.want {
			t.Errorf("%s: fresh=%v, want %v", c.name, got, c.want)
		}
	}
	m15 := int64(15 * time.Minute)
	if got := maxBarAge(cal, openOf("2026-02-09"), m15, fin); got != 2*m15+fin {
		t.Errorf("intraday bound changed: %d", got)
	}
	if fresh(openOf("2026-02-09"), closeOf("2026-02-06"), m15) {
		t.Error("a friday 15m bar must be stale at monday's open")
	}
}
