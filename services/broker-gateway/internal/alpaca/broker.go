package alpaca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/resilience"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
)

// Resolver maps a broker symbol / asset ID to the platform instrument_id (instrument master,
// section 03). It returns "" when unknown; consumers then resolve by symbol.
type Resolver func(symbol, assetID string) string

// Broker is the Alpaca adapter.Broker for one account.
type Broker struct {
	AccountID   string
	Environment brokerv1.Environment
	Client      *Client
	Feeds       []string
	Resolve     Resolver
	// Dialer for the trade_updates WebSocket (tests point it at an httptest server).
	WSDialOptions *websocket.DialOptions
	PingInterval  time.Duration
}

// Info implements adapter.Broker.
func (b *Broker) Info() adapter.Info {
	return adapter.Info{AccountID: b.AccountID, Broker: commonv1.Broker_BROKER_ALPACA, Environment: b.Environment, Capabilities: Capabilities(b.Feeds)}
}

// ---- JSON shapes

type jsonOrder struct {
	ID             string      `json:"id"`
	ClientOrderID  string      `json:"client_order_id"`
	CreatedAt      *time.Time  `json:"created_at"`
	UpdatedAt      *time.Time  `json:"updated_at"`
	SubmittedAt    *time.Time  `json:"submitted_at"`
	FilledAt       *time.Time  `json:"filled_at"`
	CanceledAt     *time.Time  `json:"canceled_at"`
	ReplacedBy     *string     `json:"replaced_by"`
	Replaces       *string     `json:"replaces"`
	AssetID        string      `json:"asset_id"`
	Symbol         string      `json:"symbol"`
	Qty            *string     `json:"qty"`
	FilledQty      string      `json:"filled_qty"`
	FilledAvgPrice *string     `json:"filled_avg_price"`
	OrderClass     string      `json:"order_class"`
	Type           string      `json:"type"`
	Side           string      `json:"side"`
	TimeInForce    string      `json:"time_in_force"`
	LimitPrice     *string     `json:"limit_price"`
	StopPrice      *string     `json:"stop_price"`
	TrailPrice     *string     `json:"trail_price"`
	TrailPercent   *string     `json:"trail_percent"`
	Status         string      `json:"status"`
	ExtendedHours  bool        `json:"extended_hours"`
	Legs           []jsonOrder `json:"legs"`
}

type jsonActivity struct {
	ID              string    `json:"id"`
	ActivityType    string    `json:"activity_type"`
	TransactionTime time.Time `json:"transaction_time"`
	Type            string    `json:"type"`
	Price           string    `json:"price"`
	Qty             string    `json:"qty"`
	Side            string    `json:"side"`
	Symbol          string    `json:"symbol"`
	OrderID         string    `json:"order_id"`
	CumQty          string    `json:"cum_qty"`
}

type jsonAccount struct {
	Status            string `json:"status"`
	Currency          string `json:"currency"`
	Cash              string `json:"cash"`
	Equity            string `json:"equity"`
	BuyingPower       string `json:"buying_power"`
	InitialMargin     string `json:"initial_margin"`
	MaintenanceMargin string `json:"maintenance_margin"`
	LongMarketValue   string `json:"long_market_value"`
	ShortMarketValue  string `json:"short_market_value"`
	TradingBlocked    bool   `json:"trading_blocked"`
	AccountBlocked    bool   `json:"account_blocked"`
	// Intraday-margin framework (2026-06). Optional: absent on older responses; verify the field
	// names against a real paper account (opt-in contract test).
	IntradayMarginDeficit string `json:"intraday_margin_deficit"`
}

type jsonPosition struct {
	AssetID       string `json:"asset_id"`
	Symbol        string `json:"symbol"`
	Qty           string `json:"qty"`
	Side          string `json:"side"`
	MarketValue   string `json:"market_value"`
	AvgEntryPrice string `json:"avg_entry_price"`
}

