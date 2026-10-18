package alpaca_test

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"net/http/httptest"

	"github.com/coder/websocket"
	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/alpaca"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/alpaca/alpacatest"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/contract"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/execstore"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/mdhub"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/testkit"
)

const fixtures = "../../../../tests/fixtures/alpaca"

func fakeClient(f *alpacatest.Fake, timeout time.Duration) *alpaca.Client {
	return &alpaca.Client{
		HTTP:      &http.Client{Timeout: timeout},
		Endpoints: alpaca.Endpoints{Trading: f.URL(), Data: f.URL(), TradeWS: f.WSURL() + "/stream", DataWS: f.WSURL()},
		Creds: func(context.Context) (alpaca.Credentials, error) {
			return alpaca.Credentials{KeyID: alpacatest.KeyID, Secret: alpacatest.Secret}, nil
		},
		Trading: resilience.NewLimiter(6000, time.Minute, 50),
		Data:    resilience.NewLimiter(6000, time.Minute, 50),
	}
}

func fakeBroker(f *alpacatest.Fake) *alpaca.Broker {
	return &alpaca.Broker{AccountID: "alpaca-fake", Environment: brokerv1.Environment_ENVIRONMENT_PAPER, Client: fakeClient(f, 400*time.Millisecond),
		Feeds: []string{"alpaca.sip"}, PingInterval: time.Second}
}

type fakeHooks struct {
	f     *alpacatest.Fake
	store *execstore.Memory
}

func (h fakeHooks) AmbiguousNextSubmit() { h.f.DelayNextSubmits(1, 1500*time.Millisecond) }
func (h fakeHooks) LostNextSubmit()      { h.f.LoseNextSubmits(1) }
func (h fakeHooks) DropStream()          { h.f.DropTradeStreams() }
func (h fakeHooks) Fill(symbol string) {
	h.f.Sim.SetPrice(symbol, decimal.RequireFromString("0.5"))
	h.f.Sim.SetPrice(symbol, decimal.NewFromInt(100))
}
func (h fakeHooks) InjectManualOrder(symbol string) string {
	return h.f.Sim.InjectManualOrder(symbol, commonv1.Side_SIDE_BUY, decimal.NewFromInt(1))
}
func (h fakeHooks) ExecutionsPublished(id string) int {
	return testkit.CountExec(h.store, "alpaca-fake", id)
}
func (h fakeHooks) ReconciliationRequests() int {
	return len(h.store.Messages(execstore.ReconSubject("alpaca-fake")))
}

func TestBR001ContractAlpacaFake(t *testing.T) {
	f := alpacatest.New()
	t.Cleanup(f.Close)
	store := execstore.NewMemory()
	acct := adapter.NewAccount(fakeBroker(f), store, &testkit.Audit{}, nil, slog.New(slog.DiscardHandler), nil, testkit.FastOptions())
	testkit.Run(t, acct)
	client := contract.InProcess(t, []*adapter.Account{acct}, "execution")
	contract.Run(t, contract.Target{Client: client, Account: "alpaca-fake", Symbol: "AAPL", FarLimit: "1.00", Hooks: fakeHooks{f: f, store: store}})
	// BR-020: no account-wide endpoint was ever called.
	for _, r := range f.Requests() {
		if r == "DELETE /v2/orders" || r == "DELETE /v2/positions" {
			t.Fatalf("account-wide endpoint called: %s", r)
		}
	}
}

