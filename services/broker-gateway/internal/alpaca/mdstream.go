package alpaca

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sync"
	"time"

	"github.com/coder/websocket"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/resilience"
)

// DataStream is the single upstream market-data WebSocket for one feed (BR-015). It keeps the
// desired subscription set, re-subscribes everything after a reconnect, and applies diffs while
// connected.
type DataStream struct {
	Feed        string // platform feed, e.g. "alpaca.sip"
	URL         string // e.g. wss://stream.data.alpaca.markets/v2/sip
	Creds       CredentialFunc
	Log         *slog.Logger
	Backoff     resilience.Backoff
	DialOptions *websocket.DialOptions
	Now         func() time.Time

	mu      sync.Mutex
	want    *brokerv1.Subscription
	have    *brokerv1.Subscription
	conn    *websocket.Conn
	status  *brokerv1.FeedStatus
	changed chan struct{}
}

// NewDataStream returns a stream for feed (e.g. "alpaca.sip") at base (wss://stream.data.alpaca.markets).
func NewDataStream(feed, base string, creds CredentialFunc, log *slog.Logger) (*DataStream, error) {
	p, err := FeedParam(feed)
	if err != nil {
		return nil, err
	}
	return &DataStream{Feed: feed, URL: base + "/v2/" + p, Creds: creds, Log: log, Backoff: resilience.DefaultBackoff, Now: time.Now,
		want: &brokerv1.Subscription{}, have: &brokerv1.Subscription{}, changed: make(chan struct{}, 1),
		status: &brokerv1.FeedStatus{Feed: feed, State: brokerv1.FeedState_FEED_STATE_DISCONNECTED}}, nil
}

// SetSubscriptions sets the desired union of symbols per channel.
func (s *DataStream) SetSubscriptions(want *brokerv1.Subscription) {
	s.mu.Lock()
	s.want = normSub(want)
	s.mu.Unlock()
	select {
	case s.changed <- struct{}{}:
	default:
	}
}

// Status returns a copy of the feed status.
func (s *DataStream) Status() *brokerv1.FeedStatus {
	s.mu.Lock()
	defer s.mu.Unlock()
	return proto2status(s.status)
}

func normSub(x *brokerv1.Subscription) *brokerv1.Subscription {
	n := func(v []string) []string {
		out := slices.Clone(v)
		slices.Sort(out)
		return slices.Compact(out)
	}
	return &brokerv1.Subscription{Bars: n(x.GetBars()), UpdatedBars: n(x.GetUpdatedBars()), Quotes: n(x.GetQuotes()), Trades: n(x.GetTrades())}
}

func diff(a, b []string) []string { // a \ b
	var out []string
	for _, x := range a {
		if !slices.Contains(b, x) {
			out = append(out, x)
		}
	}
	return out
}

func (s *DataStream) setState(st brokerv1.FeedState, detail string, emit func(*brokerv1.StreamMarketDataResponse)) {
	s.mu.Lock()
	s.status.State = st
	s.status.Detail = detail
	if st == brokerv1.FeedState_FEED_STATE_CONNECTED {
		s.status.ConnectedSince = s.Now().UnixNano()
	}
	cp := proto2status(s.status)
	s.mu.Unlock()
	emit(&brokerv1.StreamMarketDataResponse{Event: &brokerv1.StreamMarketDataResponse_Status{Status: cp}})
}

func proto2status(f *brokerv1.FeedStatus) *brokerv1.FeedStatus {
	return &brokerv1.FeedStatus{Feed: f.GetFeed(), State: f.GetState(), LastMessageAt: f.GetLastMessageAt(), ConnectedSince: f.GetConnectedSince(),
		Reconnects: f.GetReconnects(), SubscribedSymbols: f.GetSubscribedSymbols(), Detail: f.GetDetail()}
}

// errConnLimit marks Alpaca's 406 connection-limit error (V7, BR-015).
var errConnLimit = errors.New("alpaca: market-data connection limit exceeded (406)")
var errAuthFailed = errors.New("alpaca: market-data authentication failed")

