// Package cache is the hot cache (Valkey 9, D6) key layout and client shared by Go services
// (section 03, rule ST-009; keys documented in contracts/cache/README.md). Values are protobuf
// binary messages from contracts/proto.
package cache

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
	"google.golang.org/protobuf/proto"

	featuresv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/features/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/secrets"
)

// Key builders (ST-009).
func BarLastKey(tf, instrumentID string) string { return "bar:last:" + tf + ":" + instrumentID }
func BarRingKey(tf, instrumentID string) string { return "bar:ring:" + tf + ":" + instrumentID }
func QuoteLastKey(instrumentID string) string   { return "quote:last:" + instrumentID }
func FeatureKey(featureSet, instrumentID string) string {
	return "feat:" + featureSet + ":" + instrumentID
}
func WatermarkKey(tf string) string { return "md:watermark:" + tf }

// TTLs per key class (ST-009). Bars outlive a long weekend so the UI can always draw the last
// session; staleness is judged from the bar's available_at, never from key presence.
const (
	BarTTL     = 7 * 24 * time.Hour
	QuoteTTL   = time.Hour
	FeatureTTL = 24 * time.Hour
	RingLen    = 500
)

// Config locates Valkey; the password comes from secret valkey_password (user "tb").
type Config struct {
	Addr string `env:"TB_VALKEY_ADDR" envDefault:"valkey:6379" json:"valkey_addr"`
	User string `env:"TB_VALKEY_USER" envDefault:"tb" json:"valkey_user"`
}

// Client wraps a Redis-protocol client.
type Client struct {
	R redis.UniversalClient
}

// Connect opens a client with the password from secrets.
func Connect(c Config, store *secrets.Store) (*Client, error) {
	pw, err := store.GetString("valkey_password")
	if err != nil {
		return nil, err
	}
	return &Client{R: redis.NewClient(&redis.Options{Addr: c.Addr, Username: c.User, Password: pw, DialTimeout: 3 * time.Second,
		ReadTimeout: time.Second, WriteTimeout: time.Second, PoolSize: 16})}, nil
}

// Ready is an ops readiness check.
func (c *Client) Ready(ctx context.Context) error { return c.R.Ping(ctx).Err() }

var ringScript = redis.NewScript(`
local head = redis.call('LINDEX', KEYS[2], 0)
if head and ARGV[2] == '1' then
  redis.call('LSET', KEYS[2], 0, ARGV[1])
else
  redis.call('LPUSH', KEYS[2], ARGV[1])
  redis.call('LTRIM', KEYS[2], 0, tonumber(ARGV[3]) - 1)
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[4])
redis.call('PEXPIRE', KEYS[2], ARGV[4])
return 1`)

// PutBar sets bar:last and pushes onto bar:ring (newest first). A correction of the newest bar
// (same ts_open) replaces the ring head instead of pushing a duplicate.
func (c *Client) PutBar(ctx context.Context, tf string, b *mdv1.Bar) error {
	v, err := proto.Marshal(b)
	if err != nil {
		return err
	}
	replace := "0"
	if last, err := c.LastBar(ctx, tf, b.GetInstrumentId()); err == nil && last.GetTsOpen() == b.GetTsOpen() {
		replace = "1"
	} else if err == nil && last.GetTsOpen() > b.GetTsOpen() {
		return nil // older than what we have: never move bar:last backwards
	}
	return ringScript.Run(ctx, c.R, []string{BarLastKey(tf, b.GetInstrumentId()), BarRingKey(tf, b.GetInstrumentId())},
		v, replace, RingLen, BarTTL.Milliseconds()).Err()
}

// ErrMiss is returned on a cache miss.
var ErrMiss = errors.New("cache: miss")

// LastBar reads bar:last.
func (c *Client) LastBar(ctx context.Context, tf, instrumentID string) (*mdv1.Bar, error) {
	v, err := c.R.Get(ctx, BarLastKey(tf, instrumentID)).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, ErrMiss
	}
	if err != nil {
		return nil, err
	}
	b := &mdv1.Bar{}
	return b, proto.Unmarshal(v, b)
}

// Ring returns up to n most recent bars, oldest first.
func (c *Client) Ring(ctx context.Context, tf, instrumentID string, n int) ([]*mdv1.Bar, error) {
	vs, err := c.R.LRange(ctx, BarRingKey(tf, instrumentID), 0, int64(n-1)).Result()
	if err != nil {
		return nil, err
	}
	out := make([]*mdv1.Bar, len(vs))
	for i, v := range vs {
		b := &mdv1.Bar{}
		if err := proto.Unmarshal([]byte(v), b); err != nil {
			return nil, err
		}
		out[len(vs)-1-i] = b
	}
	return out, nil
}

// PutQuote sets quote:last.
func (c *Client) PutQuote(ctx context.Context, q *mdv1.Quote) error {
	v, err := proto.Marshal(q)
	if err != nil {
		return err
	}
	return c.R.Set(ctx, QuoteLastKey(q.GetInstrumentId()), v, QuoteTTL).Err()
}

// PutFeatures sets feat:{feature_set}:{instrument_id}.
func (c *Client) PutFeatures(ctx context.Context, fv *featuresv1.FeatureVector) error {
	v, err := proto.Marshal(fv)
	if err != nil {
		return err
	}
	return c.R.Set(ctx, FeatureKey(fv.GetFeatureSetId(), fv.GetInstrumentId()), v, FeatureTTL).Err()
}

// SetWatermark records the latest final bar ts_open per feed for a timeframe (hash field = feed).
func (c *Client) SetWatermark(ctx context.Context, tf, feed string, tsOpen int64) error {
	return c.R.HSet(ctx, WatermarkKey(tf), feed, strconv.FormatInt(tsOpen, 10)).Err()
}

// Watermark reads the watermark of feed.
func (c *Client) Watermark(ctx context.Context, tf, feed string) (int64, error) {
	s, err := c.R.HGet(ctx, WatermarkKey(tf), feed).Result()
	if errors.Is(err, redis.Nil) {
		return 0, ErrMiss
	}
	if err != nil {
		return 0, err
	}
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("cache: bad watermark: %w", err)
	}
	return v, nil
}
