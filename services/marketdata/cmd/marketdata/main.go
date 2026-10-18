// Command marketdata owns market-data ingestion (section 05) and is the single writer of the lake and
// the reference-data schema (section 03, D4): historical backfill, live streaming through
// broker-gateway's shared socket, bar finality and resampling, gap repair, cache and bus
// publication, daily reference sync, and nightly cross-source reconciliation.
//
// Subcommands:
//
//	marketdata                       run the service
//	marketdata spike v2|v3|v4 [...]  verify-early spikes (docs/verify-early.md)
//	marketdata lake verify           recompute lake checksums against the manifest (ST-003)
//	marketdata healthcheck           container liveness probe
package main

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"log/slog"
	"maps"
	"net"
	"os"
	"slices"
	"strings"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/oklog/ulid/v2"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/cache"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/svc"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/marketdata/internal/api"
	"github.com/illyanyc/strawberry/services/marketdata/internal/ctxsink"
	"github.com/illyanyc/strawberry/services/marketdata/internal/fmp"
	"github.com/illyanyc/strawberry/services/marketdata/internal/ingest"
	"github.com/illyanyc/strawberry/services/marketdata/internal/lake"
	"github.com/illyanyc/strawberry/services/marketdata/internal/live"
	"github.com/illyanyc/strawberry/services/marketdata/internal/recon"
	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
	"github.com/illyanyc/strawberry/services/marketdata/internal/refsync"
	"github.com/illyanyc/strawberry/services/marketdata/internal/sched"
	"github.com/illyanyc/strawberry/services/marketdata/internal/spikes"
	"github.com/illyanyc/strawberry/services/marketdata/internal/universe"
)

type cfg struct {
	config.Common
	DB    db.Config
	Bus   bus.Config
	Cache cache.Config
	// LakeDir is the Parquet lake root (D4: host APFS, bind-mounted).
	LakeDir    string `env:"TB_LAKE_DIR" envDefault:"/data/lake" json:"lake_dir"`
	GRPCAddr   string `env:"TB_GRPC_ADDR" envDefault:":9090" json:"grpc_addr"`
	BrokerAddr string `env:"TB_BROKER_GATEWAY_ADDR" envDefault:"broker-gateway:9090" json:"broker_gateway_addr"`
	// Feed for training and serving (D20: SIP; alpaca.iex only without Algo Trader Plus, O2).
	Feed          string        `env:"TB_MD_FEED" envDefault:"alpaca.sip" json:"feed"`
	StreamEnabled bool          `env:"TB_MD_STREAM_ENABLED" envDefault:"true" json:"stream_enabled"`
	UniverseName  string        `env:"TB_UNIVERSE" envDefault:"dev" json:"universe"`
	UniverseFile  string        `env:"TB_UNIVERSE_FILE" json:"universe_file"`
	Delays        string        `env:"TB_PUBLICATION_DELAYS" json:"publication_delays"`
	FMPRatePerMin int           `env:"TB_FMP_RATE_PER_MIN" envDefault:"240" json:"fmp_rate_per_min"`
	FMPBandwidth  int64         `env:"TB_FMP_BANDWIDTH_GB_30D" envDefault:"16" json:"fmp_bandwidth_gb_30d"`
	TickRetention time.Duration `env:"TB_LAKE_TICK_RETENTION" envDefault:"2160h" json:"tick_retention"`
	MinGapRun1m   int           `env:"TB_MD_MIN_GAP_RUN_1M" envDefault:"5" json:"min_gap_run_1m"`
	LakeFlush     time.Duration `env:"TB_LAKE_FLUSH_INTERVAL" envDefault:"30s" json:"lake_flush_interval"`
	HistoryStart  string        `env:"TB_MD_HISTORY_START" json:"history_start"` // YYYY-MM-DD; empty = no history load
	CatchupDays   int           `env:"TB_MD_CATCHUP_DAYS" envDefault:"10" json:"catchup_days"`
	// Context events from context-data (ctx.>) are persisted here (ADR-0032, CX-003).
	ContextSink    bool          `env:"TB_MD_CONTEXT_SINK" envDefault:"true" json:"context_sink"`
	ContextCompact time.Duration `env:"TB_MD_CONTEXT_COMPACT_EVERY" envDefault:"10m" json:"context_compact_every"`
}