// Run keeps the upstream connected until ctx ends, emitting market-data events and status changes.
func (s *DataStream) Run(ctx context.Context, emit func(*brokerv1.StreamMarketDataResponse)) {
	attempt := 0
	for ctx.Err() == nil {
		s.setState(brokerv1.FeedState_FEED_STATE_CONNECTING, "", emit)
		err := s.session(ctx, emit)
		if ctx.Err() != nil {
			return
		}
		wait := s.Backoff.Delay(attempt)
		switch {
		case errors.Is(err, errConnLimit):
			// Another client holds the single slot (a notebook, a TradingView panel on the same key).
			s.setState(brokerv1.FeedState_FEED_STATE_CONNECTION_LIMIT, err.Error(), emit)
			wait = max(wait, s.Backoff.Cap)
		case errors.Is(err, errAuthFailed):
			s.setState(brokerv1.FeedState_FEED_STATE_AUTH_FAILED, err.Error(), emit)
			wait = s.Backoff.Cap
		default:
			detail := ""
			if err != nil {
				detail = logging.Redact(err.Error())
			}
			s.setState(brokerv1.FeedState_FEED_STATE_DISCONNECTED, detail, emit)
		}
		if s.Log != nil {
			s.Log.Warn("market-data stream disconnected", "feed", s.Feed, "err", err, "retry_in", wait)
		}
		s.mu.Lock()
		s.status.Reconnects++
		s.mu.Unlock()
		attempt++
		if resilience.Sleep(ctx, wait) != nil {
			return
		}
	}
}

type dataMsg struct {
	T    string      `json:"T"`
	Msg  string      `json:"msg"`
	Code int         `json:"code"`
	S    string      `json:"S"`
	O    json.Number `json:"o"`
	H    json.Number `json:"h"`
	L    json.Number `json:"l"`
	C    json.Number `json:"c"`
	V    json.Number `json:"v"`
	N    int64       `json:"n"`
	VW   json.Number `json:"vw"`
	Ts   time.Time   `json:"t"`
	// trades
	I  json.Number `json:"i"`
	X  string      `json:"x"`
	P  json.Number `json:"p"`
	Sz json.Number `json:"s"`
	Cn []string    `json:"-"`
	// quotes
	BP json.Number `json:"bp"`
	BS json.Number `json:"bs"`
	AP json.Number `json:"ap"`
	AS json.Number `json:"as"`
}

// UnmarshalJSON handles the "c" field, which is a number for bars and a condition list for trades.
func (m *dataMsg) UnmarshalJSON(b []byte) error {
	type alias dataMsg
	var raw map[string]json.RawMessage
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	if err := d.Decode(&raw); err != nil {
		return err
	}
	var ty string
	_ = json.Unmarshal(raw["T"], &ty)
	cRaw := raw["c"]
	delete(raw, "c")
	rest, _ := json.Marshal(raw)
	d2 := json.NewDecoder(bytes.NewReader(rest))
	d2.UseNumber()
	var a alias
	if err := d2.Decode(&a); err != nil {
		return err
	}
	*m = dataMsg(a)
	if len(cRaw) > 0 {
		if ty == "b" || ty == "u" || ty == "d" {
			m.C = json.Number(bytes.Trim(cRaw, `"`))
		} else {
			_ = json.Unmarshal(cRaw, &m.Cn)
		}
	}
	return nil
}

func (s *DataStream) session(ctx context.Context, emit func(*brokerv1.StreamMarketDataResponse)) error {
	creds, err := s.Creds(ctx)
	if err != nil {
		return err
	}
	dctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	c, resp, err := websocket.Dial(dctx, s.URL, s.DialOptions)
	cancel()
	if resp != nil && resp.Body != nil {
		_ = resp.Body.Close()
	}
	if err != nil {
		return fmt.Errorf("dial: %s", logging.Redact(err.Error()))
	}
	defer func() { _ = c.CloseNow() }()
	c.SetReadLimit(16 << 20)
	read := func(ctx context.Context) ([]dataMsg, error) {
		_, data, err := c.Read(ctx)
		if err != nil {
			return nil, err
		}
		var ms []dataMsg
		if err := json.Unmarshal(data, &ms); err != nil {
			return nil, fmt.Errorf("decode: %w", err)
		}
		return ms, nil
	}
	write := func(v any) error {
		b, _ := json.Marshal(v)
		wctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		return c.Write(wctx, websocket.MessageText, b)
	}
	check := func(ms []dataMsg) error {
		for _, m := range ms {
			if m.T == "error" {
				switch m.Code {
				case 406:
					return errConnLimit
				case 401, 402, 403:
					return fmt.Errorf("%w (%d %s)", errAuthFailed, m.Code, m.Msg)
				default:
					return fmt.Errorf("alpaca data error %d: %s", m.Code, m.Msg)
				}
			}
		}
		return nil
	}
	hctx, hcancel := context.WithTimeout(ctx, 10*time.Second)
	defer hcancel()
	ms, err := read(hctx)
	if err != nil {
		return err
	}
	if err := check(ms); err != nil {
		return err
	}
	if err := write(map[string]string{"action": "auth", "key": creds.KeyID, "secret": creds.Secret}); err != nil {
		return err
	}
	if ms, err = read(hctx); err != nil {
		return err
	}
	if err := check(ms); err != nil {
		return err
	}
	s.mu.Lock()
	s.conn = c
	s.have = &brokerv1.Subscription{}
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.conn = nil
		s.mu.Unlock()
	}()
	s.setState(brokerv1.FeedState_FEED_STATE_CONNECTED, "", emit)
	if err := s.applySubs(write); err != nil {
		return err
	}
	errc := make(chan error, 1)
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case <-s.changed:
				if err := s.applySubs(write); err != nil {
					errc <- err
					return
				}
			}
		}
	}()
	for {
		select {
		case err := <-errc:
			return err
		default:
		}
		ms, err := read(ctx)
		if err != nil {
			return err
		}
		if err := check(ms); err != nil {
			return err
		}
		now := s.Now().UnixNano()
		s.mu.Lock()
		s.status.LastMessageAt = now
		s.mu.Unlock()
		for _, m := range ms {
			if ev := s.toEvent(m, now); ev != nil {
				emit(ev)
			}
		}
	}
}

