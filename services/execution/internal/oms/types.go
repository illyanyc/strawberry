// Package oms is the order management system (section 07, ADR-0025): intent intake, the pre-trade
// gate, order construction against broker capabilities, the order state machine with idempotent
// submission, broker-side protection, cancel/replace, kill switches, halts, dead-man checks, and
// the trading-day lifecycle. Rules OE-*, RM-*, SM-* in docs/business-logic/.
package oms

import (
	"context"
	"log/slog"
	"time"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
)

// Brokers routes order RPCs to the adapter that serves each account (broker-gateway for Alpaca
// and the simulator, broker-gateway-ibkr for IBKR), all speaking tb.broker.v1.BrokerGatewayService.
type Brokers interface {
	Accounts(ctx context.Context) ([]*brokerv1.BrokerAccount, error)
	Session(ctx context.Context, acct string) (*brokerv1.SessionStatus, error)
	Submit(ctx context.Context, req *brokerv1.BrokerOrderRequest) (*brokerv1.SubmitOrderResponse, error)
	Cancel(ctx context.Context, acct, clientOrderID, brokerOrderID string) (*brokerv1.CancelOrderResponse, error)
	GetOrder(ctx context.Context, acct, clientOrderID, brokerOrderID string) (*brokerv1.GetOrderResponse, error)
	ListOpenOrders(ctx context.Context, acct string) ([]*brokerv1.BrokerOrder, error)
	InstrumentInfo(ctx context.Context, acct string, inst *commonv1.Instrument) (*brokerv1.InstrumentInfo, error)
}

// Ledger is the subset of tb.ledger.v1.LedgerService on the order path.
type Ledger interface {
	Constraints(ctx context.Context, acct, instr, owner string) (*ledgerv1.OwnershipConstraints, error)
	Reserve(ctx context.Context, r *ledgerv1.ReserveForOrderRequest) (*ledgerv1.ReserveForOrderResponse, error)
	Release(ctx context.Context, intentID string) error
	Portfolio(ctx context.Context, owner string) (*ledgerv1.OwnerView, error)
	RegisterOwner(ctx context.Context, r *ledgerv1.RegisterOwnerRequest) error
	SetAllocation(ctx context.Context, r *ledgerv1.SetAllocationRequest) error
}

// Instruments resolves instrument_id to the identifiers adapters need (instrument master).
type Instruments interface {
	Lookup(ctx context.Context, instrumentID string) (*commonv1.Instrument, error)
}

// StaticInstruments is an in-memory instrument master (tests, harness).
type StaticInstruments map[string]*commonv1.Instrument

// Lookup implements Instruments.
func (s StaticInstruments) Lookup(_ context.Context, id string) (*commonv1.Instrument, error) {
	if i, ok := s[id]; ok {
		return i, nil
	}
	return nil, errUnknownInstrument
}

// AccountPolicy is the per-account D30 configuration (RM-008, RM-012, RM-014).
type AccountPolicy struct {
	MarginMultiple decimal.Decimal
	MarginUtilCap  decimal.Decimal
	PDTGuard       string
	PDTMax         int
	Segregation    bool
}