type jsonAsset struct {
	ID             string `json:"id"`
	Class          string `json:"class"`
	Exchange       string `json:"exchange"`
	Symbol         string `json:"symbol"`
	Name           string `json:"name"`
	Status         string `json:"status"`
	Tradable       bool   `json:"tradable"`
	Marginable     bool   `json:"marginable"`
	Shortable      bool   `json:"shortable"`
	EasyToBorrow   bool   `json:"easy_to_borrow"`
	Fractionable   bool   `json:"fractionable"`
	PriceIncrement string `json:"price_increment"`
}

func dec(s *string) *commonv1.Decimal {
	if s == nil || *s == "" {
		return nil
	}
	return &commonv1.Decimal{Value: normDec(*s)}
}

func normDec(s string) string {
	d, err := decimal.NewFromString(s)
	if err != nil {
		return s
	}
	return d.String()
}

func ns(t *time.Time) int64 {
	if t == nil {
		return 0
	}
	return t.UnixNano()
}

func sideOf(s string) commonv1.Side {
	switch s {
	case "buy":
		return commonv1.Side_SIDE_BUY
	case "sell":
		return commonv1.Side_SIDE_SELL
	default:
		return commonv1.Side_SIDE_UNSPECIFIED
	}
}

func (b *Broker) instrument(symbol, assetID string) *commonv1.Instrument {
	inst := &commonv1.Instrument{Symbol: symbol, AlpacaAssetId: assetID}
	if b.Resolve != nil {
		inst.InstrumentId = b.Resolve(symbol, assetID)
	}
	return inst
}

func (b *Broker) toOrder(j jsonOrder) *brokerv1.BrokerOrder {
	qty := ""
	if j.Qty != nil {
		qty = *j.Qty
	}
	qd, _ := decimal.NewFromString(orZero(qty))
	fd, _ := decimal.NewFromString(orZero(j.FilledQty))
	state, _ := MapStatus(j.Status, fd, qd)
	o := &brokerv1.BrokerOrder{
		BrokerAccountId: b.AccountID, BrokerOrderId: j.ID, ClientOrderId: j.ClientOrderID,
		Instrument: b.instrument(j.Symbol, j.AssetID), Side: sideOf(j.Side), Type: orderTypesRev[j.Type],
		Tif: tifsRev[j.TimeInForce], OrderClass: orderClassesRev[j.OrderClass], Qty: dec(j.Qty),
		FilledQty: &commonv1.Decimal{Value: normDec(orZero(j.FilledQty))}, AvgFillPrice: dec(j.FilledAvgPrice),
		LimitPrice: dec(j.LimitPrice), StopPrice: dec(j.StopPrice), TrailPrice: dec(j.TrailPrice), TrailPercent: dec(j.TrailPercent),
		State: state, BrokerStatus: j.Status, ExtendedHours: j.ExtendedHours,
		CreatedAt: ns(j.CreatedAt), SubmittedAt: ns(j.SubmittedAt), UpdatedAt: ns(j.UpdatedAt), FilledAt: ns(j.FilledAt), CanceledAt: ns(j.CanceledAt),
	}
	if o.OrderClass == brokerv1.OrderClass_ORDER_CLASS_UNSPECIFIED {
		o.OrderClass = brokerv1.OrderClass_ORDER_CLASS_SIMPLE
	}
	if j.ReplacedBy != nil {
		o.ReplacedByBrokerOrderId = *j.ReplacedBy
	}
	if j.Replaces != nil {
		o.ReplacesBrokerOrderId = *j.Replaces
	}
	for _, l := range j.Legs {
		leg := b.toOrder(l)
		leg.ParentBrokerOrderId = j.ID
		// OE-009 / BR-018: legs carry the parent's attribution for ownership.
		if leg.ClientOrderId == "" || !strings.HasPrefix(leg.ClientOrderId, "tb1-") {
			leg.ClientOrderId = j.ClientOrderID
		}
		o.Legs = append(o.Legs, leg)
	}
	return o
}

