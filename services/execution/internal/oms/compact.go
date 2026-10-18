package oms

import "slices"

// Compact drops finished history older than before (UTC ns) from memory; the store keeps it. It
// removes final orders with no working order in their intent and no working child, terminal intents
// with no working order, fills, and order timestamps. The gate reads open orders, one minute of
// order times, five sessions of fills (day trades), the duplicate window, and the last twenty order
// sizes, so compacting a week or more back never changes a decision. The backtest simcore calls it at
// session closes to keep long simulations linear (BT-003); the live service does not call it.
func (e *Engine) Compact(before int64) int {
	e.mu.Lock()
	defer e.mu.Unlock()
	workingIntent := map[string]bool{}
	workingParent := map[string]bool{}
	for _, o := range e.orders {
		if !IsFinal(o.GetState()) {
			workingIntent[o.GetIntentId()] = true
			if p := o.GetParentOrderId(); p != "" {
				workingParent[p] = true
			}
		}
	}
	n := 0
	for id, o := range e.orders {
		if !IsFinal(o.GetState()) || o.GetUpdatedAt() >= before || workingIntent[o.GetIntentId()] || workingParent[id] {
			continue
		}
		delete(e.orders, id)
		delete(e.protect, id)
		if b := o.GetBrokerOrderId(); b != "" {
			delete(e.byBroker, o.GetBrokerAccountId()+"|"+b)
		}
		key := o.GetBrokerAccountId() + "|" + o.GetClientOrderId()
		e.byClient[key] = slices.DeleteFunc(e.byClient[key], func(x string) bool { return x == id })
		if len(e.byClient[key]) == 0 {
			delete(e.byClient, key)
		}
		n++
	}
	for id, r := range e.intents {
		switch r.GetState() {
		case IntentDone, IntentFailed, IntentRejected, IntentShadow:
		default:
			continue
		}
		if r.GetReceivedAt() < before && !workingIntent[id] {
			delete(e.intents, id)
			n++
		}
	}
	for k, f := range e.fills {
		if f.GetEventTime() < before {
			delete(e.fills, k)
			n++
		}
	}
	for sid, ts := range e.orderTimes {
		e.orderTimes[sid] = slices.DeleteFunc(ts, func(t int64) bool { return t < before })
	}
	return n
}
