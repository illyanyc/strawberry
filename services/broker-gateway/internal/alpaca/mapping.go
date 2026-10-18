package alpaca

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
)

// MapStatus maps an Alpaca order status to the normalized state (BR-006 table in brokers.md).
// Statuses whose meaning depends on fills use filled vs qty. ok=false means unmapped (→ UNKNOWN).
func MapStatus(status string, filled, qty decimal.Decimal) (ordersv1.OrderState, bool) {
	byFill := func(none ordersv1.OrderState) ordersv1.OrderState {
		switch {
		case qty.IsPositive() && filled.GreaterThanOrEqual(qty):
			return ordersv1.OrderState_ORDER_STATE_FILLED
		case filled.IsPositive():
			return ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED
		default:
			return none
		}
	}
	switch status {
	case "pending_new":
		return ordersv1.OrderState_ORDER_STATE_SUBMITTED, true
	case "accepted", "new", "accepted_for_bidding", "held":
		return ordersv1.OrderState_ORDER_STATE_ACCEPTED, true
	case "partially_filled":
		return ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED, true
	case "filled":
		return ordersv1.OrderState_ORDER_STATE_FILLED, true
	case "done_for_day", "stopped":
		if qty.IsPositive() && filled.GreaterThanOrEqual(qty) {
			return ordersv1.OrderState_ORDER_STATE_FILLED, true
		}
		return ordersv1.OrderState_ORDER_STATE_ACCEPTED, true
	case "calculated":
		return byFill(ordersv1.OrderState_ORDER_STATE_ACCEPTED), true
	case "pending_cancel":
		return ordersv1.OrderState_ORDER_STATE_PENDING_CANCEL, true
	case "pending_replace":
		return ordersv1.OrderState_ORDER_STATE_PENDING_REPLACE, true
	case "canceled":
		return ordersv1.OrderState_ORDER_STATE_CANCELED, true
	case "expired":
		return ordersv1.OrderState_ORDER_STATE_EXPIRED, true
	case "replaced":
		return ordersv1.OrderState_ORDER_STATE_REPLACED, true
	case "rejected":
		return ordersv1.OrderState_ORDER_STATE_REJECTED, true
	case "suspended":
		// OE-005: fail closed.
		return ordersv1.OrderState_ORDER_STATE_UNKNOWN, true
	default:
		return ordersv1.OrderState_ORDER_STATE_UNKNOWN, false
	}
}

// MapReject maps an Alpaca HTTP error to a RejectReason (BR-005). It returns nil for responses that
// do not prove a rejection (5xx), which callers treat as ambiguous.
func MapReject(httpStatus int, code int64, message string) *brokerv1.RejectReason {
	m := strings.ToLower(message)
	bc := strconv.Itoa(httpStatus)
	if code != 0 {
		bc += "/" + strconv.FormatInt(code, 10)
	}
	r := func(c brokerv1.RejectCode) *brokerv1.RejectReason {
		return &brokerv1.RejectReason{Code: c, Message: message, BrokerCode: bc}
	}
	switch {
	case httpStatus >= 500:
		return nil
	case httpStatus == http.StatusTooManyRequests:
		return r(brokerv1.RejectCode_REJECT_CODE_RATE_LIMITED)
	case strings.Contains(m, "wash trade") || strings.Contains(m, "self-cross") || strings.Contains(m, "opposite side"):
		return r(brokerv1.RejectCode_REJECT_CODE_SELF_CROSS)
	case strings.Contains(m, "buying power"):
		return r(brokerv1.RejectCode_REJECT_CODE_INSUFFICIENT_BUYING_POWER)
	case strings.Contains(m, "client_order_id must be unique") || strings.Contains(m, "duplicate client"):
		return r(brokerv1.RejectCode_REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID)
	case strings.Contains(m, "not shortable") || strings.Contains(m, "cannot be sold short") || strings.Contains(m, "short"):
		return r(brokerv1.RejectCode_REJECT_CODE_NOT_SHORTABLE)
	case strings.Contains(m, "margin"):
		return r(brokerv1.RejectCode_REJECT_CODE_BROKER_RISK)
	case strings.Contains(m, "not tradable") || strings.Contains(m, "is not active") || strings.Contains(m, "halted"):
		return r(brokerv1.RejectCode_REJECT_CODE_NOT_TRADABLE)
	case strings.Contains(m, "asset") && strings.Contains(m, "not found"):
		return r(brokerv1.RejectCode_REJECT_CODE_UNKNOWN_INSTRUMENT)
	case strings.Contains(m, "market is closed") || strings.Contains(m, "market hours"):
		return r(brokerv1.RejectCode_REJECT_CODE_MARKET_CLOSED)
	case httpStatus == http.StatusUnprocessableEntity:
		return r(brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER)
	default:
		return r(brokerv1.RejectCode_REJECT_CODE_BROKER_OTHER)
	}
}