// TestBR001ContractAlpacaPaper runs the contract suite against the owner's Alpaca paper account.
// Opt-in: set TB_ALPACA_PAPER_KEY_ID and TB_ALPACA_PAPER_SECRET (never committed). It places and
// cancels far-from-market DAY limit orders for TB_BROKER_CONTRACT_SYMBOL (default SPY, 1 share at $1).
func TestBR001ContractAlpacaPaper(t *testing.T) {
	key, secret := os.Getenv("TB_ALPACA_PAPER_KEY_ID"), os.Getenv("TB_ALPACA_PAPER_SECRET")
	if key == "" || secret == "" {
		t.Skip("TB_ALPACA_PAPER_KEY_ID / TB_ALPACA_PAPER_SECRET not set (opt-in real-API test)")
	}
	creds, err := alpaca.FieldsToCredentials(map[string]string{"api_key_id": key, "api_secret": secret})
	if err != nil {
		t.Fatal(err)
	}
	ep := alpaca.EndpointsFor(brokerv1.Environment_ENVIRONMENT_PAPER)
	b := &alpaca.Broker{AccountID: "alpaca-paper", Environment: brokerv1.Environment_ENVIRONMENT_PAPER, Feeds: []string{"alpaca.iex"},
		Client: &alpaca.Client{HTTP: &http.Client{Timeout: 10 * time.Second}, Endpoints: ep,
			Creds:   func(context.Context) (alpaca.Credentials, error) { return creds, nil },
			Trading: resilience.NewLimiter(160, time.Minute, 10), Data: resilience.NewLimiter(160, time.Minute, 10)}}
	acct := adapter.NewAccount(b, execstore.NewMemory(), &testkit.Audit{}, nil, slog.New(slog.DiscardHandler), nil, adapter.DefaultOptions())
	testkit.Run(t, acct)
	sym := os.Getenv("TB_BROKER_CONTRACT_SYMBOL")
	if sym == "" {
		sym = "SPY"
	}
	contract.Run(t, contract.Target{Client: contract.InProcess(t, []*adapter.Account{acct}, "execution"), Account: "alpaca-paper",
		Symbol: sym, FarLimit: "1.00", ReadyTimeout: time.Minute})
}

func TestBR006AlpacaStatusMapping(t *testing.T) {
	d := decimal.RequireFromString
	cases := []struct {
		status      string
		filled, qty string
		want        ordersv1.OrderState
		mapped      bool
	}{
		{"pending_new", "0", "10", ordersv1.OrderState_ORDER_STATE_SUBMITTED, true},
		{"new", "0", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"accepted", "0", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"accepted_for_bidding", "0", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"held", "0", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"partially_filled", "3", "10", ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED, true},
		{"filled", "10", "10", ordersv1.OrderState_ORDER_STATE_FILLED, true},
		{"done_for_day", "3", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"done_for_day", "10", "10", ordersv1.OrderState_ORDER_STATE_FILLED, true},
		{"stopped", "0", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"calculated", "10", "10", ordersv1.OrderState_ORDER_STATE_FILLED, true},
		{"calculated", "4", "10", ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED, true},
		{"calculated", "0", "10", ordersv1.OrderState_ORDER_STATE_ACCEPTED, true},
		{"pending_cancel", "0", "10", ordersv1.OrderState_ORDER_STATE_PENDING_CANCEL, true},
		{"pending_replace", "0", "10", ordersv1.OrderState_ORDER_STATE_PENDING_REPLACE, true},
		{"canceled", "2", "10", ordersv1.OrderState_ORDER_STATE_CANCELED, true},
		{"expired", "0", "10", ordersv1.OrderState_ORDER_STATE_EXPIRED, true},
		{"replaced", "0", "10", ordersv1.OrderState_ORDER_STATE_REPLACED, true},
		{"rejected", "0", "10", ordersv1.OrderState_ORDER_STATE_REJECTED, true},
		{"suspended", "0", "10", ordersv1.OrderState_ORDER_STATE_UNKNOWN, true},
		{"brand_new_status", "0", "10", ordersv1.OrderState_ORDER_STATE_UNKNOWN, false},
	}
	for _, c := range cases {
		got, ok := alpaca.MapStatus(c.status, d(c.filled), d(c.qty))
		if got != c.want || ok != c.mapped {
			t.Errorf("%s filled=%s: got %s ok=%v, want %s ok=%v", c.status, c.filled, got, ok, c.want, c.mapped)
		}
	}
}