func orZero(s string) string {
	if s == "" {
		return "0"
	}
	return s
}

// ---- adapter.Broker

// Connect implements adapter.Broker: a credentialed GET /v2/account.
func (b *Broker) Connect(ctx context.Context) error {
	var a jsonAccount
	err := b.Client.do(ctx, "trading", http.MethodGet, "/v2/account", nil, nil, resilience.PriorityQuery, &a)
	var ae *APIError
	if errors.As(err, &ae) && (ae.Status == http.StatusUnauthorized || ae.Status == http.StatusForbidden) {
		return fmt.Errorf("%w: HTTP %d", adapter.ErrAuth, ae.Status)
	}
	return err
}

// Snapshot implements adapter.Broker.
func (b *Broker) Snapshot(ctx context.Context) (*ledgerv1.AccountSnapshot, error) {
	var a jsonAccount
	if err := b.Client.retryable(ctx, func(ctx context.Context) error {
		return b.Client.do(ctx, "trading", http.MethodGet, "/v2/account", nil, nil, resilience.PriorityQuery, &a)
	}); err != nil {
		return nil, err
	}
	var ps []jsonPosition
	if err := b.Client.retryable(ctx, func(ctx context.Context) error {
		return b.Client.do(ctx, "trading", http.MethodGet, "/v2/positions", nil, nil, resilience.PriorityQuery, &ps)
	}); err != nil {
		return nil, err
	}
	d := func(s string) *commonv1.Decimal { return &commonv1.Decimal{Value: normDec(orZero(s))} }
	eq, _ := decimal.NewFromString(orZero(a.Equity))
	mm, _ := decimal.NewFromString(orZero(a.MaintenanceMargin))
	imd, _ := decimal.NewFromString(orZero(a.IntradayMarginDeficit))
	snap := &ledgerv1.AccountSnapshot{
		BrokerAccountId: b.AccountID, Broker: commonv1.Broker_BROKER_ALPACA,
		Cash: d(a.Cash), BuyingPower: d(a.BuyingPower), Equity: d(a.Equity), InitialMargin: d(a.InitialMargin),
		MaintenanceMargin: d(a.MaintenanceMargin), LongMarketValue: d(a.LongMarketValue), ShortMarketValue: d(a.ShortMarketValue),
		Currency: a.Currency, TradingBlocked: a.TradingBlocked, AccountBlocked: a.AccountBlocked, Status: a.Status,
		// D30: the PDT fields were removed (2026-07-06); the count is a metric only.
		DayTradeCount: -1,
		// A margin call exists by definition when equity is below maintenance margin (RM-013).
		MarginCall:            mm.IsPositive() && eq.LessThan(mm),
		IntradayMarginDeficit: imd.IsPositive(),
		AsOf:                  b.Client.now().UnixNano(),
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].Symbol < ps[j].Symbol })
	for _, p := range ps {
		q, _ := decimal.NewFromString(orZero(p.Qty))
		if p.Side == "short" && q.IsPositive() {
			q = q.Neg()
		}
		snap.Positions = append(snap.Positions, &ledgerv1.Position{
			InstrumentId: b.instrument(p.Symbol, p.AssetID).GetInstrumentId(), Symbol: p.Symbol, BrokerInstrumentId: p.AssetID,
			Qty: &commonv1.Decimal{Value: q.String()}, AvgCost: d(p.AvgEntryPrice), MarketValue: d(p.MarketValue),
		})
	}
	return snap, nil
}

