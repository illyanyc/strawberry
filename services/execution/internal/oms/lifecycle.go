package oms

import (
	"context"
	"fmt"
	"slices"
	"sort"
	"time"

	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// Tick runs the time-driven work (call about once a second): broker accounts and sessions,
// session-loss paging, open-order reconciliation and startup recovery, the intraday flatten before
// the close, the strategy-runtime dead-man check, and the execution heartbeat.
func (e *Engine) Tick(ctx context.Context) {
	wall := e.cfg.Wall()
	if wall.Sub(e.lastAccts) > 30*time.Second || len(e.accts) == 0 {
		if accts, err := e.brokers.Accounts(ctx); err == nil {
			e.mu.Lock()
			for _, a := range accts {
				e.acct(a.GetBrokerAccountId()).info = a
			}
			e.lastAccts = wall
			e.mu.Unlock()
		}
	}
	e.pollSessions(ctx)
	e.guardLive(ctx)
	e.reconcileAll(ctx)
	e.lifecycle(ctx)
	e.deadmanCheck(ctx)
	if e.Heartbeat != nil && wall.Sub(e.lastBeat) >= 5*time.Second {
		e.lastBeat = wall
		e.Heartbeat(e.Status())
	}
}

func (e *Engine) accountIDs() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	ids := make([]string, 0, len(e.accts))
	for id, a := range e.accts {
		if a.info != nil {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids
}

// pollSessions refreshes broker session state. A transition to READY records readyAt: intents stay
// blocked until a reconciliation newer than it arrives (OE-008, RM-009). An account that leaves
// READY during the session pages after SessionAlertAfter (stale stream, lost connection, IBKR
// status 1100).
func (e *Engine) pollSessions(ctx context.Context) {
	for _, id := range e.accountIDs() {
		st, err := e.brokers.Session(ctx, id)
		e.mu.Lock()
		a := e.acct(id)
		wasReady := a.session != nil && a.session.GetState() == brokerv1.SessionState_SESSION_STATE_READY
		if err != nil {
			st = &brokerv1.SessionStatus{BrokerAccountId: id, State: brokerv1.SessionState_SESSION_STATE_DISCONNECTED, DegradedReasons: []string{"adapter unreachable"}}
		}
		a.session, a.polledAt = st, e.cfg.Wall()
		ready := st.GetState() == brokerv1.SessionState_SESSION_STATE_READY
		tx := &Tx{}
		switch {
		case ready && !wasReady:
			a.readyAt, a.notReadyAt, a.pagedDown = e.now(), time.Time{}, false
			a.ordersRecon = false
			delete(e.alerted, "session-"+id)
		case !ready && wasReady:
			a.notReadyAt = e.cfg.Wall()
		case !ready && a.notReadyAt.IsZero():
			a.notReadyAt = e.cfg.Wall()
		}
		if !ready && !a.pagedDown && !a.notReadyAt.IsZero() && e.cfg.Wall().Sub(a.notReadyAt) >= e.cfg.SessionAlertAfter &&
			e.cfg.Calendar.IsOpen(e.cfg.Now()) && st.GetState() != brokerv1.SessionState_SESSION_STATE_STOPPED && e.hasStrategies(id) {
			a.pagedDown = true
			e.pageOnce("session-"+id, "broker session not ready", fmt.Sprintf("%s is %s (%v); new intents are blocked until it is ready and reconciled",
				id, st.GetState(), st.GetDegradedReasons()), "docs/runbooks/broker-outage.md", tx)
		}
		_ = e.commit(ctx, tx)
		e.mu.Unlock()
	}
}

func (e *Engine) hasStrategies(acct string) bool {
	for _, s := range e.strategies {
		if s.GetBrokerAccountId() == acct && (s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_PAPER || s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE) {
			return true
		}
	}
	return false
}

// reconcileAll reconciles open orders after every (re)connect and every 30 s, and resolves UNKNOWN
// orders every tick. Startup recovery ends when every READY account was reconciled (OE-020).
func (e *Engine) reconcileAll(ctx context.Context) {
	allDone := true
	any := false
	for _, id := range e.accountIDs() {
		e.mu.Lock()
		a := e.acct(id)
		ready := a.session.GetState() == brokerv1.SessionState_SESSION_STATE_READY
		due := !a.ordersRecon || e.cfg.Wall().Sub(a.lastOrderRecon) > 30*time.Second
		unknown := false
		for _, o := range e.orders {
			if o.GetBrokerAccountId() == id && o.GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN {
				unknown = true
			}
		}
		e.mu.Unlock()
		if !ready {
			continue
		}
		any = true
		if due || unknown {
			if err := e.ReconcileOrders(ctx, id); err != nil {
				allDone = false
			}
		}
		e.mu.Lock()
		allDone = allDone && e.acct(id).ordersRecon
		e.mu.Unlock()
	}
	e.mu.Lock()
	if e.recovering && any && allDone {
		e.recovering = false
		e.log.Info("startup recovery complete: open orders reconciled")
	}
	e.mu.Unlock()
}

// lifecycle flattens intraday strategies before the close (OE-016): cancel their open orders and
// exit their own positions, once per session.
func (e *Engine) lifecycle(ctx context.Context) {
	now := e.cfg.Now()
	s, _, ok := e.cfg.Calendar.SessionAt(now)
	if !ok || now.Before(s.Close.Add(-e.cfg.FlattenBeforeClose)) || !now.Before(s.Close) {
		return
	}
	e.mu.Lock()
	var todo []*ordersv1.Strategy
	for id, st := range e.strategies {
		if st.GetHorizon() != commonv1.Horizon_HORIZON_INTRADAY || e.flattened[id] == s.Date {
			continue
		}
		if m := st.GetMode(); m != ordersv1.StrategyMode_STRATEGY_MODE_PAPER && m != ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			continue
		}
		e.flattened[id] = s.Date
		todo = append(todo, proto.Clone(st).(*ordersv1.Strategy))
	}
	e.mu.Unlock()
	for _, st := range todo {
		pv, err := e.ledger.Portfolio(ctx, st.GetStrategyId())
		if err != nil {
			continue
		}
		for _, pos := range pv.GetPositions() {
			q := pd(pos.GetQty())
			if !q.IsPositive() {
				continue
			}
			it := &ordersv1.OrderIntent{IntentId: newID(), StrategyId: st.GetStrategyId(), BrokerAccountId: st.GetBrokerAccountId(), InstrumentId: pos.GetInstrumentId(),
				Side: commonv1.Side_SIDE_SELL, Sizing: &ordersv1.OrderIntent_Quantity{Quantity: d(q)}, OrderType: ordersv1.OrderType_ORDER_TYPE_MARKET,
				Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY, Reason: "intraday flatten before the close (OE-016)", Mode: modeOf(st), CorrelationId: newID(),
				CreatedAt: e.now(), Horizon: st.GetHorizon(), Kind: ordersv1.IntentKind_INTENT_KIND_CLOSE, Source: ordersv1.IntentSource_INTENT_SOURCE_LIFECYCLE, DecisionTime: e.now()}
			if _, err := e.Submit(ctx, it, &ordersv1.Actor{Name: "service:execution:lifecycle"}); err != nil {
				e.log.Warn("lifecycle flatten failed", "strategy", st.GetStrategyId(), "err", err)
			}
		}
	}
}

// deadmanCheck: if the strategy runtime's heartbeat is older than DeadmanTimeout during the
// regular session while runtime-hosted strategies are active, latch a cancel-open kill switch for
// each of them and page (RM-017). Re-enabling is manual (release with step-up).
func (e *Engine) deadmanCheck(ctx context.Context) {
	e.mu.Lock()
	wall := e.cfg.Wall()
	last := e.runtimeAt
	if last.IsZero() {
		last = e.started
	}
	if !e.cfg.Calendar.IsOpen(e.cfg.Now()) || wall.Sub(last) < e.cfg.DeadmanTimeout {
		if wall.Sub(last) < e.cfg.DeadmanTimeout {
			e.deadman = false
		}
		e.mu.Unlock()
		return
	}
	if e.deadman {
		e.mu.Unlock()
		return
	}
	var ids []string
	for id, s := range e.strategies {
		if s.GetRuntimeHosted() && (s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_PAPER || s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE) {
			ids = append(ids, id)
		}
	}
	e.deadman = len(ids) > 0
	e.mu.Unlock()
	sort.Strings(ids)
	for _, id := range ids {
		_, _ = e.Trigger(ctx, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, id, ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN,
			fmt.Sprintf("strategy runtime heartbeat lost for %s (RM-017)", wall.Sub(last).Round(time.Second)), &ordersv1.Actor{Name: "deadman:strategy-runtime"})
	}
}

// ---- views

// Status is the trading status for the UI status bar and the heartbeat.
func (e *Engine) Status() *ordersv1.TradingStatus {
	e.mu.Lock()
	defer e.mu.Unlock()
	st := &ordersv1.TradingStatus{GlobalState: "running", AsOf: e.now(), ClockOk: e.cfg.ClockOK()}
	if !e.runtimeAt.IsZero() {
		st.RuntimeLastHeartbeat = e.runtimeAt.UnixNano()
	}
	for _, k := range e.kills {
		if k.GetLatched() {
			st.ActiveKillSwitches = append(st.ActiveKillSwitches, proto.Clone(k).(*ordersv1.KillSwitch))
			if k.GetScope() == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL {
				st.GlobalState = "frozen"
			}
		}
	}
	for _, h := range e.halts {
		st.ActiveHalts = append(st.ActiveHalts, proto.Clone(h).(*ordersv1.Halt))
	}
	ids := make([]string, 0, len(e.accts))
	for id := range e.accts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		a := e.accts[id]
		if a.info == nil {
			continue
		}
		pol := e.cfg.Policy(id)
		as := &ordersv1.AccountTradingStatus{BrokerAccountId: id, PdtGuard: pol.PDTGuard, Environment: "paper", DayTradeCount: int32(e.dayTrades(id))}
		if a.info.GetEnvironment() == brokerv1.Environment_ENVIRONMENT_LIVE {
			as.Environment = "live"
		}
		var reasons []string
		if a.session != nil {
			as.SessionState = a.session.GetState().String()
			if a.session.GetState() != brokerv1.SessionState_SESSION_STATE_READY {
				reasons = append(reasons, "session "+a.session.GetState().String())
			}
		} else {
			reasons = append(reasons, "session unknown")
		}
		if a.recon != nil {
			as.ReconciliationStatus = a.recon.GetStatus().String()
			as.ReconciliationFresh = a.readyAt > 0 && a.recon.GetAsOf() >= a.readyAt
			if a.recon.GetStatus().String() == "RECONCILIATION_STATUS_BREAK" {
				reasons = append(reasons, "reconciliation break")
			}
			if !as.ReconciliationFresh {
				reasons = append(reasons, "awaiting reconciliation")
			}
		} else {
			reasons = append(reasons, "no reconciliation yet")
		}
		if s := a.snap; s != nil {
			as.IntradayMarginDeficit, as.MarginCall = s.GetIntradayMarginDeficit(), s.GetMarginCall()
			if eq := pd(s.GetEquity()); eq.IsPositive() {
				as.MarginUtilization = d(pd(s.GetLongMarketValue()).Add(pd(s.GetShortMarketValue()).Abs()).DivRound(eq.Mul(pol.MarginMultiple), 4))
			}
			if s.GetIntradayMarginDeficit() || s.GetMarginCall() {
				reasons = append(reasons, "margin event")
			}
		}
		for _, k := range e.kills {
			if k.GetLatched() && (k.GetScope() == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL || (k.GetScope() == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT && k.GetTarget() == id)) {
				reasons = append(reasons, "kill switch "+k.GetEventId())
			}
		}
		if e.recovering {
			reasons = append(reasons, "startup recovery")
		}
		as.BlockedReasons, as.AcceptingIntents = reasons, len(reasons) == 0
		st.Accounts = append(st.Accounts, as)
	}
	sids := make([]string, 0, len(e.strategies))
	for id := range e.strategies {
		sids = append(sids, id)
	}
	sort.Strings(sids)
	for _, id := range sids {
		s := e.strategies[id]
		ss := &ordersv1.StrategyTradingStatus{StrategyId: id, Mode: s.GetMode()}
		var reasons []string
		if m := s.GetMode(); m != ordersv1.StrategyMode_STRATEGY_MODE_PAPER && m != ordersv1.StrategyMode_STRATEGY_MODE_LIVE && m != ordersv1.StrategyMode_STRATEGY_MODE_SHADOW {
			reasons = append(reasons, "mode "+m.String())
		}
		for _, k := range e.kills {
			if k.GetLatched() && k.GetScope() == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY && k.GetTarget() == id {
				reasons = append(reasons, "kill switch "+k.GetEventId())
			}
		}
		for _, h := range e.halts {
			if h.GetStrategyId() == id {
				reasons = append(reasons, "halt: "+h.GetReason())
			}
		}
		ss.BlockedReasons, ss.AcceptingIntents = reasons, len(reasons) == 0
		if s.GetRuntimeHosted() && !e.runtimeAt.IsZero() {
			ss.LastHeartbeat = e.runtimeAt.UnixNano()
		}
		st.Strategies = append(st.Strategies, ss)
	}
	for _, o := range e.orders {
		if !IsFinal(o.GetState()) {
			st.OpenOrders++
		}
	}
	return st
}