func main() {
	if runSpike(os.Args[1:]) {
		return
	}
	var c cfg
	rt, err := svc.Start(&c, &c.Common)
	if err != nil {
		svc.Fatal(nil, "startup", err)
	}
	defer rt.Close()
	ctx := rt.Ctx
	if c.DB.User == "" {
		c.DB.User = "svc_marketdata"
	}
	if c.Bus.User == "" {
		c.Bus.User = "marketdata"
	}
	cal := calendar.XNYS()

	signer, err := svcauth.LoadSigner(rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "service key", err)
	}
	bconn, err := grpc.NewClient(c.BrokerAddr, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithDefaultCallOptions(signer.PerRPC("broker-gateway")))
	if err != nil {
		svc.Fatal(rt.Log, "broker-gateway client", err)
	}
	defer func() { _ = bconn.Close() }()
	brokerMD := brokerv1.NewBrokerMarketDataServiceClient(bconn)
	fmpKey, _ := rt.Secrets.GetString("fmp_api_key")
	if fmpKey == "unset" {
		fmpKey = ""
	}
	fc := fmp.New(fmpKey, fmp.Options{RatePerMin: c.FMPRatePerMin, BandwidthBudget: c.FMPBandwidth << 30, Registerer: rt.Ops.Registry()})

	if len(os.Args) > 2 && os.Args[1] == "spike" {
		switch os.Args[2] {
		case "v2":
			fmt.Println(spikes.JSON(spikes.V2(ctx, ingest.BrokerSource{Client: brokerMD, FeedStr: c.Feed})))
		case "v3":
			fmt.Println(spikes.JSON(spikes.V3(ctx, fc)))
		}
		return
	}

	pool, err := db.Connect(ctx, c.DB, rt.Secrets)
	if err != nil {
		svc.Fatal(rt.Log, "database", err)
	}
	defer pool.Close()
	lk := lake.New(c.LakeDir, lake.PGManifest{Pool: pool})
	if len(os.Args) > 2 && os.Args[1] == "lake" && os.Args[2] == "verify" {
		res, err := lk.Verify(ctx)
		fmt.Println(spikes.JSON(res))
		if err != nil || !res.OK() {
			os.Exit(1)
		}
		return
	}
	ref, err := refdata.LoadPG(ctx, pool)
	if err != nil {
		svc.Fatal(rt.Log, "refdata", err)
	}
	if _, err := refdata.MirrorCalendar(ctx, pool, cal); err != nil {
		rt.Log.Warn("calendar mirror failed", "err", err)
	}
	nc, err := bus.Connect(c.Bus, rt.Secrets, c.Service)
	if err != nil {
		svc.Fatal(rt.Log, "bus", err)
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		svc.Fatal(rt.Log, "jetstream", err)
	}
	cc, err := cache.Connect(c.Cache, rt.Secrets)
	if err != nil {
		svc.Fatal(rt.Log, "cache", err)
	}

	alert := func(ctx context.Context, subject, msg string) {
		_ = bus.Publish(ctx, js, "alert.warning", "", &alertsv1.Alert{AlertId: ulid.MustNew(ulid.Now(), rand.Reader).String(),
			Severity: alertsv1.Severity_SEVERITY_WARNING, Source: "marketdata", Subject: subject, Message: msg,
			DedupeKey: subject + ":" + time.Now().Format("2006-01-02"), RunbookUrl: "docs/runbooks/market-data.md", OccurredAt: time.Now().UnixNano()})
	}
	delays, err := ingest.ParseDelays(c.Delays)
	if err != nil {
		svc.Fatal(rt.Log, "config", err)
	}
	norm := ingest.Normalizer{Cal: cal, Ref: ref, Delays: delays, Now: time.Now}
	store := ingest.PGStore{Pool: pool}
	sources := map[string]ingest.Source{
		"alpaca.sip": ingest.BrokerSource{Client: brokerMD, FeedStr: "alpaca.sip"},
		"alpaca.iex": ingest.BrokerSource{Client: brokerMD, FeedStr: "alpaca.iex"},
	}
	bf := &ingest.Backfiller{Sources: sources, Lake: lk, Norm: norm, Symbols: ref, Store: store, Cal: cal, Log: rt.Log,
		MinGapRun1m: c.MinGapRun1m, Metrics: ingest.NewMetrics(rt.Ops.Registry())}
	go bf.Run(ctx)

	syncer := &refsync.Syncer{Ref: ref, Broker: brokerMD, FMP: fc, Alert: alert, Log: rt.Log, Now: time.Now,
		Publish: func(ctx context.Context, a *mdv1.CorporateAction) error {
			return bus.Publish(ctx, js, "ref.corporate_action."+a.GetInstrumentId(), "ca:"+a.GetSource()+":"+a.GetSourceId()+":"+a.GetAgreement().String(), a)
		}}
	unis := universe.Universes{Ref: ref}
	tickers, err := universe.Load(c.UniverseFile)
	if err != nil {
		svc.Fatal(rt.Log, "universe", err)
	}
	resolveUniverse := func(ctx context.Context) []string {
		ids, unknown := unis.FromTickers(ctx, tickers, time.Now())
		if len(unknown) > 0 && len(unknown) == len(tickers) {
			// Fresh install: learn the instrument master from the broker first.
			if err := syncer.Universe(ctx); err != nil {
				rt.Log.Warn("reference sync (universe) failed", "err", err)
			}
			if _, err := syncer.Assets(ctx); err != nil {
				rt.Log.Warn("reference sync (assets) failed; universe stays empty until it succeeds", "err", err)
			}
			ids, unknown = unis.FromTickers(ctx, tickers, time.Now())
		}
		if len(unknown) > 0 {
			rt.Log.Warn("universe tickers not in the instrument master", "count", len(unknown), "tickers", strings.Join(unknown, ","))
		}
		if len(ids) > 0 {
			_ = ref.SetUniverse(ctx, c.UniverseName, ids, time.Now().UnixNano())
			_ = ref.Flush(ctx)
			_ = ref.Export(ctx, lk)
		}
		return ids
	}
	ids := resolveUniverse(ctx)

	lakeSink := &live.LakeSink{L: lk, Interval: c.LakeFlush, Log: rt.Log}
	go lakeSink.Run(ctx)
	pipe := live.New(cal, ref, live.Multi{live.BusSink{JS: js}, live.CacheSink{C: cc}, lakeSink}, c.Feed, rt.Log, live.NewMetrics(rt.Ops.Registry()))
	var streamer *live.Streamer
	if c.StreamEnabled {
		streamer = &live.Streamer{Client: brokerMD, Feed: c.Feed, Pipe: pipe, Hist: sources[c.Feed], Log: rt.Log}
		if len(ids) > 0 {
			if m, err := unis.Members(ctx, c.UniverseName, time.Now()); err == nil {
				pipe.SetSymbols(m)
			}
		}
		go streamer.Run(ctx)
	}

	breaches := prometheus.NewCounter(prometheus.CounterOpts{Name: "tb_md_recon_breaches_total", Help: "Cross-source reconciliation breaches (symbol-days quarantined)."})
	rt.Ops.Registry().MustRegister(breaches)
	reconciler := &recon.Reconciler{Lake: lk, EOD: fc, Cal: cal, Store: recon.PGStore{Pool: pool}, Tol: recon.Default, PrimaryFeed: c.Feed, Alert: alert, Breaches: breaches,
		SymbolAt: func(ctx context.Context, id string, at int64) string {
			for _, s := range ref.History(ctx, id) {
				if s.ValidFrom <= at && (s.ValidTo == 0 || at < s.ValidTo) {
					return s.Symbol
				}
			}
			return ""
		}}
	timeframes := []mdv1.Timeframe{mdv1.Timeframe_TIMEFRAME_1M, mdv1.Timeframe_TIMEFRAME_5M, mdv1.Timeframe_TIMEFRAME_15M, mdv1.Timeframe_TIMEFRAME_1D}
	submitStartupJobs(ctx, rt.Log, bf, c, ids, timeframes)
	scheduler := &sched.Scheduler{Cal: cal, Now: time.Now, Log: rt.Log, Jobs: []sched.Job{
		{Name: "refsync", Due: sched.At(6, 0, 0), Run: func(ctx context.Context, s calendar.Session) error {
			ids = resolveUniverse(ctx)
			err := syncer.Sync(ctx, ids, time.Now().AddDate(0, 0, -30), time.Now().AddDate(0, 0, 30))
			return errors.Join(err, ref.Flush(ctx), ref.Export(ctx, lk))
		}},
		{Name: "eod", Due: sched.AfterClose(20 * time.Minute), Run: func(ctx context.Context, s calendar.Session) error {
			// Source-final bars replace provisional live bars; differences become revisions (MD-006).
			if err := lakeSink.Flush(ctx); err != nil {
				return err
			}
			if len(ids) > 0 {
				id, err := bf.Submit(ctx, &ingest.Job{Source: c.Feed, Timeframes: timeframes, Instruments: ids, Start: s.PreOpen, End: s.PostClose})
				if err != nil {
					return err
				}
				_ = id
			}
			if _, err := lk.CompactAll(ctx); err != nil {
				return err
			}
			_, err := lk.PurgeOlderThan(ctx, "quotes", c.TickRetention, time.Now())
			return errors.Join(err, ref.Export(ctx, lk))
		}},
		{Name: "recon", Due: sched.At(7, 0, 1), Run: func(ctx context.Context, s calendar.Session) error {
			if len(ids) == 0 {
				return nil
			}
			sum, err := reconciler.Run(ctx, ids, s.Open, s.Open)
			rt.Log.Info("reconciliation", "session", s.Date, "checked", sum.Checked, "breaches", sum.Breaches, "missing", sum.Missing)
			return err
		}},
	}}
	go scheduler.Run(ctx)
	go publishHealth(ctx, js, pipe, fc, c.Feed)
	if c.ContextSink {
		go ctxsink.New(lk, rt.Log, rt.Ops.Registry()).Run(ctx, js)
		go compactContextEvery(ctx, rt.Log, lk, c.ContextCompact)
	}

	verifier, err := svcauth.LoadVerifier(rt.Secrets, c.Service, "api-gateway", "execution")
	if err != nil {
		svc.Fatal(rt.Log, "service keys", err)
	}
	allowed := maps.Clone(api.AllowedCallers)
	gs := grpc.NewServer(grpc.UnaryInterceptor(verifier.UnaryServerInterceptor(allowed)), grpc.StreamInterceptor(verifier.StreamServerInterceptor(allowed)))
	mdv1.RegisterMarketDataServiceServer(gs, &api.Server{Backfiller: bf, Store: store, Pipe: pipe, Streamer: streamer, Universe: unis, Feed: c.Feed, Now: time.Now, Bars: cc, Symbols: unis})

	rt.Ops.AddCheck("postgres", db.Ping(pool))
	rt.Ops.AddCheck("bus", bus.Ready(nc))
	rt.Ops.AddCheck("cache", cc.Ready)
	rt.ServeOps()
	lis, err := (&net.ListenConfig{}).Listen(ctx, "tcp", c.GRPCAddr)
	if err != nil {
		svc.Fatal(rt.Log, "listen", err)
	}
	go func() {
		<-ctx.Done()
		gs.GracefulStop()
	}()
	rt.Log.Info("marketdata running", "lake_dir", c.LakeDir, "feed", c.Feed, "universe", c.UniverseName, "instruments", len(ids), "stream", c.StreamEnabled,
		"fmp", fmpKey != "")
	if err := gs.Serve(lis); err != nil {
		svc.Fatal(rt.Log, "grpc", err)
	}
	rt.Wait()
}

