// Package simcore runs the live order path inside a backtest (section 08, ADR-0031, BT-003): the OMS
// engine with its pre-trade gate (internal/oms, internal/risk) and the ledger's accounting core
// (services/ledger/book), driven request by request by the Python event-driven engine over the
// tb.backtest.v1 simcore protocol. The backtester owns the clock, the market data, and the fill
// decisions; every intent decision, order, reservation, and booking is made by production code.
package simcore

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/shopspring/decimal"

	backtestv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/backtest/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/execution/app"
	"github.com/illyanyc/strawberry/services/execution/internal/oms"
	"github.com/illyanyc/strawberry/services/ledger/book"
)

// Core is one simulated trading stack (one or more simulated accounts).
type Core struct {
	clock    int64
	broker   *Broker
	ledger   *Ledger
	eng      *oms.Engine
	cal      *calendar.Calendar
	log      *slog.Logger
	accounts []string
	seenAt   int64
}

// New returns an empty core; the first request must be a setup.
func New(log *slog.Logger) *Core {
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	c := &Core{cal: calendar.XNYS(), log: log}
	now := func() int64 { return c.clock }
	c.broker, c.ledger = newBroker(now), newLedger(c.cal, now)
	return c
}

func (c *Core) now() time.Time { return time.Unix(0, c.clock).UTC() }

// Handle answers one request. Errors are returned in the response (the backtest stops).
func (c *Core) Handle(ctx context.Context, req *backtestv1.SimRequest) *backtestv1.SimResponse {
	resp, err := c.handle(ctx, req)
	if err != nil {
		return &backtestv1.SimResponse{Error: err.Error()}
	}
	return resp
}

func (c *Core) advanceClock(now int64) error {
	if now < c.clock {
		return fmt.Errorf("simcore: clock moved backwards (%d < %d)", now, c.clock)
	}
	c.clock = now
	return nil
}

