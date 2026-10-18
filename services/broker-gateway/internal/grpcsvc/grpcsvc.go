// Package grpcsvc serves tb.broker.v1.BrokerGatewayService and BrokerMarketDataService over the
// accounts and the market-data hub of this process (ADR-0018).
package grpcsvc

import (
	"context"
	"errors"
	"io"
	"sort"
	"sync"
	"time"

	"github.com/shopspring/decimal"
	"google.golang.org/genproto/googleapis/rpc/errdetails"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/adapter"
	"github.com/illyanyc/strawberry/services/broker-gateway/internal/mdhub"
)

const svc = "/tb.broker.v1.BrokerGatewayService/"
const mdsvc = "/tb.broker.v1.BrokerMarketDataService/"

// AllowedCallers is the per-method caller allowlist (SE-040). Only execution may change orders.
var AllowedCallers = map[string][]string{
	svc + "ListAccounts":           {"execution", "ledger", "marketdata", "api-gateway"},
	svc + "GetSessionStatus":       {"execution", "ledger", "marketdata", "api-gateway"},
	svc + "GetAccountSnapshot":     {"execution", "ledger", "api-gateway"},
	svc + "StreamAccountSnapshots": {"execution", "ledger"},
	svc + "SubmitOrder":            {"execution"},
	svc + "CancelOrder":            {"execution"},
	svc + "ReplaceOrder":           {"execution"},
	svc + "GetOrder":               {"execution", "ledger"},
	svc + "ListOpenOrders":         {"execution", "ledger"},
	svc + "ListOrders":             {"execution", "ledger"},
	svc + "ListExecutions":         {"execution", "ledger"},
	svc + "StreamOrderUpdates":     {"execution", "ledger"},
	svc + "StreamExecutions":       {"execution", "ledger"},
	svc + "GetInstrumentInfo":      {"execution", "ledger", "marketdata"},
	mdsvc + "GetHistoricalBars":    {"marketdata"},
	mdsvc + "StreamMarketData":     {"marketdata"},
	mdsvc + "ListAssets":           {"marketdata"},
	mdsvc + "GetCorporateActions":  {"marketdata"},
	mdsvc + "GetFeedStatus":        {"marketdata", "api-gateway"},
	mdsvc + "GetNews":              {"context-data"},
}

// Server implements BrokerGatewayService.
type Server struct {
	brokerv1.UnimplementedBrokerGatewayServiceServer
	accounts map[string]*adapter.Account

	mu    sync.Mutex
	infos map[string]cachedInfo
	now   func() time.Time
}

type cachedInfo struct {
	info *brokerv1.InstrumentInfo
	at   time.Time
}

// New returns the server.
func New(accounts []*adapter.Account) *Server {
	m := map[string]*adapter.Account{}
	for _, a := range accounts {
		m[a.Info().AccountID] = a
	}
	return &Server{accounts: m, infos: map[string]cachedInfo{}, now: time.Now}
}

func (s *Server) account(id string) (*adapter.Account, error) {
	if id == "" {
		return nil, status.Error(codes.InvalidArgument, "broker_account_id is required")
	}
	a, ok := s.accounts[id]
	if !ok {
		return nil, status.Errorf(codes.NotFound, "unknown broker account %q", id)
	}
	return a, nil
}

// toStatus maps adapter errors to gRPC errors (BR-004).
func toStatus(err error) error {
	var ue *adapter.UnavailableError
	if errors.As(err, &ue) {
		st := status.New(codes.FailedPrecondition, "broker unavailable: session "+ue.State.String())
		if d, derr := st.WithDetails(&errdetails.ErrorInfo{Domain: "tb.broker.v1", Reason: "BROKER_UNAVAILABLE",
			Metadata: map[string]string{"session_state": ue.State.String()}}); derr == nil {
			return d.Err()
		}
		return st.Err()
	}
	if errors.Is(err, adapter.ErrNotFound) {
		return status.Error(codes.NotFound, "not found")
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return status.FromContextError(err).Err()
	}
	return status.Error(codes.Internal, logging.Redact(err.Error()))
}

// ListAccounts implements the RPC.
func (s *Server) ListAccounts(context.Context, *brokerv1.ListAccountsRequest) (*brokerv1.ListAccountsResponse, error) {
	ids := make([]string, 0, len(s.accounts))
	for id := range s.accounts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	out := &brokerv1.ListAccountsResponse{}
	for _, id := range ids {
		a := s.accounts[id]
		i := a.Info()
		out.Accounts = append(out.Accounts, &brokerv1.BrokerAccount{BrokerAccountId: id, Broker: i.Broker, Environment: i.Environment,
			Capabilities: i.Capabilities, Session: a.Session().Status()})
	}
	return out, nil
}