type submitBody struct {
	Symbol        string            `json:"symbol"`
	Qty           string            `json:"qty"`
	Side          string            `json:"side"`
	Type          string            `json:"type"`
	TimeInForce   string            `json:"time_in_force"`
	LimitPrice    string            `json:"limit_price,omitempty"`
	StopPrice     string            `json:"stop_price,omitempty"`
	TrailPrice    string            `json:"trail_price,omitempty"`
	TrailPercent  string            `json:"trail_percent,omitempty"`
	ExtendedHours bool              `json:"extended_hours,omitempty"`
	ClientOrderID string            `json:"client_order_id"`
	OrderClass    string            `json:"order_class,omitempty"`
	TakeProfit    map[string]string `json:"take_profit,omitempty"`
	StopLoss      map[string]string `json:"stop_loss,omitempty"`
}

func rejectErr(err error) error {
	var ae *APIError
	if errors.As(err, &ae) {
		if r := MapReject(ae.Status, ae.Code, ae.Message); r != nil {
			return &adapter.RejectError{Reason: r}
		}
	}
	return err
}

// Submit implements adapter.Broker.
func (b *Broker) Submit(ctx context.Context, req *brokerv1.BrokerOrderRequest) (*brokerv1.BrokerOrder, error) {
	sym := req.GetInstrument().GetSymbol()
	if sym == "" {
		sym = req.GetInstrument().GetAlpacaAssetId()
	}
	body := submitBody{
		Symbol: sym, Qty: req.GetQty().GetValue(), Side: "buy", Type: orderTypes[req.GetType()], TimeInForce: tifs[req.GetTif()],
		LimitPrice: req.GetLimitPrice().GetValue(), StopPrice: req.GetStopPrice().GetValue(), TrailPrice: req.GetTrailPrice().GetValue(),
		TrailPercent: req.GetTrailPercent().GetValue(), ExtendedHours: req.GetExtendedHours(), ClientOrderID: req.GetClientOrderId(),
	}
	if req.GetSide() != commonv1.Side_SIDE_BUY {
		body.Side = "sell" // Alpaca infers a short sale from the position (SELL_SHORT → sell).
	}
	if br := req.GetBracket(); br != nil && br.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_UNSPECIFIED && br.GetOrderClass() != brokerv1.OrderClass_ORDER_CLASS_SIMPLE {
		body.OrderClass = orderClasses[br.GetOrderClass()]
		if v := br.GetTakeProfitLimitPrice().GetValue(); v != "" {
			body.TakeProfit = map[string]string{"limit_price": v}
		}
		if v := br.GetStopLossStopPrice().GetValue(); v != "" {
			body.StopLoss = map[string]string{"stop_price": v}
			if lv := br.GetStopLossLimitPrice().GetValue(); lv != "" {
				body.StopLoss["limit_price"] = lv
			}
		}
	}
	var j jsonOrder
	if err := b.Client.do(ctx, "trading", http.MethodPost, "/v2/orders", nil, body, resilience.PrioritySubmit, &j); err != nil {
		return nil, rejectErr(err)
	}
	return b.toOrder(j), nil
}

// Cancel implements adapter.Broker (one order; never DELETE /v2/orders, BR-020).
func (b *Broker) Cancel(ctx context.Context, ref adapter.OrderRef) error {
	if ref.BrokerOrderID == "" {
		return fmt.Errorf("alpaca: cancel needs the broker order ID")
	}
	err := b.Client.do(ctx, "trading", http.MethodDelete, "/v2/orders/"+url.PathEscape(ref.BrokerOrderID), nil, nil, resilience.PriorityCancel, nil)
	var ae *APIError
	if errors.As(err, &ae) && ae.Status == http.StatusNotFound {
		return adapter.ErrNotFound
	}
	return rejectErr(err)
}