// compactContextEvery merges staged context files on a timer so partitions stay one file each.
func compactContextEvery(ctx context.Context, log *slog.Logger, lk *lake.Lake, every time.Duration) {
	if every <= 0 {
		return
	}
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if res, err := lk.CompactAllContext(ctx); err != nil {
				log.Warn("context compaction failed", "err", err)
			} else if len(res) > 0 {
				log.Info("context compacted", "partitions", len(res))
			}
		}
	}
}

// publishHealth publishes SourceHealth on sys.health.<source> every 15 s (MD-008).
func publishHealth(ctx context.Context, js jetstream.JetStream, pipe *live.Pipeline, fc *fmp.Client, feed string) {
	t := time.NewTicker(15 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		now := time.Now()
		age := int64(-1)
		if last := pipe.LastMessageAt(); last > 0 {
			age = now.Sub(time.Unix(0, last)).Milliseconds()
		}
		state := "closed"
		if !pipe.Connected() {
			state = "disconnected"
		}
		_ = bus.Publish(ctx, js, "sys.health."+feed, "", &mdv1.SourceHealth{Source: feed, LastMessageAgeMs: age, BreakerState: state, AsOf: now.UnixNano()})
		_ = bus.Publish(ctx, js, "sys.health.fmp", "", &mdv1.SourceHealth{Source: "fmp", LastMessageAgeMs: -1, BreakerState: fc.Breaker.State().String(), AsOf: now.UnixNano()})
	}
}

