package adapter

import (
	"slices"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
)

// Dec parses a contract decimal; empty means "unset" (ok=false).
func Dec(d *commonv1.Decimal) (decimal.Decimal, bool, error) {
	if d == nil || d.GetValue() == "" {
		return decimal.Zero, false, nil
	}
	v, err := decimal.NewFromString(d.GetValue())
	return v, true, err
}

// D formats a decimal for the contract.
func D(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }

func invalid(msg string) *RejectError {
	return Reject(brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER, true, "", msg)
}

// Validate checks an order request against the attribution format (BR-003) and the account's
// capabilities (BR-002) before anything is sent. It returns a local *RejectError.
func Validate(req *brokerv1.BrokerOrderRequest, caps *brokerv1.Capabilities, fractionable bool) *RejectError {
	if _, err := attribution.Parse(req.GetClientOrderId(), int(caps.GetMaxClientOrderIdLen())); err != nil {
		return Reject(brokerv1.RejectCode_REJECT_CODE_INVALID_ATTRIBUTION, true, "", err.Error())
	}
	if req.GetInstrument().GetSymbol() == "" && req.GetInstrument().GetAlpacaAssetId() == "" && req.GetInstrument().GetIbkrConId() == "" {
		return invalid("instrument has no broker identifier")
	}
	if !slices.Contains(caps.GetOrderTypes(), req.GetType()) {
		return invalid("order type " + req.GetType().String() + " not supported by account")
	}
	if !slices.Contains(caps.GetTifs(), req.GetTif()) {
		return invalid("time in force " + req.GetTif().String() + " not supported by account")
	}
	switch req.GetSide() {
	case commonv1.Side_SIDE_BUY, commonv1.Side_SIDE_SELL:
	case commonv1.Side_SIDE_SELL_SHORT:
		if !caps.GetSupportsShort() {
			return invalid("short selling not supported by account")
		}
	default:
		return invalid("side is required")
	}
	qty, ok, err := Dec(req.GetQty())
	if err != nil || !ok || !qty.IsPositive() {
		return invalid("quantity must be a positive decimal")
	}
	if !qty.IsInteger() {
		// BR-017: never round a quantity silently.
		if !caps.GetSupportsFractional() || !fractionable {
			return invalid("fractional quantity not supported for this instrument/account")
		}
		if req.GetType() != ordersv1.OrderType_ORDER_TYPE_MARKET && req.GetType() != ordersv1.OrderType_ORDER_TYPE_LIMIT {
			return invalid("fractional orders must be market or limit")
		}
		if req.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_DAY {
			return invalid("fractional orders must be DAY")
		}
	}
	lim, hasLim, err1 := Dec(req.GetLimitPrice())
	stop, hasStop, err2 := Dec(req.GetStopPrice())
	if err1 != nil || err2 != nil {
		return invalid("malformed price")
	}
	if (hasLim && !lim.IsPositive()) || (hasStop && !stop.IsPositive()) {
		return invalid("prices must be positive")
	}
	switch req.GetType() {
	case ordersv1.OrderType_ORDER_TYPE_MARKET:
		if hasLim || hasStop {
			return invalid("market order must not carry limit or stop price")
		}
	case ordersv1.OrderType_ORDER_TYPE_LIMIT:
		if !hasLim || hasStop {
			return invalid("limit order needs exactly a limit price")
		}
	case ordersv1.OrderType_ORDER_TYPE_STOP:
		if !hasStop || hasLim {
			return invalid("stop order needs exactly a stop price")
		}
	case ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT:
		if !hasStop || !hasLim {
			return invalid("stop-limit order needs stop and limit prices")
		}
	case ordersv1.OrderType_ORDER_TYPE_TRAILING_STOP:
		_, hasTP, e1 := Dec(req.GetTrailPrice())
		_, hasTPct, e2 := Dec(req.GetTrailPercent())
		if e1 != nil || e2 != nil || hasTP == hasTPct {
			return invalid("trailing stop needs exactly one of trail_price / trail_percent")
		}
		if !caps.GetSupportsTrailingStop() {
			return invalid("trailing stops not supported by account")
		}
	default:
		return invalid("order type is required")
	}
	if req.GetExtendedHours() {
		if !caps.GetSupportsExtendedHours() {
			return invalid("extended hours not supported by account")
		}
		if req.GetType() != ordersv1.OrderType_ORDER_TYPE_LIMIT ||
			(req.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_DAY && req.GetTif() != ordersv1.TimeInForce_TIME_IN_FORCE_GTC) {
			return invalid("extended-hours orders must be DAY/GTC limit orders")
		}
	}
	if b := req.GetBracket(); b != nil && b.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_UNSPECIFIED && b.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_SIMPLE {
		if req.GetExtendedHours() {
			return invalid("brackets are not allowed in extended hours")
		}
		if !qty.IsInteger() {
			return invalid("brackets need whole-share quantities")
		}
		tp, hasTP, e1 := Dec(b.GetTakeProfitLimitPrice())
		sl, hasSL, e2 := Dec(b.GetStopLossStopPrice())
		if e1 != nil || e2 != nil {
			return invalid("malformed bracket price")
		}
		switch b.GetOrderClass() {
		case brokerv1.OrderClass_ORDER_CLASS_BRACKET:
			if !caps.GetSupportsBracket() {
				return invalid("bracket orders not supported by account")
			}
			if !hasTP || !hasSL {
				return invalid("bracket needs take-profit and stop-loss prices")
			}
		case brokerv1.OrderClass_ORDER_CLASS_OCO:
			if !caps.GetSupportsOco() {
				return invalid("OCO orders not supported by account")
			}
			if !hasTP || !hasSL {
				return invalid("OCO needs take-profit and stop-loss prices")
			}
		case brokerv1.OrderClass_ORDER_CLASS_OTO:
			if !caps.GetSupportsOto() {
				return invalid("OTO orders not supported by account")
			}
			if hasTP == hasSL {
				return invalid("OTO needs exactly one protective leg")
			}
		}
		// Protective legs must sit on the correct side of the entry (no instant self-trigger).
		if hasTP && hasSL {
			buy := req.GetSide() == commonv1.Side_SIDE_BUY
			if (buy && !tp.GreaterThan(sl)) || (!buy && !tp.LessThan(sl)) {
				return invalid("take-profit and stop-loss are on the wrong sides")
			}
		}
	}
	return nil
}