// Orders lists orders (newest first).
func (e *Engine) Orders(acct, strategy string, openOnly bool, since int64, limit int) []*ordersv1.Order {
	e.mu.Lock()
	defer e.mu.Unlock()
	var out []*ordersv1.Order
	for _, o := range e.orders {
		if (acct != "" && o.GetBrokerAccountId() != acct) || (strategy != "" && o.GetStrategyId() != strategy) || (openOnly && IsFinal(o.GetState())) || o.GetCreatedAt() < since {
			continue
		}
		out = append(out, proto.Clone(o).(*ordersv1.Order))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetCreatedAt() > out[j].GetCreatedAt() })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

// Intents lists intents with decisions (newest first).
func (e *Engine) Intents(strategy string, rejectedOnly bool, since int64, limit int) []*ordersv1.IntentRecord {
	e.mu.Lock()
	defer e.mu.Unlock()
	var out []*ordersv1.IntentRecord
	for _, r := range e.intents {
		if (strategy != "" && r.GetIntent().GetStrategyId() != strategy) || (rejectedOnly && r.GetState() != IntentRejected) || r.GetReceivedAt() < since {
			continue
		}
		out = append(out, proto.Clone(r).(*ordersv1.IntentRecord))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetReceivedAt() > out[j].GetReceivedAt() })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

