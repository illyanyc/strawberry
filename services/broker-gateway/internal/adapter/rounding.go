package adapter

import (
	"github.com/shopspring/decimal"

	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
)

var (
	pennyTick    = decimal.RequireFromString("0.01")
	subPennyTick = decimal.RequireFromString("0.0001")
	one          = decimal.NewFromInt(1)
)

// Tick returns the US-equity minimum price increment for price (Reg NMS Rule 612: $0.01 at or
// above $1.00, $0.0001 below), unless the instrument reports a larger tick.
func Tick(price decimal.Decimal, instrumentTick decimal.Decimal) decimal.Decimal {
	t := pennyTick
	if price.LessThan(one) {
		t = subPennyTick
	}
	if instrumentTick.GreaterThan(t) {
		return instrumentTick
	}
	return t
}

func roundDown(p, tick decimal.Decimal) decimal.Decimal { return p.Div(tick).Floor().Mul(tick) }
func roundUp(p, tick decimal.Decimal) decimal.Decimal   { return p.Div(tick).Ceil().Mul(tick) }

// RoundPrices returns a copy of req with prices on the tick grid using explicit modes (BR-017,
// OE-007): limits never become more aggressive (buy limits round down, sell limits round up) and
// stops round away from the market (buy stops up, sell stops down). Bracket legs follow the exit
// side: take-profit is a limit on the opposite side, stop-loss a stop on the opposite side.
func RoundPrices(req *brokerv1.BrokerOrderRequest, instrumentTick decimal.Decimal) *brokerv1.BrokerOrderRequest {
	out := proto.Clone(req).(*brokerv1.BrokerOrderRequest)
	buy := req.GetSide() == commonv1.Side_SIDE_BUY
	lim := func(d *commonv1.Decimal, buySide bool) *commonv1.Decimal {
		v, ok, err := Dec(d)
		if !ok || err != nil {
			return d
		}
		t := Tick(v, instrumentTick)
		if buySide {
			return D(roundDown(v, t))
		}
		return D(roundUp(v, t))
	}
	stop := func(d *commonv1.Decimal, buySide bool) *commonv1.Decimal {
		v, ok, err := Dec(d)
		if !ok || err != nil {
			return d
		}
		t := Tick(v, instrumentTick)
		if buySide {
			return D(roundUp(v, t))
		}
		return D(roundDown(v, t))
	}
	out.LimitPrice = lim(req.GetLimitPrice(), buy)
	out.StopPrice = stop(req.GetStopPrice(), buy)
	if b := out.GetBracket(); b != nil {
		b.TakeProfitLimitPrice = lim(b.GetTakeProfitLimitPrice(), !buy)
		b.StopLossStopPrice = stop(b.GetStopLossStopPrice(), !buy)
		b.StopLossLimitPrice = lim(b.GetStopLossLimitPrice(), !buy)
	}
	return out
}
