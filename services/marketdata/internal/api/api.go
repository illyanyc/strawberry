// Package api serves tb.marketdata.v1.MarketDataService (section 05, ADR-0022).
package api

import (
	"context"
	"errors"
	"strings"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/services/marketdata/internal/ingest"
	"github.com/illyanyc/strawberry/services/marketdata/internal/live"
)

const svc = "/tb.marketdata.v1.MarketDataService/"

// AllowedCallers is the per-method caller allowlist (SE-040).
var AllowedCallers = map[string][]string{
	svc + "Backfill":          {"api-gateway"},
	svc + "GetBackfillStatus": {"api-gateway"},
	svc + "SetUniverse":       {"api-gateway"},
	svc + "GetFeedStatus":     {"api-gateway", "execution"},
	svc + "IsFresh":           {"execution", "api-gateway"},
	svc + "GetBars":           {"api-gateway"},
	svc + "ResolveSymbols":    {"api-gateway"},
}

// Symbols resolves current tickers to instrument IDs.
type Symbols interface {
	FromTickers(ctx context.Context, tickers []string, at time.Time) (ids []string, unknown []string)
}

// BarCache reads recent final bars (the Valkey ring, ST-009).
type BarCache interface {
	Ring(ctx context.Context, tf, instrumentID string, n int) ([]*mdv1.Bar, error)
}

// Universe resolves a named universe to instrument IDs and their current symbols.
type Universe interface {
	Members(ctx context.Context, name string, at time.Time) (map[string]string, error) // symbol -> instrument_id
	Set(ctx context.Context, name string, ids []string, at time.Time) (map[string]string, error)
}

// Server implements MarketDataService.
type Server struct {
	mdv1.UnimplementedMarketDataServiceServer
	Backfiller *ingest.Backfiller
	Store      ingest.Store
	Pipe       *live.Pipeline
	Streamer   *live.Streamer
	Universe   Universe
	Feed       string
	Now        func() time.Time
	Bars       BarCache
	Symbols    Symbols
}

// ResolveSymbols maps current tickers to instrument IDs, exact matches only (UI-011).
func (s *Server) ResolveSymbols(ctx context.Context, r *mdv1.ResolveSymbolsRequest) (*mdv1.ResolveSymbolsResponse, error) {
	if len(r.GetSymbols()) == 0 || len(r.GetSymbols()) > 100 {
		return nil, status.Error(codes.InvalidArgument, "1-100 symbols are required")
	}
	if s.Symbols == nil {
		return nil, status.Error(codes.Unavailable, "instrument master not configured")
	}
	now := s.Now()
	out := &mdv1.ResolveSymbolsResponse{}
	for _, sym := range r.GetSymbols() {
		sym = strings.ToUpper(strings.TrimSpace(sym))
		if ids, _ := s.Symbols.FromTickers(ctx, []string{sym}, now); len(ids) == 1 {
			out.Matches = append(out.Matches, &mdv1.SymbolMatch{Symbol: sym, InstrumentId: ids[0]})
			continue
		}
		out.Unknown = append(out.Unknown, sym)
	}
	return out, nil
}

var tfNames = map[mdv1.Timeframe]string{mdv1.Timeframe_TIMEFRAME_1M: "1m", mdv1.Timeframe_TIMEFRAME_5M: "5m", mdv1.Timeframe_TIMEFRAME_15M: "15m",
	mdv1.Timeframe_TIMEFRAME_30M: "30m", mdv1.Timeframe_TIMEFRAME_1H: "1h", mdv1.Timeframe_TIMEFRAME_1D: "1d"}

// GetBars serves recent final bars for charts from the hot-cache ring, oldest first (UI-010).
func (s *Server) GetBars(ctx context.Context, r *mdv1.GetBarsRequest) (*mdv1.GetBarsResponse, error) {
	tf, ok := tfNames[r.GetTimeframe()]
	if r.GetInstrumentId() == "" || !ok {
		return nil, status.Error(codes.InvalidArgument, "instrument_id and a timeframe are required")
	}
	if s.Bars == nil {
		return nil, status.Error(codes.Unavailable, "bar cache not configured")
	}
	n := int(r.GetLimit())
	if n <= 0 || n > 500 {
		n = 500
	}
	bars, err := s.Bars.Ring(ctx, tf, r.GetInstrumentId(), n)
	if err != nil {
		return nil, status.Error(codes.Unavailable, "bar cache unavailable")
	}
	out := &mdv1.GetBarsResponse{Source: "cache"}
	for _, b := range bars {
		if (r.GetStart() == 0 || b.GetTsOpen() >= r.GetStart()) && (r.GetEnd() == 0 || b.GetTsOpen() <= r.GetEnd()) {
			out.Bars = append(out.Bars, b)
		}
	}
	return out, nil
}