// Fills lists fills (newest first).
func (e *Engine) Fills(acct, strategy string, since int64, limit int) []*ordersv1.Fill {
	e.mu.Lock()
	defer e.mu.Unlock()
	var out []*ordersv1.Fill
	for _, f := range e.fills {
		if (acct != "" && f.GetBrokerAccountId() != acct) || (strategy != "" && f.GetStrategyId() != strategy) || f.GetEventTime() < since {
			continue
		}
		out = append(out, proto.Clone(f).(*ordersv1.Fill))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetEventTime() > out[j].GetEventTime() })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

// Trace returns everything that shares a correlation ID: signal → intent → decision → orders →
// fills (section 07 traceability).
func (e *Engine) Trace(corr string) *ordersv1.GetTraceResponse {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := &ordersv1.GetTraceResponse{}
	intents := map[string]bool{}
	for _, r := range e.intents {
		if r.GetIntent().GetCorrelationId() == corr || r.GetIntent().GetIntentId() == corr {
			out.Intents = append(out.Intents, proto.Clone(r).(*ordersv1.IntentRecord))
			intents[r.GetIntent().GetIntentId()] = true
		}
	}
	for _, o := range e.orders {
		if intents[o.GetIntentId()] {
			out.Orders = append(out.Orders, proto.Clone(o).(*ordersv1.Order))
		}
	}
	for _, f := range e.fills {
		if intents[f.GetIntentId()] {
			out.Fills = append(out.Fills, proto.Clone(f).(*ordersv1.Fill))
		}
	}
	sort.Slice(out.Orders, func(i, j int) bool { return out.Orders[i].GetCreatedAt() < out.Orders[j].GetCreatedAt() })
	sort.Slice(out.Fills, func(i, j int) bool { return out.Fills[i].GetEventTime() < out.Fills[j].GetEventTime() })
	return out
}

