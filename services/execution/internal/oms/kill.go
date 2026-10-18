package oms

import (
	"context"
	"fmt"
	"sort"
	"sync"

	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/execution/internal/risk"
)

func (e *Engine) killCovers(o *ordersv1.Order) bool {
	if o.GetStrategyId() == Manual {
		return false
	}
	for _, k := range e.kills {
		if k.GetLatched() && risk.KillMatches(risk.KillSwitch{Scope: scopeName(k.GetScope()), Target: k.GetTarget()}, o.GetBrokerAccountId(), o.GetStrategyId(), o.GetInstrumentId()) {
			return true
		}
	}
	return false
}

// Trigger engages a kill switch (RM-006): it latches immediately (the gate rejects every new intent
// in scope), then cancels strategy-owned open orders in scope in parallel (manual orders are never
// touched, VP-006). FREEZE only latches; CANCEL_OPEN cancels working orders and keeps broker-side
// protective stops; FLATTEN_STRATEGY_OWNED cancels everything the strategies have open in scope,
// including protection, and exits strategy-owned quantity only; DEMOTE_TO_PAPER also moves live
// strategies in scope to paper. Engaging needs no step-up (RM-007).
func (e *Engine) Trigger(ctx context.Context, scope ordersv1.KillSwitchScope, target string, action ordersv1.KillSwitchAction, reason string, actor *ordersv1.Actor) (*ordersv1.TriggerKillSwitchResponse, error) {
	return e.trigger(ctx, newID(), scope, target, action, reason, actor)
}

func (e *Engine) trigger(ctx context.Context, eventID string, scope ordersv1.KillSwitchScope, target string, action ordersv1.KillSwitchAction, reason string, actor *ordersv1.Actor) (*ordersv1.TriggerKillSwitchResponse, error) {
	if scope == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_UNSPECIFIED || action == ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_UNSPECIFIED ||
		action == ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_RELEASE || (scope != ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL && target == "") {
		return nil, fmt.Errorf("%w: scope, target, and an engage action are required", ErrInvalid)
	}
	start := e.cfg.Wall()
	e.mu.Lock()
	if k := e.kills[eventID]; k != nil {
		e.mu.Unlock()
		return &ordersv1.TriggerKillSwitchResponse{KillSwitch: k}, nil
	}
	k := &ordersv1.KillSwitch{EventId: eventID, Scope: scope, Target: target, Action: action, EngagedBy: actor.GetName(), Reason: reason, EngagedAt: e.now(), Latched: true}
	e.kills[eventID] = k
	evt := &ordersv1.KillSwitchEvent{EventId: eventID, Scope: scope, Target: target, Action: action, Actor: actor.GetName(), Reason: reason, OccurredAt: k.GetEngagedAt(), Latched: true}
	tx := &Tx{Kills: []*ordersv1.KillSwitch{k}, Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.killswitch.engage", Target: scopeName(scope) + ":" + target,
		IP: actor.GetIp(), Device: actor.GetDevice(), Details: map[string]any{"action": action.String(), "reason": reason, "event_id": eventID}}},
		Outbox: []outbox.Message{msg("risk.kill", "kill:"+eventID, evt, ""), e.alert(alertsv1.Severity_SEVERITY_CRITICAL, "kill switch engaged",
			fmt.Sprintf("%s %s %s by %s: %s", scopeName(scope), target, action, actor.GetName(), reason), "kill-"+eventID, "docs/runbooks/kill-switch.md", "")}}
	flatten := action == ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED
	var cancel []*ordersv1.Order
	if action != ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FREEZE {
		for _, o := range e.orders {
			if IsFinal(o.GetState()) || o.GetState() == ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT || !e.killCovers(o) {
				continue
			}
			if isProtective(o) && !flatten {
				continue
			}
			cancel = append(cancel, o)
		}
	}
	var demoted []*ordersv1.Strategy
	var flat []*ordersv1.Strategy
	for _, s := range e.strategies {
		in := risk.KillMatches(risk.KillSwitch{Scope: scopeName(scope), Target: target}, s.GetBrokerAccountId(), s.GetStrategyId(), "")
		if scope == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_INSTRUMENT {
			in = true
		}
		if !in {
			continue
		}
		if action == ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_DEMOTE_TO_PAPER && s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			s.Mode, s.ModeReason, s.UpdatedAt = ordersv1.StrategyMode_STRATEGY_MODE_PAPER, "demoted by kill switch "+eventID, e.now()
			demoted = append(demoted, s)
		}
		if flatten {
			flat = append(flat, s)
		}
	}
	tx.Strategies = demoted
	if err := e.commit(ctx, tx); err != nil {
		// The latch stays in memory: failing closed beats forgetting an engagement.
		e.log.Error("kill switch persisted only in memory", "event", eventID, "err", err)
	}
	e.mu.Unlock()
	var wg sync.WaitGroup
	var mu sync.Mutex
	n := 0
	for _, o := range cancel {
		wg.Add(1)
		go func(o *ordersv1.Order) {
			defer wg.Done()
			if e.cancelOrder(ctx, o) {
				mu.Lock()
				n++
				mu.Unlock()
			}
		}(o)
	}
	wg.Wait()
	e.mu.Lock()
	k.CancelCompletedNs = int64(e.cfg.Wall().Sub(start))
	_ = e.commit(ctx, &Tx{Kills: []*ordersv1.KillSwitch{k}})
	out := proto.Clone(k).(*ordersv1.KillSwitch)
	e.mu.Unlock()
	intents := 0
	if flatten {
		intents = e.flatten(ctx, flat, scope, target, eventID)
	}
	return &ordersv1.TriggerKillSwitchResponse{KillSwitch: out, OrdersCanceled: int32(n), FlattenIntents: int32(intents)}, nil
}

