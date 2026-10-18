package oms

import (
	"context"
	"fmt"
	"sync"

	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/outbox"
)

// Demote is automatic containment (RM-018, SM-006): it only ever reduces risk. A live strategy moves
// to paper through a latched strategy kill switch with DEMOTE_TO_PAPER (RM-006), so it cannot send
// another live order until the owner releases the latch with a desktop passkey. A shadow or paper
// strategy moves to halted and its open non-protective orders are canceled; broker-side protective
// stops stay so open quantity is never left naked (N5). Halted and disabled strategies are left
// unchanged, which makes repeated calls idempotent. Nothing here can raise a mode (RM-015).
func (e *Engine) Demote(ctx context.Context, id, reason, evidence string, actor *ordersv1.Actor) (*ordersv1.DemoteStrategyResponse, error) {
	if id == "" || id == Manual {
		return nil, fmt.Errorf("%w: strategy_id is required and cannot be manual", ErrInvalid)
	}
	if reason == "" {
		return nil, fmt.Errorf("%w: a reason is required", ErrInvalid)
	}
	e.mu.Lock()
	s := e.strategies[id]
	if s == nil {
		e.mu.Unlock()
		return nil, ErrNotFound
	}
	from := s.GetMode()
	e.mu.Unlock()

	switch from {
	case ordersv1.StrategyMode_STRATEGY_MODE_LIVE:
		k, err := e.trigger(ctx, newID(), ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, id,
			ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_DEMOTE_TO_PAPER, "containment: "+reason, actor)
		if err != nil {
			return nil, err
		}
		e.mu.Lock()
		out := proto.Clone(e.strategies[id]).(*ordersv1.Strategy)
		_ = e.commit(ctx, &Tx{Audit: []audit.Event{e.demoteAudit(actor, id, from, out.GetMode(), reason, evidence, k.GetKillSwitch().GetEventId())}})
		e.mu.Unlock()
		return &ordersv1.DemoteStrategyResponse{Strategy: out, From: from, Changed: true,
			KillSwitchEventId: k.GetKillSwitch().GetEventId(), OrdersCanceled: k.GetOrdersCanceled()}, nil
	case ordersv1.StrategyMode_STRATEGY_MODE_SHADOW, ordersv1.StrategyMode_STRATEGY_MODE_PAPER:
	default:
		e.mu.Lock()
		out := proto.Clone(s).(*ordersv1.Strategy)
		e.mu.Unlock()
		return &ordersv1.DemoteStrategyResponse{Strategy: out, From: from}, nil
	}

	halted := ordersv1.StrategyMode_STRATEGY_MODE_HALTED
	e.mu.Lock()
	s.Mode, s.ModeReason, s.UpdatedAt = halted, "demoted by containment: "+reason, e.now()
	var cancel []*ordersv1.Order
	for _, o := range e.orders {
		if o.GetStrategyId() == id && !IsFinal(o.GetState()) && o.GetState() != ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT && !isProtective(o) {
			cancel = append(cancel, o)
		}
	}
	tx := &Tx{Strategies: []*ordersv1.Strategy{s}, Audit: []audit.Event{e.demoteAudit(actor, id, from, halted, reason, evidence, "")},
		Outbox: []outbox.Message{e.alert(alertsv1.Severity_SEVERITY_CRITICAL, "strategy demoted",
			fmt.Sprintf("%s %s → halted by %s: %s", id, modeName(from), actor.GetName(), reason), "demote-"+id, "docs/runbooks/containment.md", evidence)}}
	if err := e.commit(ctx, tx); err != nil {
		// The halted mode stays in memory: failing closed beats forgetting a demotion.
		e.log.Error("demotion persisted only in memory", "strategy", id, "err", err)
	}
	out := proto.Clone(s).(*ordersv1.Strategy)
	e.mu.Unlock()

	var wg sync.WaitGroup
	var mu sync.Mutex
	n := int32(0)
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
	return &ordersv1.DemoteStrategyResponse{Strategy: out, From: from, Changed: true, OrdersCanceled: n}, nil
}

func (e *Engine) demoteAudit(actor *ordersv1.Actor, id string, from, to ordersv1.StrategyMode, reason, evidence, killID string) audit.Event {
	return audit.Event{Actor: actor.GetName(), Action: "execution.strategy.demote", Target: id, IP: actor.GetIp(), Device: actor.GetDevice(), CorrelationID: evidence,
		Details: map[string]any{"from": from.String(), "to": to.String(), "reason": reason, "evidence_id": evidence, "kill_switch_event_id": killID}}
}

func modeName(m ordersv1.StrategyMode) string {
	switch m {
	case ordersv1.StrategyMode_STRATEGY_MODE_SHADOW:
		return "shadow"
	case ordersv1.StrategyMode_STRATEGY_MODE_PAPER:
		return "paper"
	case ordersv1.StrategyMode_STRATEGY_MODE_LIVE:
		return "live"
	case ordersv1.StrategyMode_STRATEGY_MODE_HALTED:
		return "halted"
	case ordersv1.StrategyMode_STRATEGY_MODE_DISABLED:
		return "disabled"
	default:
		return "unspecified"
	}
}
