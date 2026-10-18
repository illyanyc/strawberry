package app

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"google.golang.org/grpc"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// BrokerEndpoint is one adapter serving tb.broker.v1.BrokerGatewayService.
type BrokerEndpoint struct {
	// Audience is the adapter's service name for service tokens ("broker-gateway",
	// "broker-gateway-ibkr").
	Audience string
	Client   brokerv1.BrokerGatewayServiceClient
	// Opts override the call options for this endpoint (tests with their own signer).
	Opts []grpc.CallOption
}

// Brokers routes each account to the adapter that lists it (D30: Alpaca and the simulator through
// broker-gateway, IBKR through broker-gateway-ibkr).
type Brokers struct {
	Endpoints []BrokerEndpoint
	Signer    *svcauth.Signer
	Timeout   time.Duration

	mu    sync.Mutex
	route map[string]int
}

func (b *Brokers) opts(i int) []grpc.CallOption {
	if b.Endpoints[i].Opts != nil {
		return b.Endpoints[i].Opts
	}
	if b.Signer == nil {
		return nil
	}
	return []grpc.CallOption{b.Signer.PerRPC(b.Endpoints[i].Audience)}
}

func (b *Brokers) ctx(ctx context.Context) (context.Context, context.CancelFunc) {
	t := b.Timeout
	if t == 0 {
		t = 10 * time.Second
	}
	return context.WithTimeout(ctx, t)
}

// Accounts implements oms.Brokers: every reachable adapter's accounts.
func (b *Brokers) Accounts(ctx context.Context) ([]*brokerv1.BrokerAccount, error) {
	var out []*brokerv1.BrokerAccount
	var errs []error
	route := map[string]int{}
	for i, ep := range b.Endpoints {
		c, cancel := b.ctx(ctx)
		r, err := ep.Client.ListAccounts(c, &brokerv1.ListAccountsRequest{}, b.opts(i)...)
		cancel()
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", ep.Audience, err))
			continue
		}
		for _, a := range r.GetAccounts() {
			route[a.GetBrokerAccountId()] = i
			out = append(out, a)
		}
	}
	b.mu.Lock()
	if b.route == nil {
		b.route = map[string]int{}
	}
	for k, v := range route {
		b.route[k] = v
	}
	b.mu.Unlock()
	if len(out) == 0 && len(errs) > 0 {
		return nil, errors.Join(errs...)
	}
	return out, nil
}

var errUnrouted = errors.New("execution: no broker adapter serves this account")

func (b *Brokers) pick(acct string) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	i, ok := b.route[acct]
	if !ok {
		return 0, errUnrouted
	}
	return i, nil
}

// Session implements oms.Brokers.
func (b *Brokers) Session(ctx context.Context, acct string) (*brokerv1.SessionStatus, error) {
	i, err := b.pick(acct)
	if err != nil {
		return nil, err
	}
	c, cancel := b.ctx(ctx)
	defer cancel()
	r, err := b.Endpoints[i].Client.GetSessionStatus(c, &brokerv1.GetSessionStatusRequest{BrokerAccountId: acct}, b.opts(i)...)
	return r.GetStatus(), err
}