func TestBR005AlpacaRejectMapping(t *testing.T) {
	cases := []struct {
		status int
		msg    string
		want   brokerv1.RejectCode
	}{
		{403, "potential wash trade detected. use complex orders", brokerv1.RejectCode_REJECT_CODE_SELF_CROSS},
		{403, "insufficient buying power", brokerv1.RejectCode_REJECT_CODE_INSUFFICIENT_BUYING_POWER},
		{422, "client_order_id must be unique", brokerv1.RejectCode_REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID},
		{403, "asset XYZ cannot be sold short", brokerv1.RejectCode_REJECT_CODE_NOT_SHORTABLE},
		{403, "order would create an intraday margin deficit", brokerv1.RejectCode_REJECT_CODE_BROKER_RISK},
		{422, "asset \"QQQQ\" not found", brokerv1.RejectCode_REJECT_CODE_UNKNOWN_INSTRUMENT},
		{403, "asset ABC is not tradable", brokerv1.RejectCode_REJECT_CODE_NOT_TRADABLE},
		{422, "qty must be > 0", brokerv1.RejectCode_REJECT_CODE_INVALID_ORDER},
		{429, "rate limit exceeded", brokerv1.RejectCode_REJECT_CODE_RATE_LIMITED},
		{400, "something odd", brokerv1.RejectCode_REJECT_CODE_BROKER_OTHER},
	}
	for _, c := range cases {
		r := alpaca.MapReject(c.status, 40310000, c.msg)
		if r == nil || r.GetCode() != c.want || r.GetLocal() || !strings.HasPrefix(r.GetBrokerCode(), "4") {
			t.Errorf("%d %q: got %+v want %s", c.status, c.msg, r, c.want)
		}
	}
	if alpaca.MapReject(503, 0, "service unavailable") != nil {
		t.Error("5xx must be ambiguous (nil), not a rejection")
	}
}

func TestBR020NoAccountWideEndpoints(t *testing.T) {
	for _, r := range alpaca.Routes {
		if r.Method == http.MethodDelete && (r.Path == "/v2/orders" || r.Path == "/v2/positions" || strings.HasPrefix(r.Path, "/v2/positions")) {
			t.Fatalf("forbidden account-wide route in table: %s %s", r.Method, r.Path)
		}
	}
	f := alpacatest.New()
	defer f.Close()
	b := fakeBroker(f)
	// Cancel requires a specific order ID; there is no code path to cancel-all.
	if err := b.Cancel(context.Background(), adapter.OrderRef{}); err == nil {
		t.Fatal("cancel without an order ID must fail")
	}
}

func TestBR018BracketLegsAttributed(t *testing.T) {
	f := alpacatest.New()
	defer f.Close()
	b := fakeBroker(f)
	coid := contract.NewClientOrderID()
	o, err := b.Submit(context.Background(), &brokerv1.BrokerOrderRequest{ClientOrderId: coid, Instrument: &commonv1.Instrument{Symbol: "AAPL"},
		Side: commonv1.Side_SIDE_BUY, Qty: &commonv1.Decimal{Value: "5"}, Type: ordersv1.OrderType_ORDER_TYPE_LIMIT,
		LimitPrice: &commonv1.Decimal{Value: "1.00"}, Tif: ordersv1.TimeInForce_TIME_IN_FORCE_GTC,
		Bracket: &brokerv1.BracketSpec{OrderClass: brokerv1.OrderClass_ORDER_CLASS_BRACKET, TakeProfitLimitPrice: &commonv1.Decimal{Value: "2.00"},
			StopLossStopPrice: &commonv1.Decimal{Value: "0.50"}}})
	if err != nil {
		t.Fatal(err)
	}
	if o.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_BRACKET || len(o.GetLegs()) != 2 {
		t.Fatalf("bracket legs missing: %+v", o)
	}
	for _, l := range o.GetLegs() {
		if l.GetClientOrderId() != coid || l.GetParentBrokerOrderId() != o.GetBrokerOrderId() || l.GetBrokerStatus() != "held" {
			t.Fatalf("leg not attributed to parent: %+v", l)
		}
	}
}

func TestHistoricalBarsFixtureDecodesExactly(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(fixtures, "bars_1min.json"))
	if err != nil {
		t.Fatal(err)
	}
	f := alpacatest.New()
	defer f.Close()
	var seen *http.Request
	f.SetBarsResponse(func(r *http.Request) any { seen = r; return json.RawMessage(raw) })
	c := fakeClient(f, time.Second)
	start := time.Date(2026, 3, 2, 14, 30, 0, 0, time.UTC)
	resp, err := c.HistoricalBars(context.Background(), &brokerv1.GetHistoricalBarsRequest{Symbols: []string{"AAPL", "MSFT"},
		Timeframe: mdv1.Timeframe_TIMEFRAME_1M, Start: start.UnixNano(), End: start.Add(time.Hour).UnixNano(), Feed: "alpaca.sip"})
	if err != nil {
		t.Fatal(err)
	}
	q := seen.URL.Query()
	if q.Get("adjustment") != "raw" || q.Get("feed") != "sip" || q.Get("timeframe") != "1Min" || q.Get("asof") != "2026-03-02" {
		t.Fatalf("request params: %v (DF-005 requires adjustment=raw)", q)
	}
	if len(resp.GetBars()) != 3 || resp.GetNextPageToken() == "" {
		t.Fatalf("bars: %d token=%q", len(resp.GetBars()), resp.GetNextPageToken())
	}
	b := resp.GetBars()[0]
	if b.GetSymbol() != "AAPL" || b.GetOpen().GetValue() != "241.12" || b.GetHigh().GetValue() != "241.5" || b.GetVwap().GetValue() != "241.2611" ||
		b.GetVolume().GetValue() != "812345" || b.GetTradeCount() != 9123 || b.GetFeed() != "alpaca.sip" || b.GetSource() != "alpaca" ||
		b.GetAdjustmentBasis() != mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW || b.GetTsClose()-b.GetTsOpen() != int64(time.Minute) {
		t.Fatalf("bar decoded wrong: %+v", b)
	}
}

