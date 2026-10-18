// Package alpacatest is an in-process fake of Alpaca's trading REST API, trade_updates stream, and
// market-data stream, backed by the broker simulator's matching engine. It speaks Alpaca's JSON
// shapes (docs.alpaca.markets) so the real adapter code (HTTP, JSON, WebSocket) is exercised in CI
// without credentials. Fault hooks: delayed submit responses (client timeouts), dropped streams,
// manual orders, a second data connection (406).
package alpacatest

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/sim"
)

// KeyID and Secret are the fake's only valid credential (test-only values, not secrets).
const (
	KeyID  = "PKTESTFAKEKEY0000000"
	Secret = "fake-secret-for-tests-only-0000000000000"
)

// Fake is the server.
type Fake struct {
	Sim    *sim.Sim
	Server *httptest.Server

	mu           sync.Mutex
	tradeConns   map[*websocket.Conn]bool
	dataConn     *websocket.Conn
	submitDelay  time.Duration
	submitDelayN int
	lostN        int
	requests     []string
	cancelRun    context.CancelFunc
	done         chan struct{}
	barsResponse func(r *http.Request) any
}

// New starts a fake with a fresh simulator.
func New() *Fake {
	f := &Fake{Sim: sim.New(sim.Config{AccountID: "alpaca-fake"}), tradeConns: map[*websocket.Conn]bool{}, done: make(chan struct{})}
	mux := http.NewServeMux()
	mux.HandleFunc("/", f.rest)
	mux.HandleFunc("/stream", f.tradeStream)
	mux.HandleFunc("/v2/sip", f.dataStream)
	mux.HandleFunc("/v2/iex", f.dataStream)
	f.Server = httptest.NewServer(mux)
	ctx, cancel := context.WithCancel(context.Background())
	f.cancelRun = cancel
	// The fake consumes the simulator's events and broadcasts them to trade_updates clients.
	go func() {
		for ctx.Err() == nil {
			_ = f.Sim.Stream(ctx, func() {}, f.broadcast)
		}
	}()
	return f
}

// Close stops the server.
func (f *Fake) Close() {
	f.cancelRun()
	close(f.done)
	f.DropTradeStreams()
	f.DropDataStream()
	f.Server.CloseClientConnections()
	f.Server.Close()
}

// URL of the REST API.
func (f *Fake) URL() string { return f.Server.URL }

// WSURL of the streams (ws://...).
func (f *Fake) WSURL() string { return "ws" + strings.TrimPrefix(f.Server.URL, "http") }

// DelayNextSubmits makes the next n POST /v2/orders create the order, then respond after d (so a
// client with a shorter timeout sees an ambiguous failure, BR-007).
func (f *Fake) DelayNextSubmits(n int, d time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.submitDelayN, f.submitDelay = n, d
}

// LoseNextSubmits makes the next n POST /v2/orders hang without creating the order.
func (f *Fake) LoseNextSubmits(n int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.lostN = n
}

// DropTradeStreams closes every trade_updates connection (stream loss).
func (f *Fake) DropTradeStreams() {
	f.mu.Lock()
	defer f.mu.Unlock()
	for c := range f.tradeConns {
		_ = c.CloseNow()
		delete(f.tradeConns, c)
	}
}

// TradeStreamConnected reports whether a trade_updates client is attached.
func (f *Fake) TradeStreamConnected() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.tradeConns) > 0
}

// Requests returns "METHOD path" for every REST request served.
func (f *Fake) Requests() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]string(nil), f.requests...)
}