// Replace implements adapter.Broker (PATCH /v2/orders/{id}).
func (b *Broker) Replace(ctx context.Context, brokerOrderID string, req *brokerv1.ReplaceOrderRequest) (*brokerv1.BrokerOrder, error) {
	body := map[string]string{"client_order_id": req.GetNewClientOrderId()}
	for k, v := range map[string]string{"qty": req.GetQty().GetValue(), "limit_price": req.GetLimitPrice().GetValue(),
		"stop_price": req.GetStopPrice().GetValue(), "trail": req.GetTrailPrice().GetValue(), "time_in_force": tifs[req.GetTif()]} {
		if v != "" {
			body[k] = v
		}
	}
	var j jsonOrder
	err := b.Client.do(ctx, "trading", http.MethodPatch, "/v2/orders/"+url.PathEscape(brokerOrderID), nil, body, resilience.PrioritySubmit, &j)
	var ae *APIError
	if errors.As(err, &ae) && ae.Status == http.StatusNotFound {
		return nil, adapter.ErrNotFound
	}
	if err != nil {
		return nil, rejectErr(err)
	}
	return b.toOrder(j), nil
}

// GetOrder implements adapter.Broker.
func (b *Broker) GetOrder(ctx context.Context, ref adapter.OrderRef) (*brokerv1.BrokerOrder, error) {
	var j jsonOrder
	var err error
	q := url.Values{"nested": {"true"}}
	if ref.BrokerOrderID != "" {
		err = b.Client.do(ctx, "trading", http.MethodGet, "/v2/orders/"+url.PathEscape(ref.BrokerOrderID), q, nil, resilience.PriorityQuery, &j)
	} else {
		q.Set("client_order_id", ref.ClientOrderID)
		err = b.Client.do(ctx, "trading", http.MethodGet, "/v2/orders:by_client_order_id", q, nil, resilience.PriorityQuery, &j)
	}
	var ae *APIError
	if errors.As(err, &ae) && ae.Status == http.StatusNotFound {
		return nil, adapter.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return b.toOrder(j), nil
}

func (b *Broker) listOrders(ctx context.Context, status string, since time.Time) ([]*brokerv1.BrokerOrder, error) {
	var out []*brokerv1.BrokerOrder
	after := since
	seen := map[string]bool{}
	for page := 0; page < 100; page++ {
		q := url.Values{"status": {status}, "direction": {"asc"}, "limit": {"500"}, "nested": {"true"}}
		if !after.IsZero() {
			q.Set("after", after.UTC().Format(time.RFC3339Nano))
		}
		var js []jsonOrder
		if err := b.Client.retryable(ctx, func(ctx context.Context) error {
			return b.Client.do(ctx, "trading", http.MethodGet, "/v2/orders", q, nil, resilience.PriorityQuery, &js)
		}); err != nil {
			return nil, err
		}
		added := 0
		for _, j := range js {
			if seen[j.ID] {
				continue
			}
			seen[j.ID] = true
			added++
			out = append(out, b.toOrder(j))
			if j.SubmittedAt != nil && j.SubmittedAt.After(after) {
				after = *j.SubmittedAt
			} else if j.CreatedAt != nil && j.CreatedAt.After(after) {
				after = *j.CreatedAt
			}
		}
		if len(js) < 500 || added == 0 {
			break
		}
	}
	return out, nil
}

// ListOpenOrders implements adapter.Broker.
func (b *Broker) ListOpenOrders(ctx context.Context) ([]*brokerv1.BrokerOrder, error) {
	return b.listOrders(ctx, "open", time.Time{})
}

// ListOrders implements adapter.Broker (all statuses, including manual orders, BR-019). Alpaca's
// `after` filters on submission time, so orders submitted before `since` but updated after it are
// covered by ListOpenOrders (still working) or their executions (filled).
func (b *Broker) ListOrders(ctx context.Context, since time.Time) ([]*brokerv1.BrokerOrder, error) {
	return b.listOrders(ctx, "all", since)
}

// ListExecutions implements adapter.Broker from FILL activities. The canonical exec ID is
// <order_id>:<cum_qty> (BR-008), identical to the trade_updates stream.
func (b *Broker) ListExecutions(ctx context.Context, since time.Time) ([]*ordersv1.ExecutionReport, error) {
	var out []*ordersv1.ExecutionReport
	token := ""
	for page := 0; page < 1000; page++ {
		q := url.Values{"direction": {"asc"}, "page_size": {"100"}, "after": {since.UTC().Format(time.RFC3339Nano)}}
		if token != "" {
			q.Set("page_token", token)
		}
		var acts []jsonActivity
		if err := b.Client.retryable(ctx, func(ctx context.Context) error {
			return b.Client.do(ctx, "trading", http.MethodGet, "/v2/account/activities/FILL", q, nil, resilience.PriorityQuery, &acts)
		}); err != nil {
			return nil, err
		}
		for _, a := range acts {
			out = append(out, &ordersv1.ExecutionReport{
				BrokerAccountId: b.AccountID, BrokerExecId: CanonicalExecID(a.OrderID, a.CumQty), BrokerOrderId: a.OrderID,
				InstrumentId: b.instrument(a.Symbol, "").GetInstrumentId(), Symbol: a.Symbol, Side: sideOf(a.Side),
				Qty: &commonv1.Decimal{Value: normDec(a.Qty)}, Price: &commonv1.Decimal{Value: normDec(a.Price)},
				Fees: &commonv1.Decimal{Value: "0"}, FeeCurrency: "USD", EventTime: a.TransactionTime.UnixNano(), Broker: commonv1.Broker_BROKER_ALPACA,
			})
		}
		if len(acts) < 100 {
			break
		}
		token = acts[len(acts)-1].ID
	}
	return out, nil
}

// CanonicalExecID is the Alpaca dedupe key for a fill (BR-008).
func CanonicalExecID(orderID, cumQty string) string { return orderID + ":" + normDec(cumQty) }

// InstrumentInfo implements adapter.Broker.
func (b *Broker) InstrumentInfo(ctx context.Context, inst *commonv1.Instrument, symbol string) (*brokerv1.InstrumentInfo, error) {
	key := symbol
	if inst != nil {
		key = inst.GetAlpacaAssetId()
		if key == "" {
			key = inst.GetSymbol()
		}
	}
	if key == "" {
		return nil, fmt.Errorf("alpaca: instrument info needs a symbol or asset ID")
	}
	var a jsonAsset
	err := b.Client.retryable(ctx, func(ctx context.Context) error {
		return b.Client.do(ctx, "trading", http.MethodGet, "/v2/assets/"+url.PathEscape(key), nil, nil, resilience.PriorityQuery, &a)
	})
	var ae *APIError
	if errors.As(err, &ae) && ae.Status == http.StatusNotFound {
		return nil, adapter.ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	tick := a.PriceIncrement
	if tick == "" {
		tick = "0.01"
	}
	return &brokerv1.InstrumentInfo{
		Instrument: b.instrument(a.Symbol, a.ID), Tradable: a.Tradable, Shortable: a.Shortable, EasyToBorrow: a.EasyToBorrow,
		Fractionable: a.Fractionable, Marginable: a.Marginable, MinTick: &commonv1.Decimal{Value: normDec(tick)},
		LotSize: &commonv1.Decimal{Value: "1"}, Status: a.Status, AsOf: b.Client.now().UnixNano(),
	}, nil
}

// ---- trade_updates stream

type streamMsg struct {
	Stream string          `json:"stream"`
	Data   json.RawMessage `json:"data"`
}

type tradeUpdate struct {
	Event       string     `json:"event"`
	ExecutionID string     `json:"execution_id"`
	Order       jsonOrder  `json:"order"`
	Timestamp   *time.Time `json:"timestamp"`
	Price       string     `json:"price"`
	Qty         string     `json:"qty"`
}

// Stream implements adapter.Broker: authenticate, listen to trade_updates, emit normalized updates.
func (b *Broker) Stream(ctx context.Context, onSubscribed func(), emit func(*brokerv1.BrokerOrderUpdate)) error {
	creds, err := b.Client.Creds(ctx)
	if err != nil {
		return err
	}
	dctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	c, resp, err := websocket.Dial(dctx, b.Client.Endpoints.TradeWS, b.WSDialOptions)
	cancel()
	if resp != nil && resp.Body != nil {
		_ = resp.Body.Close()
	}
	if err != nil {
		return fmt.Errorf("alpaca: trade stream dial: %s", logging.Redact(err.Error()))
	}
	defer func() { _ = c.CloseNow() }()
	c.SetReadLimit(4 << 20)
	write := func(v any) error {
		b, _ := json.Marshal(v)
		wctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		return c.Write(wctx, websocket.MessageText, b)
	}
	read := func(timeout time.Duration) (streamMsg, error) {
		rctx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		_, data, err := c.Read(rctx)
		if err != nil {
			return streamMsg{}, err
		}
		var m streamMsg
		err = json.Unmarshal(data, &m)
		return m, err
	}
	// Alpaca requires authentication within 10 s.
	if err := write(map[string]string{"action": "auth", "key": creds.KeyID, "secret": creds.Secret}); err != nil {
		return err
	}
	m, err := read(10 * time.Second)
	if err != nil {
		return fmt.Errorf("alpaca: trade stream auth: %w", err)
	}
	var auth struct {
		Status string `json:"status"`
	}
	_ = json.Unmarshal(m.Data, &auth)
	if m.Stream != "authorization" || auth.Status != "authorized" {
		return fmt.Errorf("%w: trade stream not authorized", adapter.ErrAuth)
	}
	if err := write(map[string]any{"action": "listen", "data": map[string]any{"streams": []string{"trade_updates"}}}); err != nil {
		return err
	}
	if m, err = read(10 * time.Second); err != nil || m.Stream != "listening" {
		return fmt.Errorf("alpaca: trade stream listen failed (stream %q): %w", m.Stream, err)
	}
	onSubscribed()
	ping := b.PingInterval
	if ping <= 0 {
		ping = 30 * time.Second
	}
	go func() {
		t := time.NewTicker(ping)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				pctx, cancel := context.WithTimeout(ctx, 10*time.Second)
				err := c.Ping(pctx)
				cancel()
				if err != nil {
					_ = c.CloseNow()
					return
				}
			}
		}
	}()
	for {
		_, data, err := c.Read(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			return fmt.Errorf("alpaca: trade stream read: %w", err)
		}
		var m streamMsg
		if json.Unmarshal(data, &m) != nil || m.Stream != "trade_updates" {
			continue
		}
		var tu tradeUpdate
		if err := json.Unmarshal(m.Data, &tu); err != nil {
			continue
		}
		emit(b.toUpdate(tu))
	}
}