func (c *Core) handle(ctx context.Context, req *backtestv1.SimRequest) (*backtestv1.SimResponse, error) {
	if c.eng == nil && req.GetSetup() == nil {
		return nil, errors.New("simcore: setup required first")
	}
	out := &backtestv1.SimResponse{}
	switch b := req.GetBody().(type) {
	case *backtestv1.SimRequest_Setup:
		return out, c.setup(ctx, b.Setup)
	case *backtestv1.SimRequest_Advance:
		if err := c.advanceClock(b.Advance.GetNow()); err != nil {
			return nil, err
		}
		c.bars(b.Advance.GetBars())
		c.refresh()
		c.eng.Tick(ctx)
	case *backtestv1.SimRequest_Submit:
		if err := c.advanceClock(b.Submit.GetNow()); err != nil {
			return nil, err
		}
		c.bars(b.Submit.GetBars())
		c.refresh()
		r, err := c.eng.Submit(ctx, b.Submit.GetIntent(), &ordersv1.Actor{Name: "service:strategy-runtime"})
		if err != nil {
			return nil, fmt.Errorf("simcore: submit %s: %w", b.Submit.GetIntent().GetIntentId(), err)
		}
		out.Decision = r.GetDecision()
	case *backtestv1.SimRequest_Fill:
		f := b.Fill
		if err := c.advanceClock(f.GetNow()); err != nil {
			return nil, err
		}
		x, err := c.broker.fill(f.GetBrokerAccountId(), f.GetBrokerOrderId(), pd(f.GetQty()), pd(f.GetPrice()), pd(f.GetFees()), f.GetExecId())
		if err != nil {
			return nil, err
		}
		if err := c.booked(ctx, x, out); err != nil {
			return nil, err
		}
	case *backtestv1.SimRequest_OrderEnd:
		e := b.OrderEnd
		if err := c.advanceClock(e.GetNow()); err != nil {
			return nil, err
		}
		o := c.broker.find(e.GetBrokerAccountId(), "", e.GetBrokerOrderId())
		if o == nil {
			return nil, fmt.Errorf("simcore: order end for unknown order %q", e.GetBrokerOrderId())
		}
		if !oms.IsFinal(e.GetState()) {
			return nil, fmt.Errorf("simcore: order end needs a terminal state, got %s", e.GetState())
		}
		c.broker.end(o, e.GetState())
	case *backtestv1.SimRequest_ManualFill:
		m := b.ManualFill
		if err := c.advanceClock(m.GetNow()); err != nil {
			return nil, err
		}
		x := &ordersv1.ExecutionReport{BrokerAccountId: m.GetBrokerAccountId(), BrokerExecId: m.GetExecId(), BrokerOrderId: "manual:" + m.GetExecId(),
			InstrumentId: m.GetInstrumentId(), Side: m.GetSide(), Qty: m.GetQty(), Price: m.GetPrice(), Fees: m.GetFees(), EventTime: c.clock,
			Mode: commonv1.Mode_MODE_BACKTEST, Broker: commonv1.Broker_BROKER_SIMULATOR}
		if err := c.booked(ctx, x, out); err != nil {
			return nil, err
		}
	case *backtestv1.SimRequest_CorporateAction:
		ca := b.CorporateAction
		if err := c.advanceClock(ca.GetNow()); err != nil {
			return nil, err
		}
		for _, acct := range c.accounts {
			ev := book.Event{Account: acct, Source: ca.GetSourceId(), Time: c.clock}
			switch ca.GetKind() {
			case "split":
				ev.Kind, ev.Split = book.KindSplit, &book.Split{InstrumentID: ca.GetInstrumentId(), Ratio: pd(ca.GetValue())}
			case "dividend":
				ev.Kind, ev.Dividend = book.KindDividend, &book.Dividend{InstrumentID: ca.GetInstrumentId(), Amount: pd(ca.GetValue())}
			default:
				return nil, fmt.Errorf("simcore: unknown corporate action %q", ca.GetKind())
			}
			upd, err := c.ledger.apply(ev)
			if err != nil {
				return nil, err
			}
			if upd != nil {
				out.LedgerUpdates = append(out.LedgerUpdates, upd)
			}
		}
	case *backtestv1.SimRequest_Query:
		q := b.Query
		acct := q.GetBrokerAccountId()
		if acct == "" {
			acct = c.ledger.accountFor(q.GetOwner())
		}
		if c.ledger.b.Accounts[acct] == nil {
			return nil, fmt.Errorf("simcore: unknown account for owner %q", q.GetOwner())
		}
		out.Portfolio = c.ledger.ownerView(acct, q.GetOwner())
		return out, nil
	default:
		return nil, errors.New("simcore: empty request")
	}
	if err := c.settle(ctx, out); err != nil {
		return nil, err
	}
	c.refresh()
	out.Intents = c.newIntents()
	return out, nil
}

// booked books an execution in the ledger core and the OMS (exec.* consumers in production).
func (c *Core) booked(ctx context.Context, x *ordersv1.ExecutionReport, out *backtestv1.SimResponse) error {
	if inst := c.broker.instr[x.GetInstrumentId()]; inst != nil && x.GetSymbol() == "" {
		x.Symbol = inst.GetInstrument().GetSymbol()
	}
	upd, err := c.ledger.execution(x)
	if err != nil {
		return err
	}
	if upd != nil {
		out.LedgerUpdates = append(out.LedgerUpdates, upd)
	}
	c.ledger.mark(x.GetInstrumentId(), pd(x.GetPrice()))
	return c.eng.OnExecution(ctx, x)
}

// settle feeds every broker-side order change to the OMS (ord.update.* in production) until the
// OMS stops reacting (protective stops placed after an entry fill, exits after cancels).
func (c *Core) settle(ctx context.Context, out *backtestv1.SimResponse) error {
	for range 100 {
		changed := c.broker.drain()
		if len(changed) == 0 {
			return nil
		}
		for _, o := range changed {
			ev := map[ordersv1.OrderState]string{ordersv1.OrderState_ORDER_STATE_FILLED: "fill", ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED: "partial_fill",
				ordersv1.OrderState_ORDER_STATE_CANCELED: "canceled", ordersv1.OrderState_ORDER_STATE_EXPIRED: "expired", ordersv1.OrderState_ORDER_STATE_REJECTED: "rejected"}[o.GetState()]
			if ev == "" {
				ev = "new"
			}
			if err := c.eng.OnOrderUpdate(ctx, &brokerv1.BrokerOrderUpdate{BrokerAccountId: o.GetBrokerAccountId(), Event: ev, Order: o, EventTime: c.clock, ReceivedAt: c.clock}); err != nil {
				return err
			}
			out.Orders = append(out.Orders, o)
		}
	}
	return errors.New("simcore: order updates did not settle")
}