// submitStartupJobs queues the one-off history load (TB_MD_HISTORY_START) and a daily catch-up of
// the last TB_MD_CATCHUP_DAYS days, so an outage longer than the scheduler's 72 h look-back still
// gets backfilled (MD-006). Job IDs are derived from their parameters, so restarts never queue the
// same work twice (MD-001).
func submitStartupJobs(ctx context.Context, log *slog.Logger, bf *ingest.Backfiller, c cfg, ids []string, tfs []mdv1.Timeframe) {
	if len(ids) == 0 {
		return
	}
	sorted := slices.Clone(ids)
	slices.Sort(sorted)
	key := func(kind string, start time.Time) string {
		h := sha256.Sum256([]byte(kind + "|" + c.Feed + "|" + start.Format("2006-01-02") + "|" + strings.Join(sorted, ",")))
		return kind + "-" + start.Format("20060102") + "-" + hex.EncodeToString(h[:8])
	}
	ny, _ := time.LoadLocation("America/New_York")
	now := time.Now().In(ny)
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, ny)
	var jobs []*ingest.Job
	if c.HistoryStart != "" {
		start, err := time.ParseInLocation("2006-01-02", c.HistoryStart, ny)
		if err != nil {
			log.Error("TB_MD_HISTORY_START must be YYYY-MM-DD", "value", c.HistoryStart)
		} else if start.Before(today) {
			jobs = append(jobs, &ingest.Job{ID: key("history", start), Start: start})
		}
	}
	if c.CatchupDays > 0 {
		jobs = append(jobs, &ingest.Job{ID: key("catchup", today), Start: today.AddDate(0, 0, -c.CatchupDays)})
	}
	for _, j := range jobs {
		j.Source, j.Timeframes, j.Instruments, j.End = c.Feed, tfs, ids, today
		if _, err := bf.Submit(ctx, j); err != nil {
			log.Warn("startup backfill not queued", "job", j.ID, "err", err)
			continue
		}
		log.Info("startup backfill queued (no-op if it already ran)", "job", j.ID, "from", j.Start.Format("2006-01-02"))
	}
}