func (b *Broker) toUpdate(tu tradeUpdate) *brokerv1.BrokerOrderUpdate {
	o := b.toOrder(tu.Order)
	u := &brokerv1.BrokerOrderUpdate{BrokerAccountId: b.AccountID, Event: tu.Event, Order: o, EventTime: ns(tu.Timestamp), ReceivedAt: b.Client.now().UnixNano()}
	if (tu.Event == "fill" || tu.Event == "partial_fill") && tu.Qty != "" {
		u.Execution = &ordersv1.ExecutionReport{
			BrokerAccountId: b.AccountID, BrokerExecId: CanonicalExecID(tu.Order.ID, tu.Order.FilledQty), BrokerOrderId: tu.Order.ID,
			ClientOrderId: tu.Order.ClientOrderID, InstrumentId: o.GetInstrument().GetInstrumentId(), Symbol: tu.Order.Symbol,
			Side: sideOf(tu.Order.Side), Qty: &commonv1.Decimal{Value: normDec(tu.Qty)}, Price: &commonv1.Decimal{Value: normDec(tu.Price)},
			Fees: &commonv1.Decimal{Value: "0"}, FeeCurrency: "USD", EventTime: ns(tu.Timestamp), Broker: commonv1.Broker_BROKER_ALPACA,
		}
	}
	return u
}