// GetSessionStatus implements the RPC.
func (s *Server) GetSessionStatus(_ context.Context, r *brokerv1.GetSessionStatusRequest) (*brokerv1.GetSessionStatusResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	return &brokerv1.GetSessionStatusResponse{Status: a.Session().Status()}, nil
}

// GetAccountSnapshot implements the RPC (a fresh broker pull).
func (s *Server) GetAccountSnapshot(ctx context.Context, r *brokerv1.GetAccountSnapshotRequest) (*brokerv1.GetAccountSnapshotResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	snap, err := a.Broker().Snapshot(ctx)
	if err != nil {
		return nil, toStatus(err)
	}
	snap.BrokerAccountId, snap.Broker, snap.Mode = a.Info().AccountID, a.Info().Broker, a.Info().Mode()
	return &brokerv1.GetAccountSnapshotResponse{Snapshot: snap}, nil
}

// StreamAccountSnapshots implements the RPC.
func (s *Server) StreamAccountSnapshots(r *brokerv1.StreamAccountSnapshotsRequest, ss brokerv1.BrokerGatewayService_StreamAccountSnapshotsServer) error {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return err
	}
	ch, cancel := a.Fanout().SubscribeSnapshots()
	defer cancel()
	if snap := a.LastSnapshot(); snap != nil {
		if err := ss.Send(&brokerv1.StreamAccountSnapshotsResponse{Snapshot: snap}); err != nil {
			return err
		}
	}
	for {
		select {
		case <-ss.Context().Done():
			return nil
		case snap, ok := <-ch:
			if !ok {
				return status.Error(codes.ResourceExhausted, "subscriber too slow")
			}
			if err := ss.Send(&brokerv1.StreamAccountSnapshotsResponse{Snapshot: snap}); err != nil {
				return err
			}
		}
	}
}

// instrumentInfo returns cached instrument attributes (1 h TTL) for rounding and fractional checks.
func (s *Server) instrumentInfo(ctx context.Context, a *adapter.Account, req *brokerv1.BrokerOrderRequest) (bool, decimal.Decimal) {
	key := a.Info().AccountID + "|" + req.GetInstrument().GetSymbol() + "|" + req.GetInstrument().GetAlpacaAssetId() + "|" + req.GetInstrument().GetIbkrConId()
	s.mu.Lock()
	c, ok := s.infos[key]
	s.mu.Unlock()
	if !ok || s.now().Sub(c.at) > time.Hour {
		ictx, cancel := context.WithTimeout(ctx, 3*time.Second)
		info, err := a.Broker().InstrumentInfo(ictx, req.GetInstrument(), "")
		cancel()
		if err != nil {
			// Unknown attributes: be conservative (no fractional), default tick.
			return false, decimal.Zero
		}
		c = cachedInfo{info: info, at: s.now()}
		s.mu.Lock()
		s.infos[key] = c
		s.mu.Unlock()
	}
	tick, _, _ := adapter.Dec(c.info.GetMinTick())
	return c.info.GetFractionable(), tick
}