// Config tunes the OMS. Defaults are documented in docs/business-logic/risk-management.md.
type Config struct {
	// Routing maps horizon → accounts allowed (RM-011).
	Routing map[commonv1.Horizon][]string
	// Policies per account; DefaultPolicy for accounts not listed.
	Policies      map[string]AccountPolicy
	DefaultPolicy AccountPolicy
	// GlobalLimits and ManualLimits seed the limit scopes "global" and "manual".
	GlobalLimits *ordersv1.RiskLimits
	ManualLimits *ordersv1.RiskLimits
	// EntryCollarBps: marketable-limit offset from the last price (OE-013).
	EntryCollarBps int64
	ExitCollarBps  int64
	// FlattenBeforeClose: intraday strategies exit this long before the close (OE-016).
	FlattenBeforeClose time.Duration
	// CancelWait bounds waiting for own resting orders to cancel before an exit (OE-018).
	CancelWait time.Duration
	// UnknownGiveUp: an unknown submit not found at the broker after this is rejected (OE-005).
	UnknownGiveUp time.Duration
	// DeadmanTimeout for the strategy runtime heartbeat (RM-017).
	DeadmanTimeout time.Duration
	// SessionAlertAfter: page when an account with strategies is not READY during the session.
	SessionAlertAfter time.Duration
	// RejectStorm: this many broker rejects for a strategy within a minute freeze it (RM-016).
	RejectStorm int
	// FinalityDelay is added to two bar intervals for the freshness limit (MD-007).
	FinalityDelay time.Duration
	// LiveFlag is TB_TRADING_LIVE_ENABLED (GL-011). Default false: config alone never enables live.
	LiveFlag bool
	Now      func() time.Time
	// Wall is the clock for heartbeats (dead-man ages are wall time even when Now is simulated).
	Wall     func() time.Time
	Calendar *calendar.Calendar
	ClockOK  func() bool
	Log      *slog.Logger
}

// DefaultConfig returns the documented defaults (D30 routing for the default account names).
func DefaultConfig() Config {
	d := decimal.RequireFromString
	return Config{
		Routing: map[commonv1.Horizon][]string{
			commonv1.Horizon_HORIZON_INTRADAY: {"alpaca-paper", "sim-paper"},
			commonv1.Horizon_HORIZON_SWING:    {"ibkr-paper-bot"},
		},
		Policies:      map[string]AccountPolicy{},
		DefaultPolicy: AccountPolicy{MarginMultiple: d("1"), MarginUtilCap: d("1"), PDTGuard: "off", PDTMax: 3, Segregation: true},
		GlobalLimits: &ordersv1.RiskLimits{MaxOrderNotional: dec("25000"), MaxOrderQty: dec("5000"), MaxPositionNotional: dec("25000"),
			MaxPositionPctAllocation: dec("0.5"), MaxGrossExposure: dec("50000"), MaxNetExposure: dec("50000"), MaxPositions: 10,
			MaxConcentrationPct: dec("1"), MaxDailyLoss: dec("1000"), MaxDrawdown: dec("2500"), MaxOrdersPerMinute: 30, MaxCancelsPerMinute: 60,
			MaxConsecutiveLosses: 5, PriceCollarPct: dec("0.02"), DuplicateWindowSeconds: 5, FatFingerMultiple: dec("10"), MaxOpenOrders: 20},
		ManualLimits:       &ordersv1.RiskLimits{MaxOrderNotional: dec("10000"), PriceCollarPct: dec("0.03"), MaxOrdersPerMinute: 10, DuplicateWindowSeconds: 5},
		EntryCollarBps:     10,
		ExitCollarBps:      50,
		FlattenBeforeClose: 10 * time.Minute,
		CancelWait:         5 * time.Second,
		UnknownGiveUp:      30 * time.Second,
		DeadmanTimeout:     30 * time.Second,
		SessionAlertAfter:  10 * time.Second,
		RejectStorm:        5,
		FinalityDelay:      60 * time.Second,
		Now:                time.Now,
		Wall:               time.Now,
		Calendar:           calendar.XNYS(),
		ClockOK:            func() bool { return true },
	}
}

// Policy returns the account policy.
func (c Config) Policy(acct string) AccountPolicy {
	if p, ok := c.Policies[acct]; ok {
		return p
	}
	return c.DefaultPolicy
}

func dec(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: s} }

func d(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }

func pd(x *commonv1.Decimal) decimal.Decimal {
	if x == nil || x.GetValue() == "" {
		return decimal.Zero
	}
	v, err := decimal.NewFromString(x.GetValue())
	if err != nil {
		return decimal.Zero
	}
	return v
}

// Intent states.
const (
	IntentReceived  = "received"
	IntentRejected  = "rejected"
	IntentShadow    = "shadow"
	IntentApproved  = "approved"
	IntentSubmitted = "submitted"
	IntentDone      = "done"
	IntentFailed    = "failed"
)