func (c *Core) bars(bs []*backtestv1.SimBar) {
	for _, b := range bs {
		px := pd(b.GetClose())
		c.eng.OnBar(b.GetInstrumentId(), px, b.GetTsClose(), b.GetIntervalNs())
		c.ledger.mark(b.GetInstrumentId(), px)
	}
}

// refresh publishes what the broker adapters and the ledger would: a fresh account snapshot, a
// clean (or broken) reconciliation, and the strategy-runtime heartbeat.
func (c *Core) refresh() {
	for _, acct := range c.accounts {
		_ = c.eng.OnSnapshot(context.Background(), c.ledger.snapshot(acct))
		r := &ledgerv1.ReconciliationResult{BrokerAccountId: acct, AsOf: c.clock, SnapshotAsOf: c.clock, Status: ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK,
			Trigger: "simulation"}
		for _, bk := range c.ledger.brk.List(acct, true) {
			r.Status = ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_BREAK
			if bk.InstrumentWide {
				r.Halted = append(r.Halted, "*|"+bk.InstrumentID)
			}
			for _, o := range bk.Affected {
				r.Halted = append(r.Halted, o+"|"+bk.InstrumentID)
			}
		}
		c.eng.OnRecon(r)
	}
	c.eng.OnRuntimeHeartbeat()
	c.ledger.observe()
}

func (c *Core) newIntents() []*ordersv1.IntentRecord {
	var out []*ordersv1.IntentRecord
	for _, r := range c.eng.Intents("", false, c.seenAt, 0) {
		if r.GetReceivedAt() > c.seenAt || (c.seenAt == 0 && r.GetReceivedAt() == 0) {
			out = append(out, r)
		}
	}
	c.seenAt = c.clock
	return out
}