// SubmitOrder implements the RPC.
func (s *Server) SubmitOrder(ctx context.Context, r *brokerv1.SubmitOrderRequest) (*brokerv1.SubmitOrderResponse, error) {
	if r.GetOrder() == nil {
		return nil, status.Error(codes.InvalidArgument, "order is required")
	}
	a, err := s.account(r.GetOrder().GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	if !a.Session().CanSubmit() {
		return nil, toStatus(&adapter.UnavailableError{State: a.Session().State()})
	}
	fractionable, tick := s.instrumentInfo(ctx, a, r.GetOrder())
	resp, err := a.Submit(ctx, r.GetOrder(), fractionable, tick)
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

func ref(clientID, brokerID string) (adapter.OrderRef, error) {
	if (clientID == "") == (brokerID == "") {
		return adapter.OrderRef{}, status.Error(codes.InvalidArgument, "exactly one of client_order_id / broker_order_id is required")
	}
	return adapter.OrderRef{ClientOrderID: clientID, BrokerOrderID: brokerID}, nil
}

// CancelOrder implements the RPC.
func (s *Server) CancelOrder(ctx context.Context, r *brokerv1.CancelOrderRequest) (*brokerv1.CancelOrderResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	rf, err := ref(r.GetClientOrderId(), r.GetBrokerOrderId())
	if err != nil {
		return nil, err
	}
	resp, err := a.Cancel(ctx, rf)
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

// ReplaceOrder implements the RPC.
func (s *Server) ReplaceOrder(ctx context.Context, r *brokerv1.ReplaceOrderRequest) (*brokerv1.ReplaceOrderResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	resp, err := a.Replace(ctx, r, decimal.Zero)
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

// GetOrder implements the RPC.
func (s *Server) GetOrder(ctx context.Context, r *brokerv1.GetOrderRequest) (*brokerv1.GetOrderResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	rf, err := ref(r.GetClientOrderId(), r.GetBrokerOrderId())
	if err != nil {
		return nil, err
	}
	o, err := a.GetOrder(ctx, rf)
	if errors.Is(err, adapter.ErrNotFound) {
		return &brokerv1.GetOrderResponse{Found: false}, nil
	}
	if err != nil {
		return nil, toStatus(err)
	}
	return &brokerv1.GetOrderResponse{Found: true, Order: o}, nil
}

// ListOpenOrders implements the RPC.
func (s *Server) ListOpenOrders(ctx context.Context, r *brokerv1.ListOpenOrdersRequest) (*brokerv1.ListOpenOrdersResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	os, err := a.Broker().ListOpenOrders(ctx)
	if err != nil {
		return nil, toStatus(err)
	}
	return &brokerv1.ListOpenOrdersResponse{Orders: os}, nil
}

// ListOrders implements the RPC.
func (s *Server) ListOrders(ctx context.Context, r *brokerv1.ListOrdersRequest) (*brokerv1.ListOrdersResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	os, err := a.Broker().ListOrders(ctx, time.Unix(0, r.GetSince()))
	if err != nil {
		return nil, toStatus(err)
	}
	return &brokerv1.ListOrdersResponse{Orders: os}, nil
}

// ListExecutions implements the RPC.
func (s *Server) ListExecutions(ctx context.Context, r *brokerv1.ListExecutionsRequest) (*brokerv1.ListExecutionsResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	es, err := a.Broker().ListExecutions(ctx, time.Unix(0, r.GetSince()))
	if err != nil {
		return nil, toStatus(err)
	}
	for _, e := range es {
		e.Mode = a.Info().Mode()
	}
	return &brokerv1.ListExecutionsResponse{Executions: es}, nil
}

// StreamOrderUpdates implements the RPC.
func (s *Server) StreamOrderUpdates(r *brokerv1.StreamOrderUpdatesRequest, ss brokerv1.BrokerGatewayService_StreamOrderUpdatesServer) error {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return err
	}
	ch, cancel := a.Fanout().SubscribeOrders()
	defer cancel()
	for {
		select {
		case <-ss.Context().Done():
			return nil
		case u, ok := <-ch:
			if !ok {
				return status.Error(codes.ResourceExhausted, "subscriber too slow; resubscribe and resync")
			}
			if err := ss.Send(&brokerv1.StreamOrderUpdatesResponse{Update: u}); err != nil {
				return err
			}
		}
	}
}

// StreamExecutions implements the RPC (deduplicated: only new executions are fanned out, BR-008).
func (s *Server) StreamExecutions(r *brokerv1.StreamExecutionsRequest, ss brokerv1.BrokerGatewayService_StreamExecutionsServer) error {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return err
	}
	ch, cancel := a.Fanout().SubscribeExecutions()
	defer cancel()
	for {
		select {
		case <-ss.Context().Done():
			return nil
		case e, ok := <-ch:
			if !ok {
				return status.Error(codes.ResourceExhausted, "subscriber too slow; resubscribe and use ListExecutions")
			}
			if err := ss.Send(&brokerv1.StreamExecutionsResponse{Execution: e}); err != nil {
				return err
			}
		}
	}
}

// GetInstrumentInfo implements the RPC.
func (s *Server) GetInstrumentInfo(ctx context.Context, r *brokerv1.GetInstrumentInfoRequest) (*brokerv1.GetInstrumentInfoResponse, error) {
	a, err := s.account(r.GetBrokerAccountId())
	if err != nil {
		return nil, err
	}
	info, err := a.Broker().InstrumentInfo(ctx, r.GetInstrument(), r.GetSymbol())
	if err != nil {
		return nil, toStatus(err)
	}
	return &brokerv1.GetInstrumentInfoResponse{Info: info}, nil
}

// MarketData is the REST side of a vendor's market data (alpaca.Client).
type MarketData interface {
	HistoricalBars(ctx context.Context, req *brokerv1.GetHistoricalBarsRequest) (*brokerv1.GetHistoricalBarsResponse, error)
	Assets(ctx context.Context, includeInactive bool) (*brokerv1.ListAssetsResponse, error)
	CorporateActions(ctx context.Context, req *brokerv1.GetCorporateActionsRequest) (*brokerv1.GetCorporateActionsResponse, error)
	News(ctx context.Context, req *brokerv1.GetNewsRequest) (*brokerv1.GetNewsResponse, error)
}

// MarketDataServer implements BrokerMarketDataService.
type MarketDataServer struct {
	brokerv1.UnimplementedBrokerMarketDataServiceServer
	REST MarketData
	Hub  *mdhub.Hub
}

// GetHistoricalBars implements the RPC.
func (m *MarketDataServer) GetHistoricalBars(ctx context.Context, r *brokerv1.GetHistoricalBarsRequest) (*brokerv1.GetHistoricalBarsResponse, error) {
	if m.REST == nil {
		return nil, status.Error(codes.Unimplemented, "no market-data account configured")
	}
	resp, err := m.REST.HistoricalBars(ctx, r)
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

// ListAssets implements the RPC.
func (m *MarketDataServer) ListAssets(ctx context.Context, r *brokerv1.ListAssetsRequest) (*brokerv1.ListAssetsResponse, error) {
	if m.REST == nil {
		return nil, status.Error(codes.Unimplemented, "no market-data account configured")
	}
	resp, err := m.REST.Assets(ctx, r.GetIncludeInactive())
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

// GetCorporateActions implements the RPC.
func (m *MarketDataServer) GetCorporateActions(ctx context.Context, r *brokerv1.GetCorporateActionsRequest) (*brokerv1.GetCorporateActionsResponse, error) {
	if m.REST == nil {
		return nil, status.Error(codes.Unimplemented, "no market-data account configured")
	}
	resp, err := m.REST.CorporateActions(ctx, r)
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

// GetNews implements the RPC.
func (m *MarketDataServer) GetNews(ctx context.Context, r *brokerv1.GetNewsRequest) (*brokerv1.GetNewsResponse, error) {
	if m.REST == nil {
		return nil, status.Error(codes.Unimplemented, "no market-data account configured")
	}
	resp, err := m.REST.News(ctx, r)
	if err != nil {
		return nil, toStatus(err)
	}
	return resp, nil
}

// GetFeedStatus implements the RPC.
func (m *MarketDataServer) GetFeedStatus(context.Context, *brokerv1.GetFeedStatusRequest) (*brokerv1.GetFeedStatusResponse, error) {
	if m.Hub == nil {
		return &brokerv1.GetFeedStatusResponse{}, nil
	}
	return &brokerv1.GetFeedStatusResponse{Feeds: m.Hub.Statuses()}, nil
}

// StreamMarketData implements the bidirectional RPC over the shared upstream (BR-015).
func (m *MarketDataServer) StreamMarketData(ss brokerv1.BrokerMarketDataService_StreamMarketDataServer) error {
	if m.Hub == nil {
		return status.Error(codes.Unimplemented, "no market-data account configured")
	}
	first, err := ss.Recv()
	if err != nil {
		return err
	}
	if first.GetFeed() == "" {
		return status.Error(codes.InvalidArgument, "feed is required on the first message")
	}
	c, err := m.Hub.Register(first.GetFeed(), 0)
	if err != nil {
		return status.Error(codes.InvalidArgument, err.Error())
	}
	defer c.Close()
	c.Update(first.GetSubscribe(), first.GetUnsubscribe())
	recvErr := make(chan error, 1)
	go func() {
		for {
			req, err := ss.Recv()
			if err != nil {
				recvErr <- err
				return
			}
			c.Update(req.GetSubscribe(), req.GetUnsubscribe())
		}
	}()
	for {
		select {
		case err := <-recvErr:
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		case ev, ok := <-c.C:
			if !ok {
				if e := c.Err(); e != nil {
					return status.Error(codes.ResourceExhausted, e.Error())
				}
				return nil
			}
			if err := ss.Send(ev); err != nil {
				return err
			}
		}
	}
}
