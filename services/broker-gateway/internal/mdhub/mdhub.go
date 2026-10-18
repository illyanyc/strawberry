// Package mdhub multiplexes every market-data subscriber over one upstream connection per feed
// (rule BR-015). The upstream subscribes the union of all clients' symbols; events are routed to the
// clients that asked for them. Final-bar inputs (bars, updated bars) are never dropped: a client that
// cannot keep up is disconnected and repairs the gap from REST (MD-006). Quotes and trades for a full
// client are dropped (UI-only data).
package mdhub

import (
	"context"
	"errors"
	"slices"
	"sync"

	"github.com/prometheus/client_golang/prometheus"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
)

// Upstream is one vendor connection (alpaca.DataStream in production).
type Upstream interface {
	SetSubscriptions(*brokerv1.Subscription)
	Run(ctx context.Context, emit func(*brokerv1.StreamMarketDataResponse))
	Status() *brokerv1.FeedStatus
}

// Factory creates the upstream for a feed.
type Factory func(feed string) (Upstream, error)

// ErrSlowConsumer closes a client whose queue overflowed on bar data.
var ErrSlowConsumer = errors.New("mdhub: client too slow; disconnected (repair the gap from REST)")

// Hub owns the upstreams.
type Hub struct {
	ctx     context.Context
	factory Factory
	mu      sync.Mutex
	feeds   map[string]*feed
	dropped *prometheus.CounterVec
	events  *prometheus.CounterVec
}

type feed struct {
	up      Upstream
	clients map[*Client]bool
}

// New returns a hub whose upstreams live until ctx ends.
func New(ctx context.Context, f Factory, reg prometheus.Registerer) *Hub {
	h := &Hub{ctx: ctx, factory: f, feeds: map[string]*feed{},
		dropped: prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_hub_dropped_total", Help: "Quotes/trades dropped for slow clients."}, []string{"feed"}),
		events:  prometheus.NewCounterVec(prometheus.CounterOpts{Name: "tb_md_hub_events_total", Help: "Upstream market-data events by type."}, []string{"feed", "type"}),
	}
	if reg != nil {
		reg.MustRegister(h.dropped, h.events)
	}
	return h
}

// Client is one subscriber.
type Client struct {
	hub    *Hub
	feed   string
	C      chan *brokerv1.StreamMarketDataResponse
	mu     sync.Mutex
	sub    *brokerv1.Subscription
	err    error
	closed bool
}

// Err returns why the client was closed by the hub (nil if closed by the caller).
func (c *Client) Err() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.err
}

// Register adds a client for feed, creating the upstream on first use.
func (h *Hub) Register(feedName string, buffer int) (*Client, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	f := h.feeds[feedName]
	if f == nil {
		up, err := h.factory(feedName)
		if err != nil {
			return nil, err
		}
		f = &feed{up: up, clients: map[*Client]bool{}}
		h.feeds[feedName] = f
		go up.Run(h.ctx, func(ev *brokerv1.StreamMarketDataResponse) { h.route(feedName, ev) })
	}
	if buffer <= 0 {
		buffer = 8192
	}
	c := &Client{hub: h, feed: feedName, C: make(chan *brokerv1.StreamMarketDataResponse, buffer), sub: &brokerv1.Subscription{}}
	f.clients[c] = true
	// The new client learns the current feed state immediately.
	c.C <- &brokerv1.StreamMarketDataResponse{Event: &brokerv1.StreamMarketDataResponse_Status{Status: f.up.Status()}}
	return c, nil
}

func merge(cur []string, add, rem []string) []string {
	out := slices.Clone(cur)
	for _, a := range add {
		if !slices.Contains(out, a) {
			out = append(out, a)
		}
	}
	out = slices.DeleteFunc(out, func(x string) bool { return slices.Contains(rem, x) })
	slices.Sort(out)
	return out
}

