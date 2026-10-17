package adapter

import (
	"sync"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

// Fanout delivers order updates, executions, and snapshots to in-process subscribers (the gRPC
// server streams). A subscriber that falls more than its buffer behind is closed rather than
// blocking the account; it re-subscribes and catches up from the durable bus or ListExecutions.
type Fanout struct {
	mu     sync.Mutex
	next   int
	orders map[int]chan *brokerv1.BrokerOrderUpdate
	execs  map[int]chan *ordersv1.ExecutionReport
	snaps  map[int]chan *ledgerv1.AccountSnapshot
}

// NewFanout returns an empty hub.
func NewFanout() *Fanout {
	return &Fanout{orders: map[int]chan *brokerv1.BrokerOrderUpdate{}, execs: map[int]chan *ordersv1.ExecutionReport{}, snaps: map[int]chan *ledgerv1.AccountSnapshot{}}
}

const fanBuffer = 1024

// SubscribeOrders returns a channel of order updates and a cancel function.
func (f *Fanout) SubscribeOrders() (<-chan *brokerv1.BrokerOrderUpdate, func()) {
	f.mu.Lock()
	defer f.mu.Unlock()
	id := f.next
	f.next++
	ch := make(chan *brokerv1.BrokerOrderUpdate, fanBuffer)
	f.orders[id] = ch
	return ch, func() { f.unsub(func() { closeOnce(f.orders, id) }) }
}

// SubscribeExecutions returns a channel of executions and a cancel function.
func (f *Fanout) SubscribeExecutions() (<-chan *ordersv1.ExecutionReport, func()) {
	f.mu.Lock()
	defer f.mu.Unlock()
	id := f.next
	f.next++
	ch := make(chan *ordersv1.ExecutionReport, fanBuffer)
	f.execs[id] = ch
	return ch, func() { f.unsub(func() { closeOnce(f.execs, id) }) }
}

// SubscribeSnapshots returns a channel of snapshots and a cancel function.
func (f *Fanout) SubscribeSnapshots() (<-chan *ledgerv1.AccountSnapshot, func()) {
	f.mu.Lock()
	defer f.mu.Unlock()
	id := f.next
	f.next++
	ch := make(chan *ledgerv1.AccountSnapshot, 16)
	f.snaps[id] = ch
	return ch, func() { f.unsub(func() { closeOnce(f.snaps, id) }) }
}

func (f *Fanout) unsub(fn func()) {
	f.mu.Lock()
	defer f.mu.Unlock()
	fn()
}

func closeOnce[T any](m map[int]chan T, id int) {
	if ch, ok := m[id]; ok {
		close(ch)
		delete(m, id)
	}
}

func publish[T any](f *Fanout, m map[int]chan T, v T) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for id, ch := range m {
		select {
		case ch <- v:
		default:
			close(ch)
			delete(m, id)
		}
	}
}

// PublishOrder fans out an order update.
func (f *Fanout) PublishOrder(u *brokerv1.BrokerOrderUpdate) { publish(f, f.orders, u) }

// PublishExecution fans out an execution.
func (f *Fanout) PublishExecution(e *ordersv1.ExecutionReport) { publish(f, f.execs, e) }

// PublishSnapshot fans out a snapshot. Snapshots are idempotent state, so a full subscriber just
// misses one instead of being closed.
func (f *Fanout) PublishSnapshot(s *ledgerv1.AccountSnapshot) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, ch := range f.snaps {
		select {
		case ch <- s:
		default:
		}
	}
}