// Recovering reports whether startup recovery is still running (OE-020).
func (e *Engine) Recovering() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.recovering
}

// RoutedAccounts returns the accounts routed for a horizon (RM-011).
func (e *Engine) RoutedAccounts(h commonv1.Horizon) []string { return slices.Clone(e.cfg.Routing[h]) }

// Counts summarizes the OMS for metrics.
type Counts struct {
	Open, Stuck, Unknown, Kills, Halts int
	Decisions                          map[string]int
	RuntimeAge                         time.Duration
}

// Metrics returns counts for the Prometheus collector; orders non-final for longer than stuckAfter
// count as stuck (alert OrdersStuck).
func (e *Engine) Metrics(stuckAfter time.Duration) Counts {
	e.mu.Lock()
	defer e.mu.Unlock()
	c := Counts{Decisions: map[string]int{}}
	now := e.now()
	for _, o := range e.orders {
		if IsFinal(o.GetState()) || isProtective(o) {
			continue
		}
		c.Open++
		if o.GetState() == ordersv1.OrderState_ORDER_STATE_UNKNOWN {
			c.Unknown++
		}
		if o.GetOrderType() != ordersv1.OrderType_ORDER_TYPE_STOP && now-o.GetCreatedAt() > int64(stuckAfter) {
			c.Stuck++
		}
	}
	for _, k := range e.kills {
		if k.GetLatched() {
			c.Kills++
		}
	}
	c.Halts = len(e.halts)
	for _, r := range e.intents {
		key := "approved"
		if !r.GetDecision().GetApproved() {
			key = r.GetDecision().GetFirstFailure()
		}
		c.Decisions[key]++
	}
	if !e.runtimeAt.IsZero() {
		c.RuntimeAge = e.cfg.Wall().Sub(e.runtimeAt)
	}
	return c
}