var orderTypes = map[ordersv1.OrderType]string{
	ordersv1.OrderType_ORDER_TYPE_MARKET:        "market",
	ordersv1.OrderType_ORDER_TYPE_LIMIT:         "limit",
	ordersv1.OrderType_ORDER_TYPE_STOP:          "stop",
	ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT:    "stop_limit",
	ordersv1.OrderType_ORDER_TYPE_TRAILING_STOP: "trailing_stop",
}

var tifs = map[ordersv1.TimeInForce]string{
	ordersv1.TimeInForce_TIME_IN_FORCE_DAY: "day",
	ordersv1.TimeInForce_TIME_IN_FORCE_GTC: "gtc",
	ordersv1.TimeInForce_TIME_IN_FORCE_IOC: "ioc",
	ordersv1.TimeInForce_TIME_IN_FORCE_FOK: "fok",
	ordersv1.TimeInForce_TIME_IN_FORCE_OPG: "opg",
	ordersv1.TimeInForce_TIME_IN_FORCE_CLS: "cls",
}

var orderClasses = map[brokerv1.OrderClass]string{
	brokerv1.OrderClass_ORDER_CLASS_SIMPLE:  "simple",
	brokerv1.OrderClass_ORDER_CLASS_BRACKET: "bracket",
	brokerv1.OrderClass_ORDER_CLASS_OCO:     "oco",
	brokerv1.OrderClass_ORDER_CLASS_OTO:     "oto",
}

func reverse[K comparable, V comparable](m map[K]V) map[V]K {
	out := make(map[V]K, len(m))
	for k, v := range m {
		out[v] = k
	}
	return out
}

var (
	orderTypesRev   = reverse(orderTypes)
	tifsRev         = reverse(tifs)
	orderClassesRev = reverse(orderClasses)
)

// Capabilities of an Alpaca account (brokers.md "Alpaca specifics"); data feeds from config.
func Capabilities(feeds []string) *brokerv1.Capabilities {
	return &brokerv1.Capabilities{
		OrderTypes: []ordersv1.OrderType{ordersv1.OrderType_ORDER_TYPE_MARKET, ordersv1.OrderType_ORDER_TYPE_LIMIT,
			ordersv1.OrderType_ORDER_TYPE_STOP, ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT, ordersv1.OrderType_ORDER_TYPE_TRAILING_STOP},
		Tifs: []ordersv1.TimeInForce{ordersv1.TimeInForce_TIME_IN_FORCE_DAY, ordersv1.TimeInForce_TIME_IN_FORCE_GTC,
			ordersv1.TimeInForce_TIME_IN_FORCE_IOC, ordersv1.TimeInForce_TIME_IN_FORCE_FOK, ordersv1.TimeInForce_TIME_IN_FORCE_OPG,
			ordersv1.TimeInForce_TIME_IN_FORCE_CLS},
		SupportsBracket: true, SupportsOco: true, SupportsOto: true, SupportsFractional: true, SupportsShort: true,
		SupportsExtendedHours: true, MaxClientOrderIdLen: 128, PdtApplies: false, IsMargin: true, DataFeeds: feeds,
		SupportsReplace: true, SupportsTrailingStop: true,
	}
}

var _ = adapter.D