// flatten exits strategy-owned quantity in scope through the normal gate (source KILL_SWITCH,
// which only bypasses the freeze it caused), sized from the ledger: never manual (VP-006).
func (e *Engine) flatten(ctx context.Context, strategies []*ordersv1.Strategy, scope ordersv1.KillSwitchScope, target, eventID string) int {
	n := 0
	for _, s := range strategies {
		pv, err := e.ledger.Portfolio(ctx, s.GetStrategyId())
		if err != nil {
			continue
		}
		for _, pos := range pv.GetPositions() {
			q := pd(pos.GetQty())
			if !q.IsPositive() || (scope == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_INSTRUMENT && pos.GetInstrumentId() != target) {
				continue
			}
			it := &ordersv1.OrderIntent{IntentId: newID(), StrategyId: s.GetStrategyId(), BrokerAccountId: s.GetBrokerAccountId(), InstrumentId: pos.GetInstrumentId(),
				Side: commonv1.Side_SIDE_SELL, Sizing: &ordersv1.OrderIntent_Quantity{Quantity: d(q)}, OrderType: ordersv1.OrderType_ORDER_TYPE_MARKET,
				Tif: ordersv1.TimeInForce_TIME_IN_FORCE_DAY, Reason: "flatten by kill switch " + eventID, Mode: modeOf(s), CorrelationId: eventID,
				CreatedAt: e.now(), Horizon: s.GetHorizon(), Kind: ordersv1.IntentKind_INTENT_KIND_CLOSE, Source: ordersv1.IntentSource_INTENT_SOURCE_KILL_SWITCH, DecisionTime: e.now()}
			if _, err := e.Submit(ctx, it, &ordersv1.Actor{Name: "killswitch:" + eventID}); err == nil {
				n++
			}
		}
	}
	return n
}

func modeOf(s *ordersv1.Strategy) commonv1.Mode {
	if s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
		return commonv1.Mode_MODE_LIVE
	}
	return commonv1.Mode_MODE_PAPER
}

// Release clears a latched kill switch. It requires a fresh passkey step-up from a desktop client
// (RM-007, UI-006); automatic engagements are released the same way (re-enabling is manual, N4).
func (e *Engine) Release(ctx context.Context, eventID, reason string, actor *ordersv1.Actor) (*ordersv1.KillSwitch, error) {
	if actor.GetStepUpMethod() != "passkey" || actor.GetMobile() {
		return nil, ErrStepUp
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	k := e.kills[eventID]
	if k == nil || !k.GetLatched() {
		return nil, ErrNotFound
	}
	k.Latched, k.ReleasedAt, k.ReleasedBy = false, e.now(), actor.GetName()
	evt := &ordersv1.KillSwitchEvent{EventId: newID(), Scope: k.GetScope(), Target: k.GetTarget(), Action: ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_RELEASE,
		Actor: actor.GetName(), Reason: reason, OccurredAt: e.now(), ReleasesEventId: eventID}
	err := e.commit(ctx, &Tx{Kills: []*ordersv1.KillSwitch{k}, Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.killswitch.release", Target: eventID,
		IP: actor.GetIp(), Device: actor.GetDevice(), Details: map[string]any{"reason": reason, "step_up": actor.GetStepUpMethod()}}},
		Outbox: []outbox.Message{msg("risk.kill", "kill:"+evt.GetEventId(), evt, "")}})
	if err != nil {
		k.Latched = true
		return nil, err
	}
	// Released switches stay known so a late copy of the engagement on risk.kill cannot re-latch it.
	return proto.Clone(k).(*ordersv1.KillSwitch), nil
}

// OnKillEvent engages a switch published on risk.kill by another actor (tbctl kill, monitor).
// Releases are never accepted from the bus: they need step-up (RM-007).
func (e *Engine) OnKillEvent(ctx context.Context, ev *ordersv1.KillSwitchEvent) error {
	if ev.GetAction() == ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_RELEASE || ev.GetEventId() == "" {
		return nil
	}
	_, err := e.trigger(ctx, ev.GetEventId(), ev.GetScope(), ev.GetTarget(), ev.GetAction(), ev.GetReason(), &ordersv1.Actor{Name: ev.GetActor()})
	return err
}

// KillSwitches lists latched switches, or every known one when all is set.
func (e *Engine) KillSwitches(all ...bool) []*ordersv1.KillSwitch {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]*ordersv1.KillSwitch, 0, len(e.kills))
	for _, k := range e.kills {
		if k.GetLatched() || (len(all) > 0 && all[0]) {
			out = append(out, proto.Clone(k).(*ordersv1.KillSwitch))
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetEngagedAt() < out[j].GetEngagedAt() })
	return out
}