// LastPrice returns the latest final bar close seen for an instrument (zero if none).
func (e *Engine) LastPrice(instr string) decimal.Decimal {
	e.mu.Lock()
	defer e.mu.Unlock()
	if m := e.market[instr]; m != nil {
		return m.last
	}
	return decimal.Zero
}

// DailyLossUtilization returns, per active strategy, the session loss as a fraction of its
// effective daily loss limit (0 when in profit; alert DailyLossApproaching at 0.8, RM-005).
func (e *Engine) DailyLossUtilization(ctx context.Context) map[string]float64 {
	e.mu.Lock()
	type item struct {
		id  string
		lim decimal.Decimal
	}
	var items []item
	for id, s := range e.strategies {
		if s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_PAPER || s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			if l := e.effective(s.GetBrokerAccountId(), id, false).MaxDailyLoss; l.IsPositive() {
				items = append(items, item{id, l})
			}
		}
	}
	e.mu.Unlock()
	out := map[string]float64{}
	for _, it := range items {
		pv, err := e.ledger.Portfolio(ctx, it.id)
		if err != nil {
			continue
		}
		loss := decimal.Max(decimal.Zero, pd(pv.GetDayPnl()).Neg())
		out[it.id], _ = loss.Div(it.lim).Float64()
	}
	return out
}