func TestCorporateActionsFixture(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(fixtures, "corporate_actions.json"))
	if err != nil {
		t.Fatal(err)
	}
	srv := http.NewServeMux()
	srv.HandleFunc("/v1/corporate-actions", func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(raw) })
	f := alpacatest.New()
	defer f.Close()
	ts := newServer(t, srv)
	c := fakeClient(f, time.Second)
	c.Endpoints.Data = ts
	resp, err := c.CorporateActions(context.Background(), &brokerv1.GetCorporateActionsRequest{Start: time.Date(2022, 1, 1, 0, 0, 0, 0, time.UTC).UnixNano(), End: time.Now().UnixNano()})
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]*mdv1.CorporateAction{}
	for _, a := range resp.GetActions() {
		got[a.GetSourceId()] = a
	}
	if r := got["fs-0001"].GetRatio().GetValue(); r != "10" || got["fs-0001"].GetType() != mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPLIT {
		t.Fatalf("forward split ratio %q", r)
	}
	if r := got["rs-0001"].GetRatio().GetValue(); r != "0.25" {
		t.Fatalf("reverse split ratio %q", r)
	}
	if a := got["cd-0001"]; a.GetAmount().GetValue() != "0.25" || a.GetType() != mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DIVIDEND {
		t.Fatalf("dividend %+v", a)
	}
	if a := got["nc-0001"]; a.GetSymbol() != "FB" || a.GetNewSymbol() != "META" {
		t.Fatalf("name change %+v", a)
	}
	ny, _ := time.LoadLocation("America/New_York")
	if got["fs-0001"].GetExDate() != time.Date(2024, 6, 10, 0, 0, 0, 0, ny).UnixNano() {
		t.Fatal("ex_date must be 00:00 New York time")
	}
}

func TestDataStreamFixtureEvents(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(fixtures, "data_stream.json"))
	if err != nil {
		t.Fatal(err)
	}
	f := alpacatest.New()
	defer f.Close()
	c := fakeClient(f, time.Second)
	ds, _ := alpaca.NewDataStream("alpaca.sip", c.Endpoints.DataWS, c.Creds, slog.New(slog.DiscardHandler))
	ds.Backoff = resilience.Backoff{Base: 10 * time.Millisecond, Cap: 50 * time.Millisecond}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	evs := make(chan *brokerv1.StreamMarketDataResponse, 100)
	go ds.Run(ctx, func(e *brokerv1.StreamMarketDataResponse) { evs <- e })
	waitTrue(t, f.DataConnected)
	ds.SetSubscriptions(&brokerv1.Subscription{Bars: []string{"AAPL"}, UpdatedBars: []string{"AAPL"}, Trades: []string{"AAPL"}, Quotes: []string{"AAPL"}})
	time.Sleep(50 * time.Millisecond)
	f.PushData(string(raw))
	var kinds []string
	timeout := time.After(3 * time.Second)
	for len(kinds) < 4 {
		select {
		case e := <-evs:
			switch x := e.GetEvent().(type) {
			case *brokerv1.StreamMarketDataResponse_Bar:
				if x.Bar.GetClose().GetValue() != "241.37" || x.Bar.GetAvailableAt() == 0 {
					t.Fatalf("bar %+v", x.Bar)
				}
				kinds = append(kinds, "b")
			case *brokerv1.StreamMarketDataResponse_UpdatedBar:
				if x.UpdatedBar.GetHigh().GetValue() != "241.55" {
					t.Fatalf("updated bar %+v", x.UpdatedBar)
				}
				kinds = append(kinds, "u")
			case *brokerv1.StreamMarketDataResponse_Trade:
				if x.Trade.GetPrice().GetValue() != "241.36" || !slices.Equal(x.Trade.GetConditions(), []string{"@", "I"}) {
					t.Fatalf("trade %+v", x.Trade)
				}
				kinds = append(kinds, "t")
			case *brokerv1.StreamMarketDataResponse_Quote:
				if x.Quote.GetAskPrice().GetValue() != "241.37" {
					t.Fatalf("quote %+v", x.Quote)
				}
				kinds = append(kinds, "q")
			}
		case <-timeout:
			t.Fatalf("got only %v", kinds)
		}
	}
}

