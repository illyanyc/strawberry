// Package ingest turns vendor bars into lake-ready bars and runs backfill jobs (section 05,
// ADR-0022, rules MD-001..MD-006, MD-010).
package ingest

import (
	"context"
	"fmt"
	"strings"
	"time"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
)

// TimeframeDur is the nominal bar width.
var TimeframeDur = map[mdv1.Timeframe]time.Duration{
	mdv1.Timeframe_TIMEFRAME_1M: time.Minute, mdv1.Timeframe_TIMEFRAME_5M: 5 * time.Minute, mdv1.Timeframe_TIMEFRAME_15M: 15 * time.Minute,
	mdv1.Timeframe_TIMEFRAME_30M: 30 * time.Minute, mdv1.Timeframe_TIMEFRAME_1H: time.Hour, mdv1.Timeframe_TIMEFRAME_1D: 24 * time.Hour,
}

// Delays maps "<feed>" or "<feed>/<tf>" to the publication delay added to ts_close for historical
// bars (MD-003). It must be at least the observed live delay (bar finality window), so backtests are
// never more optimistic than live.
type Delays map[string]time.Duration

// DefaultDelays: Alpaca bars are final 60 s after their close (updatedBars window, master
// conventions); FMP EOD is published hours after the close.
var DefaultDelays = Delays{"alpaca.sip": 60 * time.Second, "alpaca.iex": 60 * time.Second, "fmp": 6 * time.Hour, "firstrate": 60 * time.Second}

// For returns the delay for a feed and timeframe (most specific first, default 60 s).
func (d Delays) For(feed string, tf mdv1.Timeframe) time.Duration {
	if v, ok := d[feed+"/"+tf.String()]; ok {
		return v
	}
	if v, ok := d[feed]; ok {
		return v
	}
	return 60 * time.Second
}

// ParseDelays parses "alpaca.sip=60s,fmp=6h".
func ParseDelays(s string) (Delays, error) {
	out := Delays{}
	for k, v := range DefaultDelays {
		out[k] = v
	}
	for _, kv := range strings.Split(s, ",") {
		kv = strings.TrimSpace(kv)
		if kv == "" {
			continue
		}
		k, v, ok := strings.Cut(kv, "=")
		if !ok {
			return nil, fmt.Errorf("bad delay %q", kv)
		}
		dur, err := time.ParseDuration(v)
		if err != nil {
			return nil, err
		}
		out[k] = dur
	}
	return out, nil
}

// Resolver maps a ticker at a time to the instrument_id (instrument master, DF-006).
type Resolver interface {
	Resolve(ctx context.Context, symbol string, at int64) (string, error)
}

// Normalizer prepares vendor bars (symbol-keyed) for the lake.
type Normalizer struct {
	Cal    *calendar.Calendar
	Ref    Resolver
	Delays Delays
	Now    func() time.Time
}

// Normalize maps symbol → instrument_id as of each bar, aligns daily bars to the session (MD-004),
// tags the session part, and sets provenance (MD-003): available_at = ts_close + delay for
// historical data, ingested_at = now. Bars outside any session or with unknown symbols are dropped
// and counted.
func (n Normalizer) Normalize(ctx context.Context, bars []*mdv1.Bar, historical bool) (out []*mdv1.Bar, unknown, offSession int) {
	now := n.Now().UnixNano()
	for _, b := range bars {
		tf := b.GetTimeframe()
		open := time.Unix(0, b.GetTsOpen()).UTC()
		var s calendar.Session
		var part calendar.Part
		var ok bool
		if tf == mdv1.Timeframe_TIMEFRAME_1D {
			// Vendor daily bars are stamped at 00:00 New York; re-align to the regular session.
			date := open.In(nyLoc).Format("2006-01-02")
			s, ok = n.Cal.Session(date)
			part = calendar.PartRegular
			if ok {
				b.TsOpen, b.TsClose = s.Open.UnixNano(), s.Close.UnixNano()
			}
		} else {
			s, part, ok = n.Cal.SessionAt(open)
			if ok {
				b.TsClose = s.BarClose(open, TimeframeDur[tf]).UnixNano()
			}
		}
		if !ok {
			offSession++
			continue
		}
		if b.GetInstrumentId() == "" {
			id, err := n.Ref.Resolve(ctx, b.GetSymbol(), b.GetTsOpen())
			if err != nil {
				unknown++
				continue
			}
			b.InstrumentId = id
		}
		b.Session = map[calendar.Part]mdv1.Session{calendar.PartPre: mdv1.Session_SESSION_PRE, calendar.PartRegular: mdv1.Session_SESSION_REGULAR,
			calendar.PartPost: mdv1.Session_SESSION_POST}[part]
		b.AdjustmentBasis = mdv1.AdjustmentBasis_ADJUSTMENT_BASIS_RAW
		b.IsFinal = true
		if historical || b.GetAvailableAt() == 0 {
			b.AvailableAt = b.GetTsClose() + n.Delays.For(b.GetFeed(), tf).Nanoseconds()
		}
		if b.GetAvailableAt() < b.GetTsClose() {
			b.AvailableAt = b.GetTsClose()
		}
		b.IngestedAt = now
		out = append(out, b)
	}
	return out, unknown, offSession
}

var nyLoc = func() *time.Location {
	l, err := time.LoadLocation("America/New_York")
	if err != nil {
		panic(err)
	}
	return l
}()

// Source fetches historical bars for symbols (all pages).
type Source interface {
	Feed() string
	MaxSymbols() int
	Bars(ctx context.Context, symbols []string, tf mdv1.Timeframe, start, end time.Time) ([]*mdv1.Bar, error)
}

// BrokerSource reads Alpaca bars through broker-gateway's BrokerMarketDataService (credentials stay
// in broker-gateway, SE-031).
type BrokerSource struct {
	Client  brokerv1.BrokerMarketDataServiceClient
	FeedStr string
}

// Feed implements Source.
func (s BrokerSource) Feed() string { return s.FeedStr }

// MaxSymbols implements Source.
func (s BrokerSource) MaxSymbols() int { return 100 }

// Bars implements Source.
func (s BrokerSource) Bars(ctx context.Context, symbols []string, tf mdv1.Timeframe, start, end time.Time) ([]*mdv1.Bar, error) {
	var out []*mdv1.Bar
	token := ""
	for page := 0; page < 10000; page++ {
		resp, err := s.Client.GetHistoricalBars(ctx, &brokerv1.GetHistoricalBarsRequest{Symbols: symbols, Timeframe: tf,
			Start: start.UnixNano(), End: end.UnixNano(), Feed: s.FeedStr, PageToken: token, Limit: 10000})
		if err != nil {
			return nil, err
		}
		out = append(out, resp.GetBars()...)
		if resp.GetNextPageToken() == "" {
			return out, nil
		}
		token = resp.GetNextPageToken()
	}
	return out, fmt.Errorf("ingest: too many pages")
}