// SetBarsResponse overrides GET /v2/stocks/bars.
func (f *Fake) SetBarsResponse(fn func(r *http.Request) any) {
	f.mu.Lock()
	f.barsResponse = fn
	f.mu.Unlock()
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func apiErr(w http.ResponseWriter, status int, code int, msg string) {
	writeJSON(w, status, map[string]any{"code": code, "message": msg})
}

var statusOf = map[ordersv1.OrderState]string{
	ordersv1.OrderState_ORDER_STATE_ACCEPTED: "new", ordersv1.OrderState_ORDER_STATE_PARTIALLY_FILLED: "partially_filled",
	ordersv1.OrderState_ORDER_STATE_FILLED: "filled", ordersv1.OrderState_ORDER_STATE_CANCELED: "canceled",
	ordersv1.OrderState_ORDER_STATE_REPLACED: "replaced", ordersv1.OrderState_ORDER_STATE_REJECTED: "rejected",
	ordersv1.OrderState_ORDER_STATE_EXPIRED: "expired", ordersv1.OrderState_ORDER_STATE_PENDING_CANCEL: "pending_cancel",
}

var typeOf = map[ordersv1.OrderType]string{ordersv1.OrderType_ORDER_TYPE_MARKET: "market", ordersv1.OrderType_ORDER_TYPE_LIMIT: "limit",
	ordersv1.OrderType_ORDER_TYPE_STOP: "stop", ordersv1.OrderType_ORDER_TYPE_STOP_LIMIT: "stop_limit"}
var tifOf = map[ordersv1.TimeInForce]string{ordersv1.TimeInForce_TIME_IN_FORCE_DAY: "day", ordersv1.TimeInForce_TIME_IN_FORCE_GTC: "gtc",
	ordersv1.TimeInForce_TIME_IN_FORCE_IOC: "ioc", ordersv1.TimeInForce_TIME_IN_FORCE_FOK: "fok"}
var classOf = map[brokerv1.OrderClass]string{brokerv1.OrderClass_ORDER_CLASS_SIMPLE: "simple", brokerv1.OrderClass_ORDER_CLASS_BRACKET: "bracket",
	brokerv1.OrderClass_ORDER_CLASS_OCO: "oco", brokerv1.OrderClass_ORDER_CLASS_OTO: "oto"}

func ts(n int64) any {
	if n == 0 {
		return nil
	}
	return time.Unix(0, n).UTC().Format(time.RFC3339Nano)
}

func opt(d *commonv1.Decimal) any {
	if d == nil || d.GetValue() == "" {
		return nil
	}
	return d.GetValue()
}

// OrderJSON renders a simulator order in Alpaca's shape. Status of bracket children waiting for
// their trigger is "held".
func OrderJSON(o *brokerv1.BrokerOrder) map[string]any {
	st := statusOf[o.GetState()]
	if o.GetBrokerStatus() == "held" {
		st = "held"
	}
	side := "buy"
	if o.GetSide() != commonv1.Side_SIDE_BUY {
		side = "sell"
	}
	m := map[string]any{
		"id": o.GetBrokerOrderId(), "client_order_id": o.GetClientOrderId(), "created_at": ts(o.GetCreatedAt()), "updated_at": ts(o.GetUpdatedAt()),
		"submitted_at": ts(o.GetSubmittedAt()), "filled_at": ts(o.GetFilledAt()), "canceled_at": ts(o.GetCanceledAt()),
		"asset_id": "asset-" + o.GetInstrument().GetSymbol(), "symbol": o.GetInstrument().GetSymbol(), "asset_class": "us_equity",
		"qty": opt(o.GetQty()), "filled_qty": o.GetFilledQty().GetValue(), "filled_avg_price": opt(o.GetAvgFillPrice()),
		"order_class": classOf[o.GetOrderClass()], "type": typeOf[o.GetType()], "order_type": typeOf[o.GetType()], "side": side,
		"time_in_force": tifOf[o.GetTif()], "limit_price": opt(o.GetLimitPrice()), "stop_price": opt(o.GetStopPrice()),
		"status": st, "extended_hours": o.GetExtendedHours(),
	}
	if o.GetReplacedByBrokerOrderId() != "" {
		m["replaced_by"] = o.GetReplacedByBrokerOrderId()
	}
	if o.GetReplacesBrokerOrderId() != "" {
		m["replaces"] = o.GetReplacesBrokerOrderId()
	}
	var legs []map[string]any
	for _, l := range o.GetLegs() {
		lj := OrderJSON(l)
		lj["client_order_id"] = l.GetBrokerOrderId() // Alpaca assigns its own IDs to legs
		legs = append(legs, lj)
	}
	if legs != nil {
		m["legs"] = legs
	}
	return m
}

func (f *Fake) authorized(r *http.Request) bool {
	return r.Header.Get("APCA-API-KEY-ID") == KeyID && r.Header.Get("APCA-API-SECRET-KEY") == Secret
}

func (f *Fake) rest(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	f.requests = append(f.requests, r.Method+" "+r.URL.Path)
	f.mu.Unlock()
	if !f.authorized(r) {
		apiErr(w, http.StatusUnauthorized, 40110000, "request is not authorized")
		return
	}
	ctx := r.Context()
	p := r.URL.Path
	switch {
	case r.Method == http.MethodGet && p == "/v2/account":
		snap, _ := f.Sim.Snapshot(ctx)
		writeJSON(w, 200, map[string]any{"status": "ACTIVE", "currency": "USD", "cash": snap.GetCash().GetValue(), "equity": snap.GetEquity().GetValue(),
			"buying_power": snap.GetBuyingPower().GetValue(), "initial_margin": "0", "maintenance_margin": "0", "long_market_value": "0",
			"short_market_value": "0", "trading_blocked": false, "account_blocked": false})
	case r.Method == http.MethodGet && p == "/v2/positions":
		snap, _ := f.Sim.Snapshot(ctx)
		var out []map[string]any
		for _, ps := range snap.GetPositions() {
			q, _ := decimal.NewFromString(ps.GetQty().GetValue())
			side := "long"
			if q.IsNegative() {
				side = "short"
			}
			out = append(out, map[string]any{"asset_id": "asset-" + ps.GetSymbol(), "symbol": ps.GetSymbol(), "qty": q.Abs().String(), "side": side,
				"market_value": ps.GetMarketValue().GetValue(), "avg_entry_price": ps.GetAvgCost().GetValue()})
		}
		if out == nil {
			out = []map[string]any{}
		}
		writeJSON(w, 200, out)
	case r.Method == http.MethodPost && p == "/v2/orders":
		f.submit(w, r)
	case r.Method == http.MethodGet && p == "/v2/orders":
		f.listOrders(w, r)
	case r.Method == http.MethodGet && p == "/v2/orders:by_client_order_id":
		o, err := f.Sim.GetOrder(ctx, adapter.OrderRef{ClientOrderID: r.URL.Query().Get("client_order_id")})
		if err != nil {
			apiErr(w, 404, 40410000, "order not found")
			return
		}
		writeJSON(w, 200, OrderJSON(o))
	case strings.HasPrefix(p, "/v2/orders/"):
		id := strings.TrimPrefix(p, "/v2/orders/")
		switch r.Method {
		case http.MethodGet:
			o, err := f.Sim.GetOrder(ctx, adapter.OrderRef{BrokerOrderID: id})
			if err != nil {
				apiErr(w, 404, 40410000, "order not found")
				return
			}
			writeJSON(w, 200, OrderJSON(o))
		case http.MethodDelete:
			err := f.Sim.Cancel(ctx, adapter.OrderRef{BrokerOrderID: id})
			switch {
			case errors.Is(err, adapter.ErrNotFound):
				apiErr(w, 404, 40410000, "order not found")
			case err != nil:
				apiErr(w, 422, 42210000, "order is not cancelable")
			default:
				w.WriteHeader(http.StatusNoContent)
			}
		case http.MethodPatch:
			var body map[string]string
			_ = json.NewDecoder(r.Body).Decode(&body)
			req := &brokerv1.ReplaceOrderRequest{NewClientOrderId: body["client_order_id"]}
			if v := body["qty"]; v != "" {
				req.Qty = &commonv1.Decimal{Value: v}
			}
			if v := body["limit_price"]; v != "" {
				req.LimitPrice = &commonv1.Decimal{Value: v}
			}
			if v := body["stop_price"]; v != "" {
				req.StopPrice = &commonv1.Decimal{Value: v}
			}
			n, err := f.Sim.Replace(ctx, id, req)
			switch {
			case errors.Is(err, adapter.ErrNotFound):
				apiErr(w, 404, 40410000, "order not found")
			case err != nil:
				apiErr(w, 422, 42210000, "order is not replaceable")
			default:
				writeJSON(w, 200, OrderJSON(n))
			}
		default:
			apiErr(w, 405, 0, "method not allowed")
		}
	case r.Method == http.MethodGet && p == "/v2/account/activities/FILL":
		f.activities(w, r)
	case r.Method == http.MethodGet && strings.HasPrefix(p, "/v2/assets/"):
		sym := strings.TrimPrefix(strings.TrimPrefix(p, "/v2/assets/"), "asset-")
		info, _ := f.Sim.InstrumentInfo(ctx, nil, sym)
		writeJSON(w, 200, map[string]any{"id": "asset-" + sym, "class": "us_equity", "exchange": "NASDAQ", "symbol": sym, "status": "active",
			"tradable": info.GetTradable(), "marginable": true, "shortable": true, "easy_to_borrow": true, "fractionable": true, "price_increment": "0.01"})
	case r.Method == http.MethodGet && p == "/v2/assets":
		writeJSON(w, 200, []map[string]any{{"id": "asset-AAPL", "class": "us_equity", "exchange": "NASDAQ", "symbol": "AAPL", "name": "Apple Inc.",
			"status": r.URL.Query().Get("status"), "tradable": true, "marginable": true, "shortable": true, "easy_to_borrow": true, "fractionable": true}})
	case r.Method == http.MethodGet && p == "/v2/stocks/bars":
		f.mu.Lock()
		fn := f.barsResponse
		f.mu.Unlock()
		if fn == nil {
			writeJSON(w, 200, map[string]any{"bars": map[string]any{}, "next_page_token": nil})
			return
		}
		writeJSON(w, 200, fn(r))
	default:
		apiErr(w, 404, 0, "not found: "+r.Method+" "+p)
	}
}

type submitBody struct {
	Symbol        string            `json:"symbol"`
	Qty           string            `json:"qty"`
	Side          string            `json:"side"`
	Type          string            `json:"type"`
	TimeInForce   string            `json:"time_in_force"`
	LimitPrice    string            `json:"limit_price"`
	StopPrice     string            `json:"stop_price"`
	ExtendedHours bool              `json:"extended_hours"`
	ClientOrderID string            `json:"client_order_id"`
	OrderClass    string            `json:"order_class"`
	TakeProfit    map[string]string `json:"take_profit"`
	StopLoss      map[string]string `json:"stop_loss"`
}

func rev[K comparable, V comparable](m map[K]V) map[V]K {
	out := map[V]K{}
	for k, v := range m {
		out[v] = k
	}
	return out
}

func (f *Fake) submit(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	lost := f.lostN > 0
	if lost {
		f.lostN--
	}
	f.mu.Unlock()
	if lost {
		select {
		case <-r.Context().Done():
		case <-f.done:
		}
		return
	}
	var b submitBody
	if err := json.NewDecoder(r.Body).Decode(&b); err != nil {
		apiErr(w, 422, 42210000, "invalid body")
		return
	}
	side := commonv1.Side_SIDE_BUY
	if b.Side == "sell" {
		side = commonv1.Side_SIDE_SELL
	}
	d := func(s string) *commonv1.Decimal {
		if s == "" {
			return nil
		}
		return &commonv1.Decimal{Value: s}
	}
	req := &brokerv1.BrokerOrderRequest{ClientOrderId: b.ClientOrderID, Instrument: &commonv1.Instrument{Symbol: b.Symbol}, Side: side,
		Qty: d(b.Qty), Type: rev(typeOf)[b.Type], Tif: rev(tifOf)[b.TimeInForce], LimitPrice: d(b.LimitPrice), StopPrice: d(b.StopPrice),
		ExtendedHours: b.ExtendedHours}
	if b.OrderClass != "" && b.OrderClass != "simple" {
		req.Bracket = &brokerv1.BracketSpec{OrderClass: rev(classOf)[b.OrderClass], TakeProfitLimitPrice: d(b.TakeProfit["limit_price"]),
			StopLossStopPrice: d(b.StopLoss["stop_price"]), StopLossLimitPrice: d(b.StopLoss["limit_price"])}
	}
	o, err := f.Sim.Submit(context.WithoutCancel(r.Context()), req)
	if err != nil {
		var re *adapter.RejectError
		if ok := asReject(err, &re); ok {
			msg := re.Reason.GetMessage()
			status := 403
			if re.Reason.GetCode() == brokerv1.RejectCode_REJECT_CODE_DUPLICATE_CLIENT_ORDER_ID {
				status = 422
			}
			apiErr(w, status, 40310000, msg)
			return
		}
		apiErr(w, 500, 50010000, "internal error")
		return
	}
	f.mu.Lock()
	delay := time.Duration(0)
	if f.submitDelayN > 0 {
		f.submitDelayN--
		delay = f.submitDelay
	}
	f.mu.Unlock()
	if delay > 0 {
		select {
		case <-time.After(delay):
		case <-r.Context().Done():
			return
		case <-f.done:
			return
		}
	}
	writeJSON(w, 200, OrderJSON(o))
}

func asReject(err error, target **adapter.RejectError) bool {
	return errors.As(err, target)
}

func (f *Fake) listOrders(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var os []*brokerv1.BrokerOrder
	if r.URL.Query().Get("status") == "open" {
		os, _ = f.Sim.ListOpenOrders(ctx)
	} else {
		var after time.Time
		if a := r.URL.Query().Get("after"); a != "" {
			after, _ = time.Parse(time.RFC3339Nano, a)
		}
		os, _ = f.Sim.ListOrders(ctx, time.Time{})
		filtered := os[:0]
		for _, o := range os {
			if after.IsZero() || time.Unix(0, o.GetSubmittedAt()).After(after) {
				filtered = append(filtered, o)
			}
		}
		os = filtered
	}
	out := []map[string]any{}
	for _, o := range os {
		out = append(out, OrderJSON(o))
	}
	writeJSON(w, 200, out)
}

func (f *Fake) activities(w http.ResponseWriter, r *http.Request) {
	after, _ := time.Parse(time.RFC3339Nano, r.URL.Query().Get("after"))
	execs, _ := f.Sim.ListExecutions(r.Context(), after)
	token := r.URL.Query().Get("page_token")
	out := []map[string]any{}
	skip := token != ""
	for _, e := range execs {
		id := fmt.Sprintf("%020d::%s", e.GetEventTime(), e.GetBrokerExecId())
		if skip {
			if id == token {
				skip = false
			}
			continue
		}
		cum := e.GetBrokerExecId()[strings.LastIndex(e.GetBrokerExecId(), ":")+1:]
		side := "buy"
		if e.GetSide() != commonv1.Side_SIDE_BUY {
			side = "sell"
		}
		out = append(out, map[string]any{"id": id, "activity_type": "FILL", "transaction_time": time.Unix(0, e.GetEventTime()).UTC().Format(time.RFC3339Nano),
			"type": "fill", "price": e.GetPrice().GetValue(), "qty": e.GetQty().GetValue(), "side": side, "symbol": e.GetSymbol(),
			"order_id": e.GetBrokerOrderId(), "cum_qty": cum})
		if len(out) == 100 {
			break
		}
	}
	writeJSON(w, 200, out)
}

func (f *Fake) tradeStream(w http.ResponseWriter, r *http.Request) {
	c, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	ctx := r.Context()
	_, data, err := c.Read(ctx)
	if err != nil {
		return
	}
	var auth map[string]string
	_ = json.Unmarshal(data, &auth)
	if auth["key"] != KeyID || auth["secret"] != Secret {
		_ = c.Write(ctx, websocket.MessageText, []byte(`{"stream":"authorization","data":{"status":"unauthorized","action":"authenticate"}}`))
		_ = c.Close(websocket.StatusPolicyViolation, "unauthorized")
		return
	}
	_ = c.Write(ctx, websocket.MessageText, []byte(`{"stream":"authorization","data":{"status":"authorized","action":"authenticate"}}`))
	if _, _, err = c.Read(ctx); err != nil {
		return
	}
	_ = c.Write(ctx, websocket.MessageBinary, []byte(`{"stream":"listening","data":{"streams":["trade_updates"]}}`))
	f.mu.Lock()
	f.tradeConns[c] = true
	f.mu.Unlock()
	for {
		if _, _, err := c.Read(ctx); err != nil {
			f.mu.Lock()
			delete(f.tradeConns, c)
			f.mu.Unlock()
			return
		}
	}
}

func (f *Fake) broadcast(u *brokerv1.BrokerOrderUpdate) {
	msg := map[string]any{"event": u.GetEvent(), "order": OrderJSON(u.GetOrder()), "timestamp": ts(u.GetEventTime())}
	if e := u.GetExecution(); e != nil {
		msg["execution_id"] = "raw-" + e.GetBrokerExecId()
		msg["price"] = e.GetPrice().GetValue()
		msg["qty"] = e.GetQty().GetValue()
	}
	b, _ := json.Marshal(map[string]any{"stream": "trade_updates", "data": msg})
	f.mu.Lock()
	conns := make([]*websocket.Conn, 0, len(f.tradeConns))
	for c := range f.tradeConns {
		conns = append(conns, c)
	}
	f.mu.Unlock()
	for _, c := range conns {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = c.Write(ctx, websocket.MessageBinary, b)
		cancel()
	}
}

// dataStream accepts one market-data client at a time; a second gets 406 (BR-015, V7).
func (f *Fake) dataStream(w http.ResponseWriter, r *http.Request) {
	c, err := websocket.Accept(w, r, nil)
	if err != nil {
		return
	}
	ctx := r.Context()
	_ = c.Write(ctx, websocket.MessageText, []byte(`[{"T":"success","msg":"connected"}]`))
	_, data, err := c.Read(ctx)
	if err != nil {
		return
	}
	var auth map[string]string
	_ = json.Unmarshal(data, &auth)
	if auth["key"] != KeyID || auth["secret"] != Secret {
		_ = c.Write(ctx, websocket.MessageText, []byte(`[{"T":"error","code":402,"msg":"auth failed"}]`))
		_ = c.Close(websocket.StatusPolicyViolation, "auth failed")
		return
	}
	f.mu.Lock()
	if f.dataConn != nil {
		f.mu.Unlock()
		_ = c.Write(ctx, websocket.MessageText, []byte(`[{"T":"error","code":406,"msg":"connection limit exceeded"}]`))
		_ = c.Close(websocket.StatusPolicyViolation, "connection limit exceeded")
		return
	}
	f.dataConn = c
	f.mu.Unlock()
	defer func() {
		f.mu.Lock()
		if f.dataConn == c {
			f.dataConn = nil
		}
		f.mu.Unlock()
	}()
	_ = c.Write(ctx, websocket.MessageText, []byte(`[{"T":"success","msg":"authenticated"}]`))
	for {
		_, data, err := c.Read(ctx)
		if err != nil {
			return
		}
		var sub map[string]any
		_ = json.Unmarshal(data, &sub)
		sub["T"] = "subscription"
		delete(sub, "action")
		b, _ := json.Marshal([]any{sub})
		_ = c.Write(ctx, websocket.MessageText, b)
	}
}

// PushData sends a raw market-data message array (e.g. `[{"T":"b",...}]`) to the data client.
func (f *Fake) PushData(raw string) bool {
	f.mu.Lock()
	c := f.dataConn
	f.mu.Unlock()
	if c == nil {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	return c.Write(ctx, websocket.MessageText, []byte(raw)) == nil
}

// DropDataStream closes the market-data connection.
func (f *Fake) DropDataStream() {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.dataConn != nil {
		_ = f.dataConn.CloseNow()
		f.dataConn = nil
	}
}

// DataConnected reports whether a data client is attached.
func (f *Fake) DataConnected() bool { f.mu.Lock(); defer f.mu.Unlock(); return f.dataConn != nil }