// Submit implements oms.Brokers. No transport retries: an ambiguous outcome is handled by the OMS
// (lookup by client_order_id, OE-003).
func (b *Brokers) Submit(ctx context.Context, req *brokerv1.BrokerOrderRequest) (*brokerv1.SubmitOrderResponse, error) {
	i, err := b.pick(req.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	c, cancel := b.ctx(ctx)
	defer cancel()
	return b.Endpoints[i].Client.SubmitOrder(c, &brokerv1.SubmitOrderRequest{Order: req}, b.opts(i)...)
}

// Cancel implements oms.Brokers.
func (b *Brokers) Cancel(ctx context.Context, acct, coid, brokerID string) (*brokerv1.CancelOrderResponse, error) {
	i, err := b.pick(acct)
	if err != nil {
		return nil, err
	}
	c, cancel := b.ctx(ctx)
	defer cancel()
	return b.Endpoints[i].Client.CancelOrder(c, &brokerv1.CancelOrderRequest{BrokerAccountId: acct, ClientOrderId: coid, BrokerOrderId: brokerID}, b.opts(i)...)
}

// GetOrder implements oms.Brokers.
func (b *Brokers) GetOrder(ctx context.Context, acct, coid, brokerID string) (*brokerv1.GetOrderResponse, error) {
	i, err := b.pick(acct)
	if err != nil {
		return nil, err
	}
	if brokerID != "" {
		coid = ""
	}
	c, cancel := b.ctx(ctx)
	defer cancel()
	return b.Endpoints[i].Client.GetOrder(c, &brokerv1.GetOrderRequest{BrokerAccountId: acct, ClientOrderId: coid, BrokerOrderId: brokerID}, b.opts(i)...)
}

// ListOpenOrders implements oms.Brokers.
func (b *Brokers) ListOpenOrders(ctx context.Context, acct string) ([]*brokerv1.BrokerOrder, error) {
	i, err := b.pick(acct)
	if err != nil {
		return nil, err
	}
	c, cancel := b.ctx(ctx)
	defer cancel()
	r, err := b.Endpoints[i].Client.ListOpenOrders(c, &brokerv1.ListOpenOrdersRequest{BrokerAccountId: acct}, b.opts(i)...)
	return r.GetOrders(), err
}

// InstrumentInfo implements oms.Brokers.
func (b *Brokers) InstrumentInfo(ctx context.Context, acct string, inst *commonv1.Instrument) (*brokerv1.InstrumentInfo, error) {
	i, err := b.pick(acct)
	if err != nil {
		return nil, err
	}
	c, cancel := b.ctx(ctx)
	defer cancel()
	r, err := b.Endpoints[i].Client.GetInstrumentInfo(c, &brokerv1.GetInstrumentInfoRequest{BrokerAccountId: acct, Instrument: inst, Symbol: inst.GetSymbol()}, b.opts(i)...)
	return r.GetInfo(), err
}

// Ledger adapts tb.ledger.v1.LedgerService to oms.Ledger.
type Ledger struct {
	Client ledgerv1.LedgerServiceClient
	Signer *svcauth.Signer
}

func (l *Ledger) opts() []grpc.CallOption {
	if l.Signer == nil {
		return nil
	}
	return []grpc.CallOption{l.Signer.PerRPC("ledger")}
}

// Constraints implements oms.Ledger.
func (l *Ledger) Constraints(ctx context.Context, acct, instr, owner string) (*ledgerv1.OwnershipConstraints, error) {
	r, err := l.Client.GetOwnershipConstraints(ctx, &ledgerv1.GetOwnershipConstraintsRequest{BrokerAccountId: acct, InstrumentId: instr, Owner: owner}, l.opts()...)
	return r.GetConstraints(), err
}

// Reserve implements oms.Ledger.
func (l *Ledger) Reserve(ctx context.Context, r *ledgerv1.ReserveForOrderRequest) (*ledgerv1.ReserveForOrderResponse, error) {
	return l.Client.ReserveForOrder(ctx, r, l.opts()...)
}

// Release implements oms.Ledger.
func (l *Ledger) Release(ctx context.Context, intentID string) error {
	_, err := l.Client.ReleaseReservation(ctx, &ledgerv1.ReleaseReservationRequest{IntentId: intentID}, l.opts()...)
	return err
}

// Portfolio implements oms.Ledger.
func (l *Ledger) Portfolio(ctx context.Context, owner string) (*ledgerv1.OwnerView, error) {
	r, err := l.Client.GetStrategyPortfolio(ctx, &ledgerv1.GetStrategyPortfolioRequest{StrategyId: owner}, l.opts()...)
	return r.GetPortfolio(), err
}

// RegisterOwner implements oms.Ledger.
func (l *Ledger) RegisterOwner(ctx context.Context, r *ledgerv1.RegisterOwnerRequest) error {
	_, err := l.Client.RegisterOwner(ctx, r, l.opts()...)
	return err
}

// SetAllocation implements oms.Ledger.
func (l *Ledger) SetAllocation(ctx context.Context, r *ledgerv1.SetAllocationRequest) error {
	_, err := l.Client.SetAllocation(ctx, r, l.opts()...)
	return err
}