func (s *DataStream) applySubs(write func(any) error) error {
	s.mu.Lock()
	want, have := s.want, s.have
	s.mu.Unlock()
	add := map[string]any{"action": "subscribe"}
	rem := map[string]any{"action": "unsubscribe"}
	nAdd, nRem := 0, 0
	for _, ch := range []struct {
		key        string
		want, have []string
	}{{"bars", want.GetBars(), have.GetBars()}, {"updatedBars", want.GetUpdatedBars(), have.GetUpdatedBars()},
		{"quotes", want.GetQuotes(), have.GetQuotes()}, {"trades", want.GetTrades(), have.GetTrades()}} {
		if d := diff(ch.want, ch.have); len(d) > 0 {
			add[ch.key] = d
			nAdd += len(d)
		}
		if d := diff(ch.have, ch.want); len(d) > 0 {
			rem[ch.key] = d
			nRem += len(d)
		}
	}
	if nRem > 0 {
		if err := write(rem); err != nil {
			return err
		}
	}
	if nAdd > 0 {
		if err := write(add); err != nil {
			return err
		}
	}
	s.mu.Lock()
	s.have = want
	s.status.SubscribedSymbols = uint32(len(want.GetBars()) + len(want.GetQuotes()) + len(want.GetTrades()))
	s.mu.Unlock()
	return nil
}

func (s *DataStream) toEvent(m dataMsg, now int64) *brokerv1.StreamMarketDataResponse {
	switch m.T {
	case "b", "u":
		b := VendorBar(m.S, mdv1.Timeframe_TIMEFRAME_1M, s.Feed, jsonBar{T: m.Ts, O: m.O, H: m.H, L: m.L, C: m.C, V: m.V, N: m.N, VW: m.VW})
		b.IngestedAt = now
		// available_at is the actual local receive time (MD-003); marketdata decides finality.
		b.AvailableAt = now
		if m.T == "u" {
			return &brokerv1.StreamMarketDataResponse{Event: &brokerv1.StreamMarketDataResponse_UpdatedBar{UpdatedBar: b}}
		}
		return &brokerv1.StreamMarketDataResponse{Event: &brokerv1.StreamMarketDataResponse_Bar{Bar: b}}
	case "t":
		return &brokerv1.StreamMarketDataResponse{Event: &brokerv1.StreamMarketDataResponse_Trade{Trade: &mdv1.Trade{
			Symbol: m.S, EventTime: m.Ts.UnixNano(), Price: num(m.P), Size: num(m.Sz), Conditions: m.Cn, Feed: s.Feed,
			TradeId: m.I.String(), Exchange: m.X, AvailableAt: now, IngestedAt: now}}}
	case "q":
		return &brokerv1.StreamMarketDataResponse{Event: &brokerv1.StreamMarketDataResponse_Quote{Quote: &mdv1.Quote{
			Symbol: m.S, EventTime: m.Ts.UnixNano(), BidPrice: num(m.BP), BidSize: num(m.BS), AskPrice: num(m.AP), AskSize: num(m.AS),
			Conditions: m.Cn, Feed: s.Feed, AvailableAt: now, IngestedAt: now}}}
	}
	return nil
}
