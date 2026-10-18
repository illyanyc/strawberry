package live

import (
	"context"
	"log/slog"
	"sort"
	"sync"
	"time"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/resilience"
)

// Streamer holds marketdata's subscription on broker-gateway's shared upstream socket (BR-015) and
// feeds the pipeline. It reconnects with backoff, re-sends the universe, and triggers a REST repair
// of the missed interval after every reconnect (MD-006). Universe changes are applied without a
// restart (MD-009).
type Streamer struct {
	Client  brokerv1.BrokerMarketDataServiceClient
	Feed    string
	Pipe    *Pipeline
	Hist    HistSource
	Backoff resilience.Backoff
	Log     *slog.Logger

	mu     sync.Mutex
	stream brokerv1.BrokerMarketDataService_StreamMarketDataClient
}

func subscription(symbols []string) *brokerv1.Subscription {
	return &brokerv1.Subscription{Bars: symbols, UpdatedBars: symbols}
}

// SetUniverse replaces the streamed universe (symbol → instrument_id) and updates the live
// subscription in place.
func (s *Streamer) SetUniverse(m map[string]string) error {
	added, removed := s.Pipe.SetSymbols(m)
	s.mu.Lock()
	st := s.stream
	s.mu.Unlock()
	if st == nil || (len(added) == 0 && len(removed) == 0) {
		return nil
	}
	return st.Send(&brokerv1.StreamMarketDataRequest{Subscribe: subscription(added), Unsubscribe: subscription(removed)})
}

// Run streams until ctx ends.
func (s *Streamer) Run(ctx context.Context) {
	if s.Backoff.Cap == 0 {
		s.Backoff = resilience.DefaultBackoff
	}
	tick := time.NewTicker(250 * time.Millisecond)
	defer tick.Stop()
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-tick.C:
				s.Pipe.Tick(ctx)
			}
		}
	}()
	attempt := 0
	for ctx.Err() == nil {
		err := s.session(ctx)
		s.Pipe.SetConnected(false)
		if ctx.Err() != nil {
			return
		}
		if s.Log != nil {
			s.Log.Warn("market-data stream ended; reconnecting", "err", err)
		}
		if resilience.Sleep(ctx, s.Backoff.Delay(attempt)) != nil {
			return
		}
		attempt++
	}
}

func (s *Streamer) session(ctx context.Context) error {
	sctx, cancel := context.WithCancel(ctx)
	defer cancel()
	st, err := s.Client.StreamMarketData(sctx)
	if err != nil {
		return err
	}
	syms := s.Pipe.Symbols()
	list := make([]string, 0, len(syms))
	for k := range syms {
		list = append(list, k)
	}
	sort.Strings(list)
	if err := st.Send(&brokerv1.StreamMarketDataRequest{Feed: s.Feed, Subscribe: subscription(list)}); err != nil {
		return err
	}
	s.mu.Lock()
	s.stream = st
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.stream = nil
		s.mu.Unlock()
	}()
	for {
		ev, err := st.Recv()
		if err != nil {
			return err
		}
		switch e := ev.GetEvent().(type) {
		case *brokerv1.StreamMarketDataResponse_Bar:
			s.Pipe.OnBar(ctx, e.Bar)
		case *brokerv1.StreamMarketDataResponse_UpdatedBar:
			s.Pipe.OnUpdatedBar(ctx, e.UpdatedBar)
		case *brokerv1.StreamMarketDataResponse_Status:
			connected := e.Status.GetState() == brokerv1.FeedState_FEED_STATE_CONNECTED
			if s.Pipe.SetConnected(connected) {
				go s.repair(ctx)
			}
		}
	}
}

func (s *Streamer) repair(ctx context.Context) {
	from := s.Pipe.EarliestLastFinal()
	if from == 0 || s.Hist == nil {
		return
	}
	n, err := s.Pipe.Repair(ctx, s.Hist, time.Unix(0, from))
	if s.Log != nil {
		s.Log.Info("stream gap repaired from REST", "bars", n, "from", time.Unix(0, from).UTC(), "err", err)
	}
}
