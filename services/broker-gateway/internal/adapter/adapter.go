// Package adapter defines the broker-specific Broker interface and the Account engine that wraps
// any Broker with the platform's rules: session gating (BR-010), attribution and capability
// validation (BR-002, BR-003), idempotent submit (BR-007), deduplicated durable events (BR-008,
// BR-011), audit (BR-014), and margin-event handling (BR-021).
package adapter

import (
	"context"
	"errors"
	"fmt"
	"time"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
)

// Info describes one account served by a Broker.
type Info struct {
	AccountID    string
	Broker       commonv1.Broker
	Environment  brokerv1.Environment
	Capabilities *brokerv1.Capabilities
}

// Mode is the order mode implied by the environment.
func (i Info) Mode() commonv1.Mode {
	if i.Environment == brokerv1.Environment_ENVIRONMENT_LIVE {
		return commonv1.Mode_MODE_LIVE
	}
	return commonv1.Mode_MODE_PAPER
}

// OrderRef identifies an order by client or broker ID.
type OrderRef struct {
	ClientOrderID string
	BrokerOrderID string
}

// Broker is the broker-specific side of an adapter (Alpaca REST/WS client, simulator).
type Broker interface {
	Info() Info
	// Connect authenticates (e.g. GET /v2/account). Errors wrapping ErrNoCredential or ErrAuth are
	// not retried quickly (BR-012).
	Connect(ctx context.Context) error
	// Stream subscribes to order/trade updates, calls onSubscribed once listening, then emits updates
	// until ctx ends or the connection fails.
	Stream(ctx context.Context, onSubscribed func(), emit func(*brokerv1.BrokerOrderUpdate)) error
	Snapshot(ctx context.Context) (*ledgerv1.AccountSnapshot, error)
	// Submit returns *RejectError for broker rejections, an error wrapping ErrNotSent when the request
	// provably never left the process, and any other error when the outcome is ambiguous.
	Submit(ctx context.Context, req *brokerv1.BrokerOrderRequest) (*brokerv1.BrokerOrder, error)
	Cancel(ctx context.Context, ref OrderRef) error
	Replace(ctx context.Context, brokerOrderID string, req *brokerv1.ReplaceOrderRequest) (*brokerv1.BrokerOrder, error)
	// GetOrder returns ErrNotFound when the broker has no such order.
	GetOrder(ctx context.Context, ref OrderRef) (*brokerv1.BrokerOrder, error)
	ListOpenOrders(ctx context.Context) ([]*brokerv1.BrokerOrder, error)
	ListOrders(ctx context.Context, since time.Time) ([]*brokerv1.BrokerOrder, error)
	ListExecutions(ctx context.Context, since time.Time) ([]*ordersv1.ExecutionReport, error)
	InstrumentInfo(ctx context.Context, inst *commonv1.Instrument, symbol string) (*brokerv1.InstrumentInfo, error)
}

// Errors.
var (
	ErrNotFound     = errors.New("broker: order not found")
	ErrNoCredential = errors.New("broker: no credential stored for account")
	ErrAuth         = errors.New("broker: authentication failed")
	ErrNotSent      = errors.New("broker: request not sent")
)

// RejectError is a broker or local rejection with a normalized reason (BR-005).
type RejectError struct {
	Reason *brokerv1.RejectReason
}

func (e *RejectError) Error() string {
	return fmt.Sprintf("broker rejected: %s %s", e.Reason.GetCode(), e.Reason.GetMessage())
}

// Reject builds a *RejectError.
func Reject(code brokerv1.RejectCode, local bool, brokerCode, msg string) *RejectError {
	return &RejectError{Reason: &brokerv1.RejectReason{Code: code, Message: msg, BrokerCode: brokerCode, Local: local}}
}

// UnavailableError is returned by order operations while the session is not ready (BR-004).
type UnavailableError struct {
	State brokerv1.SessionState
}

func (e *UnavailableError) Error() string { return "broker unavailable: session " + e.State.String() }

// Store persists executions and order events and enqueues their bus events transactionally
// (BR-008, ADR-0018). Record* return true when the item is new.
type Store interface {
	RecordExecution(ctx context.Context, account string, e *ordersv1.ExecutionReport, u *brokerv1.BrokerOrderUpdate) (bool, error)
	RecordOrderUpdate(ctx context.Context, account string, u *brokerv1.BrokerOrderUpdate) (bool, error)
	RecordSnapshot(ctx context.Context, account string, s *ledgerv1.AccountSnapshot) error
	RequestReconciliation(ctx context.Context, r *ledgerv1.ReconciliationRequest) error
	Alert(ctx context.Context, a *alertsv1.Alert) error
	// Cursor returns the latest stored execution for the account (resync start point, OE-008).
	Cursor(ctx context.Context, account string) (execID string, execTime int64, err error)
}

// Auditor appends audit events (BR-014).
type Auditor interface {
	Append(ctx context.Context, e audit.Event) error
}

// AuditFunc adapts a function to Auditor.
type AuditFunc func(ctx context.Context, e audit.Event) error

// Append implements Auditor.
func (f AuditFunc) Append(ctx context.Context, e audit.Event) error { return f(ctx, e) }

// LiveGate reports whether a live account may run (BR-013) and whether it may accept orders
// (GL-012: enabled is read-only; orders_enabled is the trading stage).
type LiveGate interface {
	Allowed(ctx context.Context, account string) (bool, string)
	OrdersAllowed(ctx context.Context, account string) (bool, string)
}

// IsFinal reports whether an order state is terminal.
func IsFinal(s ordersv1.OrderState) bool {
	switch s {
	case ordersv1.OrderState_ORDER_STATE_FILLED, ordersv1.OrderState_ORDER_STATE_CANCELED,
		ordersv1.OrderState_ORDER_STATE_REJECTED, ordersv1.OrderState_ORDER_STATE_EXPIRED,
		ordersv1.OrderState_ORDER_STATE_REPLACED:
		return true
	default:
		return false
	}
}

// stateRank orders states for monotonic merging of out-of-order updates (BR-009).
func stateRank(s ordersv1.OrderState) int {
	switch s {
	case ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT:
		return 1
	case ordersv1.OrderState_ORDER_STATE_SUBMITTED:
		return 2
	case ordersv1.OrderState_ORDER_STATE_ACCEPTED, ordersv1.OrderState_ORDER_STATE_PENDING_REPLACE:
		return 3
	case ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED, ordersv1.OrderState_ORDER_STATE_PENDING_CANCEL:
		return 4
	case ordersv1.OrderState_ORDER_STATE_UNKNOWN:
		return 0
	default:
		if IsFinal(s) {
			return 5
		}
		return 0
	}
}