// Backfill implements the RPC.
func (s *Server) Backfill(ctx context.Context, r *mdv1.BackfillRequest) (*mdv1.BackfillResponse, error) {
	ids := r.GetInstrumentIds()
	if len(ids) == 0 && r.GetUniverse() != "" {
		m, err := s.Universe.Members(ctx, r.GetUniverse(), time.Unix(0, r.GetEnd()))
		if err != nil {
			return nil, status.Error(codes.InvalidArgument, err.Error())
		}
		for _, id := range m {
			ids = append(ids, id)
		}
	}
	src := r.GetSource()
	if src == "" {
		src = s.Feed
	}
	id, err := s.Backfiller.Submit(ctx, &ingest.Job{Source: src, Timeframes: r.GetTimeframes(), Instruments: ids,
		Start: time.Unix(0, r.GetStart()).UTC(), End: time.Unix(0, r.GetEnd()).UTC()})
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	return &mdv1.BackfillResponse{JobId: id}, nil
}

// GetBackfillStatus implements the RPC.
func (s *Server) GetBackfillStatus(ctx context.Context, r *mdv1.GetBackfillStatusRequest) (*mdv1.GetBackfillStatusResponse, error) {
	j, err := s.Store.LoadJob(ctx, r.GetJobId())
	if err != nil {
		return nil, status.Error(codes.NotFound, err.Error())
	}
	resp := &mdv1.GetBackfillStatusResponse{JobId: j.ID, State: j.State, Report: j.Report, Error: j.Err}
	if !j.StartedAt.IsZero() {
		resp.StartedAt = j.StartedAt.UnixNano()
	}
	if !j.FinishedAt.IsZero() {
		resp.FinishedAt = j.FinishedAt.UnixNano()
	}
	return resp, nil
}

// SetUniverse implements the RPC (MD-009: no restart).
func (s *Server) SetUniverse(ctx context.Context, r *mdv1.SetUniverseRequest) (*mdv1.SetUniverseResponse, error) {
	if s.Streamer == nil {
		return nil, status.Error(codes.FailedPrecondition, "streaming disabled")
	}
	m, err := s.Universe.Set(ctx, r.GetUniverse(), r.GetInstrumentIds(), s.Now())
	if err != nil {
		return nil, status.Error(codes.InvalidArgument, err.Error())
	}
	if err := s.Streamer.SetUniverse(m); err != nil {
		return nil, status.Error(codes.Unavailable, err.Error())
	}
	return &mdv1.SetUniverseResponse{Subscribed: int32(len(m))}, nil //nolint:gosec // universe size is small
}

// GetFeedStatus implements the RPC.
func (s *Server) GetFeedStatus(context.Context, *mdv1.GetFeedStatusRequest) (*mdv1.GetFeedStatusResponse, error) {
	if s.Pipe == nil {
		return &mdv1.GetFeedStatusResponse{}, nil
	}
	now := s.Now()
	last := s.Pipe.LastMessageAt()
	fs := &mdv1.FeedStatus{Feed: s.Feed, Connected: s.Pipe.Connected(), LastMessageAt: last, SymbolsStale: s.Pipe.StaleSymbols(2 * time.Minute)}
	if last > 0 {
		fs.LagMs = now.Sub(time.Unix(0, last)).Milliseconds()
	}
	fs.State = "disconnected"
	if fs.Connected {
		fs.State = "connected"
	}
	return &mdv1.GetFeedStatusResponse{Feeds: []*mdv1.FeedStatus{fs}}, nil
}

// IsFresh implements the RPC (MD-007): fails closed.
func (s *Server) IsFresh(_ context.Context, r *mdv1.IsFreshRequest) (*mdv1.IsFreshResponse, error) {
	if r.GetInstrumentId() == "" {
		return nil, status.Error(codes.InvalidArgument, "instrument_id is required")
	}
	if s.Pipe == nil {
		return &mdv1.IsFreshResponse{Fresh: false, Reason: "streaming disabled"}, nil
	}
	tf := r.GetTimeframe()
	if tf == mdv1.Timeframe_TIMEFRAME_UNSPECIFIED {
		tf = mdv1.Timeframe_TIMEFRAME_1M
	}
	ok, last, why := s.Pipe.IsFresh(r.GetInstrumentId(), tf, time.Duration(r.GetMaxAge()))
	return &mdv1.IsFreshResponse{Fresh: ok, LastBarClose: last, Reason: why}, nil
}

// ErrNoUniverse is returned for an unknown universe.
var ErrNoUniverse = errors.New("unknown universe")