// Update changes the client's subscription and re-computes the upstream union.
func (c *Client) Update(add, rem *brokerv1.Subscription) {
	c.mu.Lock()
	c.sub = &brokerv1.Subscription{
		Bars:        merge(c.sub.GetBars(), add.GetBars(), rem.GetBars()),
		UpdatedBars: merge(c.sub.GetUpdatedBars(), add.GetUpdatedBars(), rem.GetUpdatedBars()),
		Quotes:      merge(c.sub.GetQuotes(), add.GetQuotes(), rem.GetQuotes()),
		Trades:      merge(c.sub.GetTrades(), add.GetTrades(), rem.GetTrades()),
	}
	c.mu.Unlock()
	c.hub.recompute(c.feed)
}

// Close removes the client.
func (c *Client) Close() { c.hub.remove(c, nil) }

func (h *Hub) remove(c *Client, err error) {
	h.mu.Lock()
	f := h.feeds[c.feed]
	if f != nil {
		delete(f.clients, c)
	}
	h.mu.Unlock()
	c.mu.Lock()
	if !c.closed {
		c.closed = true
		c.err = err
		close(c.C)
	}
	c.mu.Unlock()
	h.recompute(c.feed)
}

func (h *Hub) recompute(feedName string) {
	h.mu.Lock()
	f := h.feeds[feedName]
	if f == nil {
		h.mu.Unlock()
		return
	}
	union := &brokerv1.Subscription{}
	for c := range f.clients {
		c.mu.Lock()
		union.Bars = merge(union.Bars, c.sub.GetBars(), nil)
		union.UpdatedBars = merge(union.UpdatedBars, c.sub.GetUpdatedBars(), nil)
		union.Quotes = merge(union.Quotes, c.sub.GetQuotes(), nil)
		union.Trades = merge(union.Trades, c.sub.GetTrades(), nil)
		c.mu.Unlock()
	}
	up := f.up
	h.mu.Unlock()
	up.SetSubscriptions(union)
}

func symbolOf(ev *brokerv1.StreamMarketDataResponse) (string, string) {
	switch e := ev.GetEvent().(type) {
	case *brokerv1.StreamMarketDataResponse_Bar:
		return "bar", e.Bar.GetSymbol()
	case *brokerv1.StreamMarketDataResponse_UpdatedBar:
		return "updated_bar", e.UpdatedBar.GetSymbol()
	case *brokerv1.StreamMarketDataResponse_Quote:
		return "quote", e.Quote.GetSymbol()
	case *brokerv1.StreamMarketDataResponse_Trade:
		return "trade", e.Trade.GetSymbol()
	case *brokerv1.StreamMarketDataResponse_Status:
		return "status", ""
	default:
		return "", ""
	}
}

func (h *Hub) route(feedName string, ev *brokerv1.StreamMarketDataResponse) {
	kind, sym := symbolOf(ev)
	h.events.WithLabelValues(feedName, kind).Inc()
	h.mu.Lock()
	f := h.feeds[feedName]
	var targets []*Client
	if f != nil {
		for c := range f.clients {
			targets = append(targets, c)
		}
	}
	h.mu.Unlock()
	var slow []*Client
	for _, c := range targets {
		c.mu.Lock()
		want := false
		switch kind {
		case "status":
			want = true
		case "bar":
			want = slices.Contains(c.sub.GetBars(), sym)
		case "updated_bar":
			want = slices.Contains(c.sub.GetUpdatedBars(), sym)
		case "quote":
			want = slices.Contains(c.sub.GetQuotes(), sym)
		case "trade":
			want = slices.Contains(c.sub.GetTrades(), sym)
		}
		if !want || c.closed {
			c.mu.Unlock()
			continue
		}
		// Send under the client lock so remove() can never close the channel mid-send.
		select {
		case c.C <- ev:
		default:
			if kind == "quote" || kind == "trade" {
				h.dropped.WithLabelValues(feedName).Inc()
			} else {
				slow = append(slow, c)
			}
		}
		c.mu.Unlock()
	}
	for _, c := range slow {
		h.remove(c, ErrSlowConsumer)
	}
}

// Statuses returns every feed's status.
func (h *Hub) Statuses() []*brokerv1.FeedStatus {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]*brokerv1.FeedStatus, 0, len(h.feeds))
	for _, f := range h.feeds {
		out = append(out, f.up.Status())
	}
	return out
}

// Upstreams returns the number of upstream connections (one per feed, BR-015).
func (h *Hub) Upstreams() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return len(h.feeds)
}
