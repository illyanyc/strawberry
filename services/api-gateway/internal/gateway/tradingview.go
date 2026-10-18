package gateway

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"golang.org/x/time/rate"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	signalsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/signals/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/cfaccess"
)

// AccessVerifier checks a Cloudflare Access token for an application audience (SE-064).
type AccessVerifier interface {
	Verify(ctx context.Context, token, aud string) error
}

// TradingViewIPs are TradingView's published webhook source addresses (research 07 §5).
var TradingViewIPs = []string{"52.89.214.238/32", "34.212.75.30/32", "54.218.53.128/32", "52.32.178.7/32"}

var tvNameRe = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,39}$`)

// tvIntake is the TradingView webhook intake (section 14, D70, SE-064): a separate listener that
// turns verified alerts into Signals for the matching tv-<name> strategy. It never creates orders;
// the strategy runtime turns signals into intents, which pass the normal risk gate (RM-001).
type tvIntake struct {
	enabled   bool
	key       []byte
	allowed   []netip.Prefix
	trusted   []netip.Prefix
	maxAge    time.Duration
	skew      time.Duration
	verifier  AccessVerifier
	aud       string
	publicURL string

	mu        sync.Mutex
	seen      map[string]time.Time
	limiters  map[string]*rate.Limiter
	failures  []time.Time
	strats    map[string]*ordersv1.Strategy
	stratsAt  time.Time
	symbols   map[string]string
	symbolsAt time.Time
}

func newTVIntake(cfg Config, key []byte, v AccessVerifier) *tvIntake {
	t := &tvIntake{enabled: cfg.TVWebhookAddr != "" && len(key) >= 32, key: key, maxAge: cfg.TVMaxAge, skew: 30 * time.Second,
		verifier: v, aud: cfg.CFAccessTVAud, publicURL: strings.TrimRight(cfg.TVPublicURL, "/"),
		seen: map[string]time.Time{}, limiters: map[string]*rate.Limiter{}, symbols: map[string]string{}}
	for _, p := range cfg.TVAllowedIPs {
		if pr, err := netip.ParsePrefix(strings.TrimSpace(p)); err == nil {
			t.allowed = append(t.allowed, pr)
		}
	}
	for _, p := range cfg.TVTrustedProxies {
		if pr, err := netip.ParsePrefix(strings.TrimSpace(p)); err == nil {
			t.trusted = append(t.trusted, pr)
		}
	}
	if t.maxAge <= 0 {
		t.maxAge = time.Minute
	}
	return t
}

// TVSecret is a tv-<name> strategy's webhook secret: HMAC-SHA256 of the strategy ID under the
// tv_webhook_key secret, base64url, 32 characters. It is printed only by `api-gateway tv-secret`
// on the operator's terminal, never logged or sent to the browser (SE-064).
func TVSecret(key []byte, strategyID string) string {
	m := hmac.New(sha256.New, key)
	m.Write([]byte("tb1/tv-webhook/" + strategyID))
	return base64.RawURLEncoding.EncodeToString(m.Sum(nil))[:32]
}

type tvPayload struct {
	Secret  string `json:"secret"`
	Ticker  string `json:"ticker"`
	Action  string `json:"action"`
	Time    string `json:"time"`
	Price   string `json:"price"`
	AlertID string `json:"alert_id"`
	Comment string `json:"comment"`
}

var tvDirections = map[string]signalsv1.Direction{
	"buy": signalsv1.Direction_DIRECTION_LONG, "long": signalsv1.Direction_DIRECTION_LONG,
	"sell": signalsv1.Direction_DIRECTION_SHORT, "short": signalsv1.Direction_DIRECTION_SHORT,
	"exit": signalsv1.Direction_DIRECTION_FLAT, "close": signalsv1.Direction_DIRECTION_FLAT, "flat": signalsv1.Direction_DIRECTION_FLAT,
}

// WebhookHandler serves the dedicated webhook listener: POST /webhooks/tradingview/{name} and
// /healthz only. It is never mounted on the UI listener (section 14 pitfall: no webhook on the main
// gateway's address).
func (s *Server) WebhookHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /webhooks/tradingview/{name}", s.handleTVWebhook)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	return s.correlation(mux)
}

// clientIP of a webhook request: Cf-Connecting-Ip when the peer is a trusted proxy (cloudflared),
// the TCP peer otherwise.
func (t *tvIntake) clientIP(r *http.Request) (netip.Addr, bool) {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	peer, err := netip.ParseAddr(host)
	if err != nil {
		return netip.Addr{}, false
	}
	peer = peer.Unmap()
	for _, p := range t.trusted {
		if p.Contains(peer) {
			if a, err := netip.ParseAddr(strings.TrimSpace(r.Header.Get("Cf-Connecting-Ip"))); err == nil {
				return a.Unmap(), true
			}
			return netip.Addr{}, false
		}
	}
	return peer, true
}

func (t *tvIntake) ipAllowed(a netip.Addr) bool {
	if len(t.allowed) == 0 {
		return true
	}
	for _, p := range t.allowed {
		if p.Contains(a) {
			return true
		}
	}
	return false
}

func (t *tvIntake) allow(ip string) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	l, ok := t.limiters[ip]
	if !ok {
		if len(t.limiters) > 1000 {
			t.limiters = map[string]*rate.Limiter{}
		}
		l = rate.NewLimiter(rate.Every(100*time.Millisecond), 20)
		t.limiters[ip] = l
	}
	return l.Allow()
}

// firstSeen records an idempotency key and reports whether it is new (replay protection: keys are
// kept for maxAge + skew, beyond which the timestamp check rejects the alert anyway).
func (t *tvIntake) firstSeen(key string, now time.Time) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	for k, at := range t.seen {
		if now.Sub(at) > t.maxAge+t.skew+time.Minute {
			delete(t.seen, k)
		}
	}
	if _, dup := t.seen[key]; dup {
		return false
	}
	t.seen[key] = now
	return true
}

// failure counts unauthenticated requests; it returns true once per 10-minute window when five
// have accumulated, so the owner gets one security alert, not a flood.
func (t *tvIntake) failure(now time.Time) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	keep := t.failures[:0]
	for _, at := range t.failures {
		if now.Sub(at) < 10*time.Minute {
			keep = append(keep, at)
		}
	}
	t.failures = append(keep, now)
	return len(t.failures) == 5
}

func (s *Server) handleTVWebhook(w http.ResponseWriter, r *http.Request) {
	t := s.tv
	if t == nil || !t.enabled {
		writeError(w, http.StatusNotFound, "not_found", "no such route")
		return
	}
	ip, ok := t.clientIP(r)
	if !ok {
		writeError(w, http.StatusForbidden, "forbidden", "unknown client address")
		return
	}
	if !t.allow(ip.String()) {
		writeError(w, http.StatusTooManyRequests, "rate_limited", "too many requests")
		return
	}
	if !t.ipAllowed(ip) {
		s.tvReject(r, "source address not allowed")
		writeError(w, http.StatusForbidden, "forbidden", "source address not allowed")
		return
	}
	if t.verifier != nil {
		if err := t.verifier.Verify(r.Context(), r.Header.Get(cfaccess.Header), t.aud); err != nil {
			s.tvReject(r, "cloudflare access token rejected")
			writeError(w, http.StatusForbidden, "forbidden", "access token rejected")
			return
		}
	}
	name := r.PathValue("name")
	if !tvNameRe.MatchString(name) {
		writeError(w, http.StatusNotFound, "not_found", "no such webhook")
		return
	}
	strategyID := "tv-" + name
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8<<10))
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "too_large", "body too large")
		return
	}
	var p tvPayload
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&p); err != nil {
		s.tvReject(r, "malformed body")
		writeError(w, http.StatusBadRequest, "bad_request", "body must be the JSON alert message (secret, ticker, action, time)")
		return
	}
	want := TVSecret(t.key, strategyID)
	if subtle.ConstantTimeCompare([]byte(p.Secret), []byte(want)) != 1 {
		s.tvReject(r, "bad secret")
		writeError(w, http.StatusUnauthorized, "unauthenticated", "bad secret")
		return
	}
	// Authenticated from here on: every outcome is audited (SE-064).
	now := s.now()
	act := strings.ToLower(strings.TrimSpace(p.Action))
	ticker := strings.ToUpper(strings.TrimSpace(p.Ticker))
	if i := strings.LastIndex(ticker, ":"); i >= 0 {
		ticker = ticker[i+1:]
	}
	outcome := func(status int, code, detail string, extra map[string]any) {
		d := map[string]any{"outcome": code, "ticker": ticker, "action": act, "detail": detail, "alert_id": truncate(p.AlertID, 64), "ip": ip.String()}
		for k, v := range extra {
			d[k] = v
		}
		if _, _, err := s.appendAudit(r.Context(), audit.Event{Actor: "webhook:tradingview", Action: string(policy.ActSignalsCreate), Target: strategyID,
			IP: ip.String(), Device: "tradingview", Details: d}); err != nil {
			s.log.ErrorContext(r.Context(), "audit append failed", "action", "signals.create", "error", err)
			writeError(w, http.StatusServiceUnavailable, "audit_failed", "audit log unavailable")
			return
		}
		if status == http.StatusOK {
			writeJSON(w, status, map[string]any{"status": code, "signal_id": extra["signal_id"]})
			return
		}
		writeError(w, status, code, detail)
	}
	at, err := time.Parse(time.RFC3339, strings.TrimSpace(p.Time))
	if err != nil {
		outcome(http.StatusBadRequest, "bad_time", "time must be RFC 3339 ({{timenow}})", nil)
		return
	}
	if now.Sub(at) > t.maxAge || at.Sub(now) > t.skew {
		outcome(http.StatusUnprocessableEntity, "stale", "alert time outside the accepted window", map[string]any{"alert_time": at.UTC().Format(time.RFC3339)})
		return
	}
	dir, ok := tvDirections[act]
	if !ok || !symbolRe.MatchString(ticker) {
		outcome(http.StatusBadRequest, "bad_request", "action must be buy|sell|exit and ticker a symbol", nil)
		return
	}
	sum := sha256.Sum256([]byte(strategyID + "|" + ticker + "|" + act + "|" + at.UTC().Format(time.RFC3339Nano) + "|" + p.AlertID))
	idem := hex.EncodeToString(sum[:16])
	if !t.firstSeen(idem, now) {
		outcome(http.StatusOK, "duplicate", "already accepted", map[string]any{"idempotency_key": idem})
		return
	}
	if d := policy.Authorize(policy.Actor{Kind: policy.KindWebhook, Name: "tradingview"}, policy.ActSignalsCreate, policy.Resource{ID: strategyID}, now); d.Effect != policy.Allow {
		outcome(http.StatusForbidden, "forbidden", d.Reason, nil)
		return
	}
	registered, err := s.tvStrategy(r.Context(), strategyID)
	if err != nil {
		outcome(http.StatusServiceUnavailable, "execution_unavailable", "strategy registry unavailable", nil)
		return
	}
	if registered == nil {
		outcome(http.StatusNotFound, "unknown_strategy", "no registered strategy "+strategyID, nil)
		return
	}
	instrument, err := s.tvResolve(r.Context(), ticker)
	if err != nil {
		outcome(http.StatusServiceUnavailable, "marketdata_unavailable", "symbol lookup unavailable", nil)
		return
	}
	if instrument == "" {
		// UI-011: unknown tickers are reported, never guessed.
		outcome(http.StatusUnprocessableEntity, "unknown_symbol", "ticker is not in the instrument master", nil)
		return
	}
	if s.signals == nil {
		outcome(http.StatusServiceUnavailable, "bus_unavailable", "signal bus unavailable", nil)
		return
	}
	sig := &signalsv1.Signal{SignalId: ulid.MustNew(ulid.Timestamp(now), rand.Reader).String(), StrategyId: strategyID, ModelVersion: "tradingview",
		InstrumentId: instrument, AsOf: now.UnixNano(), Direction: dir, CorrelationId: ulid.MustNew(ulid.Timestamp(now), rand.Reader).String()}
	pctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.signals.Publish(pctx, SignalSubject(strategyID, instrument), idem, sig); err != nil {
		t.mu.Lock()
		delete(t.seen, idem)
		t.mu.Unlock()
		outcome(http.StatusServiceUnavailable, "publish_failed", "signal not stored; TradingView will retry", nil)
		return
	}
	mode := strings.TrimPrefix(registered.GetMode().String(), "STRATEGY_MODE_")
	outcome(http.StatusOK, "accepted", "signal for "+strings.ToLower(mode)+" strategy", map[string]any{"signal_id": sig.GetSignalId(),
		"instrument_id": instrument, "idempotency_key": idem, "direction": dir.String(), "correlation_id": sig.GetCorrelationId()})
}

// tvReject logs an unauthenticated request (not audited: anyone can send those) and raises one
// security alert per burst.
func (s *Server) tvReject(r *http.Request, why string) {
	s.log.WarnContext(r.Context(), "tradingview webhook rejected", "reason", why)
	if s.tv.failure(s.now()) {
		s.securityAlert(r.Context(), "TradingView webhook rejections", "Five or more unauthenticated TradingView webhook requests in 10 minutes ("+why+").", "tv-webhook-rejections")
	}
}

// tvStrategy returns the registry entry for strategyID (cached 30 s), or nil when unknown.
func (s *Server) tvStrategy(ctx context.Context, strategyID string) (*ordersv1.Strategy, error) {
	t := s.tv
	t.mu.Lock()
	if t.strats != nil && s.now().Sub(t.stratsAt) < 30*time.Second {
		st := t.strats[strategyID]
		t.mu.Unlock()
		return st, nil
	}
	t.mu.Unlock()
	all, err := s.tvStrategies(ctx)
	if err != nil {
		return nil, err
	}
	return all[strategyID], nil
}

func (s *Server) tvStrategies(ctx context.Context) (map[string]*ordersv1.Strategy, error) {
	if s.trading == nil || s.trading.Exec == nil {
		return nil, errors.New("execution not configured")
	}
	cctx, cancel := context.WithTimeout(ctx, 1500*time.Millisecond)
	defer cancel()
	resp, err := s.trading.Exec.ListStrategies(cctx, &ordersv1.ListStrategiesRequest{})
	if err != nil {
		return nil, err
	}
	m := map[string]*ordersv1.Strategy{}
	for _, st := range resp.GetStrategies() {
		if strings.HasPrefix(st.GetStrategyId(), "tv-") {
			m[st.GetStrategyId()] = st
		}
	}
	s.tv.mu.Lock()
	s.tv.strats, s.tv.stratsAt = m, s.now()
	s.tv.mu.Unlock()
	return m, nil
}

// tvResolve maps a ticker to its instrument ID (cached 10 minutes); "" means unknown.
func (s *Server) tvResolve(ctx context.Context, ticker string) (string, error) {
	t := s.tv
	t.mu.Lock()
	if s.now().Sub(t.symbolsAt) > 10*time.Minute {
		t.symbols, t.symbolsAt = map[string]string{}, s.now()
	}
	if id, ok := t.symbols[ticker]; ok {
		t.mu.Unlock()
		return id, nil
	}
	t.mu.Unlock()
	if s.trading == nil || s.trading.MD == nil {
		return "", errors.New("marketdata not configured")
	}
	cctx, cancel := context.WithTimeout(ctx, 1500*time.Millisecond)
	defer cancel()
	resp, err := s.trading.MD.ResolveSymbols(cctx, &mdv1.ResolveSymbolsRequest{Symbols: []string{ticker}})
	if err != nil {
		return "", err
	}
	id := ""
	for _, m := range resp.GetMatches() {
		if strings.EqualFold(m.GetSymbol(), ticker) {
			id = m.GetInstrumentId()
		}
	}
	t.mu.Lock()
	t.symbols[ticker] = id
	t.mu.Unlock()
	return id, nil
}

// handleTradingViewStatus is the owner's view of the intake (UI-048): configuration, the tv-<name>
// strategies, and recent deliveries from the audit log. It never returns a secret.
func (s *Server) handleTradingViewStatus(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActTradingViewStatusRead, policy.Resource{}) {
		return
	}
	t := s.tv
	widget, _ := s.ownerFlags(r.Context())
	ips := []string{}
	for _, p := range t.allowed {
		ips = append(ips, p.String())
	}
	strategies := []map[string]any{}
	if all, err := s.tvStrategies(r.Context()); err == nil {
		for id, st := range all {
			strategies = append(strategies, map[string]any{"strategy_id": id, "name": st.GetName(), "mode": st.GetMode().String(),
				"broker_account_id": st.GetBrokerAccountId(), "webhook_path": "/webhooks/tradingview/" + strings.TrimPrefix(id, "tv-")})
		}
	}
	deliveries, err := s.tvDeliveries(r.Context())
	if err != nil {
		s.log.WarnContext(r.Context(), "tradingview deliveries", "error", err)
	}
	writeJSON(w, http.StatusOK, map[string]any{"enabled": t.enabled, "public_url": t.publicURL, "access_jwt_required": t.verifier != nil,
		"allowed_ips": ips, "max_age_seconds": int(t.maxAge.Seconds()), "widget_enabled": widget, "strategies": strategies, "deliveries": deliveries})
}

func (s *Server) tvDeliveries(ctx context.Context) ([]map[string]any, error) {
	out := []map[string]any{}
	if s.pool == nil {
		return out, nil
	}
	rows, err := s.pool.Query(ctx, `SELECT occurred_at, target, details_json FROM audit.events WHERE actor='webhook:tradingview' ORDER BY seq DESC LIMIT 50`)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var occurred int64
		var target, details string
		if err := rows.Scan(&occurred, &target, &details); err != nil {
			return out, err
		}
		var d map[string]any
		_ = json.Unmarshal([]byte(details), &d)
		str := func(k string) string { v, _ := d[k].(string); return v }
		out = append(out, map[string]any{"at": time.Unix(0, occurred).UTC(), "strategy_id": target, "outcome": str("outcome"),
			"ticker": str("ticker"), "action": str("action"), "detail": str("detail")})
	}
	return out, rows.Err()
}