func waitTrue(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("condition not met")
}

func TestBR015SingleUpstreamUnionSubscriptions(t *testing.T) {
	f := alpacatest.New()
	defer f.Close()
	c := fakeClient(f, time.Second)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	hub := mdhub.New(ctx, func(feed string) (mdhub.Upstream, error) {
		ds, err := alpaca.NewDataStream(feed, c.Endpoints.DataWS, c.Creds, slog.New(slog.DiscardHandler))
		return ds, err
	}, nil)
	a, _ := hub.Register("alpaca.sip", 0)
	b, _ := hub.Register("alpaca.sip", 0)
	a.Update(&brokerv1.Subscription{Bars: []string{"AAPL"}}, nil)
	b.Update(&brokerv1.Subscription{Bars: []string{"MSFT"}}, nil)
	waitTrue(t, f.DataConnected)
	if hub.Upstreams() != 1 {
		t.Fatalf("%d upstream connections for one feed", hub.Upstreams())
	}
	time.Sleep(50 * time.Millisecond)
	f.PushData(`[{"T":"b","S":"MSFT","o":1,"h":1,"l":1,"c":1,"v":1,"t":"2026-03-02T14:30:00Z","n":1,"vw":1}]`)
	got := recvBar(t, b.C)
	if got.GetSymbol() != "MSFT" {
		t.Fatalf("client b got %s", got.GetSymbol())
	}
	select {
	case ev := <-a.C:
		if ev.GetBar() != nil {
			t.Fatalf("client a received a symbol it did not subscribe to: %+v", ev)
		}
	case <-time.After(100 * time.Millisecond):
	}
}

func recvBar(t *testing.T, ch <-chan *brokerv1.StreamMarketDataResponse) *mdv1.Bar {
	t.Helper()
	timeout := time.After(3 * time.Second)
	for {
		select {
		case ev := <-ch:
			if ev.GetBar() != nil {
				return ev.GetBar()
			}
		case <-timeout:
			t.Fatal("no bar received")
		}
	}
}

func TestBR015ConnectionLimit(t *testing.T) {
	f := alpacatest.New()
	defer f.Close()
	// Someone else (a notebook, a TradingView panel on the same key) holds the slot.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	other, _, err := websocket.Dial(ctx, f.WSURL()+"/v2/sip", nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = other.CloseNow() }()
	_, _, _ = other.Read(ctx)
	_ = other.Write(ctx, websocket.MessageText, []byte(`{"action":"auth","key":"`+alpacatest.KeyID+`","secret":"`+alpacatest.Secret+`"}`))
	_, _, _ = other.Read(ctx)
	go func() {
		for {
			if _, _, err := other.Read(ctx); err != nil {
				return
			}
		}
	}()
	c := fakeClient(f, time.Second)
	ds, _ := alpaca.NewDataStream("alpaca.sip", c.Endpoints.DataWS, c.Creds, slog.New(slog.DiscardHandler))
	ds.Backoff = resilience.Backoff{Base: 10 * time.Millisecond, Cap: time.Hour}
	go ds.Run(ctx, func(*brokerv1.StreamMarketDataResponse) {})
	waitTrue(t, func() bool { return ds.Status().GetState() == brokerv1.FeedState_FEED_STATE_CONNECTION_LIMIT })
	if !strings.Contains(ds.Status().GetDetail(), "406") {
		t.Fatalf("detail %q", ds.Status().GetDetail())
	}
}

func newServer(t *testing.T, h http.Handler) string {
	t.Helper()
	s := httptest.NewServer(h)
	t.Cleanup(s.Close)
	return s.URL
}