func (c *Core) setup(ctx context.Context, s *backtestv1.SimSetup) error {
	if c.eng != nil {
		return errors.New("simcore: already set up")
	}
	c.clock = s.GetStart()
	cfg := oms.DefaultConfig()
	cfg.Routing = map[commonv1.Horizon][]string{}
	for _, r := range s.GetRouting() {
		cfg.Routing[r.GetHorizon()] = append(cfg.Routing[r.GetHorizon()], r.GetBrokerAccountIds()...)
	}
	cfg.Policies = map[string]oms.AccountPolicy{}
	for _, a := range s.GetAccounts() {
		acct := a.GetBrokerAccountId()
		c.accounts = append(c.accounts, acct)
		cfg.Policies[acct] = oms.AccountPolicy{MarginMultiple: orOne(pd(a.GetMarginMultiple())), MarginUtilCap: orOne(pd(a.GetMarginUtilCap())),
			PDTGuard: orStr(a.GetPdtGuard(), "off"), PDTMax: int(orInt(a.GetPdtMax(), 3)), Segregation: a.GetSegregation()}
		c.broker.accounts[acct] = &brokerv1.BrokerAccount{BrokerAccountId: acct, Broker: a.GetBroker(), Environment: brokerv1.Environment_ENVIRONMENT_PAPER,
			Capabilities: &brokerv1.Capabilities{SupportsBracket: a.GetSupportsBracket(), SupportsOto: a.GetSupportsBracket(), SupportsFractional: a.GetSupportsFractional(),
				SupportsShort: true, MaxClientOrderIdLen: 128, IsMargin: true,
				OrderTypes: []ordersv1.OrderType{ordersv1.OrderType_ORDER_TYPE_MARKET, ordersv1.OrderType_ORDER_TYPE_LIMIT, ordersv1.OrderType_ORDER_TYPE_STOP},
				Tifs:       []ordersv1.TimeInForce{ordersv1.TimeInForce_TIME_IN_FORCE_DAY, ordersv1.TimeInForce_TIME_IN_FORCE_GTC}}}
		op := &book.Opening{Cash: pd(a.GetCash())}
		for _, p := range a.GetOpeningPositions() {
			op.Positions = append(op.Positions, book.OpeningPos{InstrumentID: p.GetInstrumentId(), Symbol: p.GetSymbol(), Qty: pd(p.GetQty()), AvgCost: pd(p.GetAvgCost())})
		}
		if _, err := c.ledger.apply(book.Event{Kind: book.KindOpening, Account: acct, Source: "opening:" + acct, Time: c.clock, Opening: op}); err != nil {
			return err
		}
		for _, p := range a.GetOpeningPositions() {
			c.ledger.mark(p.GetInstrumentId(), pd(p.GetAvgCost()))
		}
		c.ledger.snapshot(acct)
	}
	instr := oms.StaticInstruments{}
	for _, in := range s.GetInstruments() {
		inst := &commonv1.Instrument{InstrumentId: in.GetInstrumentId(), Symbol: in.GetSymbol(), AssetClass: commonv1.AssetClass_ASSET_CLASS_US_EQUITY, Currency: "USD"}
		instr[in.GetInstrumentId()] = inst
		c.broker.instr[in.GetInstrumentId()] = &brokerv1.InstrumentInfo{Instrument: inst, Tradable: in.GetTradable(), Shortable: in.GetShortable(),
			EasyToBorrow: in.GetShortable(), Fractionable: false, Marginable: true, MinTick: dec(decimal.RequireFromString("0.01")), LotSize: dec(decimal.NewFromInt(1)), Status: "active"}
	}
	if s.GetGlobalLimits() != nil {
		cfg.GlobalLimits = s.GetGlobalLimits()
	}
	if v := s.GetEntryCollarBps(); v > 0 {
		cfg.EntryCollarBps = v
	}
	if v := s.GetExitCollarBps(); v > 0 {
		cfg.ExitCollarBps = v
	}
	if v := s.GetFlattenBeforeCloseNs(); v > 0 {
		cfg.FlattenBeforeClose = time.Duration(v)
	}
	cfg.Now, cfg.Wall = c.now, time.Now
	cfg.Calendar = c.cal
	cfg.CancelWait = time.Second
	// The runtime heartbeat is sent with every request; wall-clock dead-man ages are meaningless here.
	cfg.DeadmanTimeout = 24 * time.Hour
	cfg.SessionAlertAfter = 24 * time.Hour
	cfg.Log = c.log
	eng, err := oms.New(ctx, cfg, oms.NewMemory(), c.broker, c.ledger, instr)
	if err != nil {
		return err
	}
	c.eng = eng
	var reg app.Registry
	if err := json.Unmarshal(s.GetRegistryJson(), &reg); err != nil {
		return fmt.Errorf("simcore: registry: %w", err)
	}
	if err := app.Seed(ctx, eng, &reg); err != nil {
		return fmt.Errorf("simcore: seed registry: %w", err)
	}
	for _, x := range reg.Strategies {
		want, _ := decimal.NewFromString(orStr(x.Allocation, "0"))
		if v := c.ledger.ownerView(x.Account, x.StrategyID); !pd(v.GetAllocation()).Equal(want) {
			return fmt.Errorf("simcore: allocation %s for %s was not accepted by the ledger (VP-009)", want, x.StrategyID)
		}
	}
	eng.Tick(ctx)
	c.refresh()
	eng.Tick(ctx)
	if eng.Recovering() {
		return errors.New("simcore: OMS did not finish startup recovery")
	}
	c.broker.drain()
	c.seenAt = c.clock
	return nil
}

func orOne(d decimal.Decimal) decimal.Decimal {
	if d.IsZero() {
		return decimal.NewFromInt(1)
	}
	return d
}

func orStr(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

func orInt(v, def int32) int32 {
	if v == 0 {
		return def
	}
	return v
}
