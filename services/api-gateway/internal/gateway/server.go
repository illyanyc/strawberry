package gateway

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"sync"
	"time"

	"github.com/go-webauthn/webauthn/protocol"
	"github.com/go-webauthn/webauthn/webauthn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/oklog/ulid/v2"
	"golang.org/x/time/rate"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/policy"
)

// AlertPublisher publishes security alerts (alert.security, SE-041). Implementations must not block long.
type AlertPublisher interface {
	Publish(ctx context.Context, a *alertsv1.Alert) error
}

// Server is the HTTP gateway.
type Server struct {
	cfg     Config
	store   *Store
	pool    *pgxpool.Pool
	wa      *webauthn.WebAuthn
	keys    keys
	audit   *audit.Writer
	log     *slog.Logger
	alerts  AlertPublisher
	creds   CredentialClient
	web     *webUI
	now     func() time.Time
	proxies []netip.Prefix
	routes  []route

	limMu     sync.Mutex
	limiters  map[string]*rate.Limiter
	rateEvery time.Duration
	rateBurst int
}

// Deps are the gateway's collaborators.
type Deps struct {
	Pool      *pgxpool.Pool
	MasterKey []byte
	Log       *slog.Logger
	Alerts    AlertPublisher
	Creds     CredentialClient
}

type route struct {
	method  string
	pattern string
	// public routes skip the session check (SE-004 allowlist).
	public bool
	h      func(http.ResponseWriter, *http.Request, *reqCtx)
}

type reqCtx struct {
	session *Session
	ip      string
	ua      string
}

func (rc *reqCtx) actor(now time.Time) policy.Actor {
	if rc.session == nil {
		return policy.Actor{Kind: policy.KindAnonymous}
	}
	a := policy.Actor{Kind: policy.KindOwner, Name: rc.session.OwnerID, StepUpMethod: policy.StepUpMethod(rc.session.StepUpMethod)}
	if rc.session.StepUpUntil != nil {
		a.StepUpUntil = *rc.session.StepUpUntil
	}
	return a
}

// New builds the server.
func New(cfg Config, d Deps) (*Server, error) {
	k, err := deriveKeys(d.MasterKey)
	if err != nil {
		return nil, err
	}
	wa, err := webauthn.New(&webauthn.Config{
		RPID:          cfg.RPID,
		RPDisplayName: cfg.RPDisplayName,
		RPOrigins:     cfg.Origins,
		AuthenticatorSelection: protocol.AuthenticatorSelection{
			ResidentKey:      protocol.ResidentKeyRequirementRequired,
			UserVerification: protocol.VerificationRequired,
		},
		AttestationPreference: protocol.PreferNoAttestation,
	})
	if err != nil {
		return nil, err
	}
	s := &Server{
		cfg: cfg, store: &Store{pool: d.Pool}, pool: d.Pool, wa: wa, keys: k, audit: audit.NewWriter(d.Pool),
		log: d.Log, alerts: d.Alerts, creds: d.Creds, now: time.Now, limiters: map[string]*rate.Limiter{},
		rateEvery: 200 * time.Millisecond, rateBurst: 30,
	}
	for _, p := range cfg.TrustedProxies {
		s.proxies = append(s.proxies, netip.MustParsePrefix(strings.TrimSpace(p)))
	}
	if cfg.WebDir != "" {
		s.web = newWebUI(cfg.WebDir, d.Log)
	}
	s.registerRoutes()
	return s, nil
}

// Routes returns method+pattern and public flag for every API route (route-inventory test, SE-004).
func (s *Server) Routes() []struct {
	Method, Pattern string
	Public          bool
} {
	out := make([]struct {
		Method, Pattern string
		Public          bool
	}, len(s.routes))
	for i, r := range s.routes {
		out[i].Method, out[i].Pattern, out[i].Public = r.method, r.pattern, r.public
	}
	return out
}

func (s *Server) registerRoutes() {
	pub := func(m, p string, h func(http.ResponseWriter, *http.Request, *reqCtx)) {
		s.routes = append(s.routes, route{m, p, true, h})
	}
	priv := func(m, p string, h func(http.ResponseWriter, *http.Request, *reqCtx)) {
		s.routes = append(s.routes, route{m, p, false, h})
	}
	// Public allowlist (SE-004). Anything not listed here requires a session.
	pub("GET", "/healthz", s.handleHealth)
	pub("GET", "/auth/state", s.handleState)
	pub("POST", "/auth/setup/begin", s.handleSetupBegin)
	pub("POST", "/auth/setup/finish", s.handleSetupFinish)
	pub("POST", "/auth/webauthn/login/begin", s.handleLoginBegin)
	pub("POST", "/auth/webauthn/login/finish", s.handleLoginFinish)
	pub("POST", "/auth/totp/login", s.handleTOTPLogin)
	pub("POST", "/auth/recovery", s.handleRecoveryLogin)

	priv("GET", "/auth/me", s.handleMe)
	priv("POST", "/auth/logout", s.handleLogout)
	priv("POST", "/auth/webauthn/register/begin", s.handleRegisterBegin)
	priv("POST", "/auth/webauthn/register/finish", s.handleRegisterFinish)
	priv("DELETE", "/auth/webauthn/credentials/{id}", s.handleDeleteCredential)
	priv("POST", "/auth/totp/enroll", s.handleTOTPEnroll)
	priv("POST", "/auth/totp/verify", s.handleTOTPVerify)
	priv("POST", "/auth/recovery/regenerate", s.handleRecoveryRegenerate)
	priv("POST", "/auth/step-up/begin", s.handleStepUpBegin)
	priv("POST", "/auth/step-up/finish", s.handleStepUpFinish)
	priv("POST", "/auth/step-up/totp", s.handleStepUpTOTP)
	priv("GET", "/auth/sessions", s.handleListSessions)
	priv("DELETE", "/auth/sessions/{id}", s.handleRevokeSession)
	priv("GET", "/api/v1/audit", s.handleAuditList)
	priv("GET", "/api/v1/audit/verify", s.handleAuditVerify)
	priv("GET", "/api/v1/settings", s.handleSettingsGet)
	priv("PUT", "/api/v1/settings/{key}", s.handleSettingsPut)
	priv("GET", "/api/v1/broker/credentials", s.handleCredentialsList)
	priv("PUT", "/api/v1/broker/credentials/{account}/{environment}", s.handleCredentialsPut)
	priv("DELETE", "/api/v1/broker/credentials/{account}/{environment}", s.handleCredentialsDelete)
	priv("GET", "/api/v1/system/status", s.handleSystemStatus)
	priv("GET", "/api/v1/events", s.handleEvents)
}

// Handler returns the full HTTP handler (API + static UI).
func (s *Server) Handler(metrics func(http.Handler) http.Handler) http.Handler {
	mux := http.NewServeMux()
	for _, rt := range s.routes {
		rt := rt
		mux.Handle(rt.method+" "+rt.pattern, s.wrap(rt))
	}
	// Unknown API paths must not fall through to the UI.
	mux.HandleFunc("/api/", func(w http.ResponseWriter, _ *http.Request) {
		writeError(w, http.StatusNotFound, "not_found", "no such route")
	})
	mux.HandleFunc("/auth/", func(w http.ResponseWriter, _ *http.Request) {
		writeError(w, http.StatusNotFound, "not_found", "no such route")
	})
	if s.web != nil {
		mux.Handle("GET /", s.web)
	}
	var h http.Handler = mux
	if metrics != nil {
		h = metrics(h)
	}
	return s.securityHeaders(s.correlation(h))
}

func (s *Server) wrap(rt route) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rc := &reqCtx{ip: s.clientIP(r), ua: truncate(r.UserAgent(), 256)}
		if strings.HasPrefix(rt.pattern, "/auth/") && !s.allow(rc.ip) {
			writeError(w, http.StatusTooManyRequests, "rate_limited", "too many requests")
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead && !s.checkCSRF(r) {
			writeError(w, http.StatusForbidden, "csrf", "missing or invalid CSRF token or origin")
			return
		}
		if !rt.public {
			sess, err := s.sessionFromRequest(r)
			if err != nil {
				s.clearSessionCookie(w)
				writeError(w, http.StatusUnauthorized, "unauthenticated", "login required")
				return
			}
			rc.session = sess
		}
		r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
		rt.h(w, r, rc)
	})
}

// ---- middleware ----

func (s *Server) correlation(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Correlation-Id")
		if len(id) == 0 || len(id) > 64 {
			id = ulid.MustNew(ulid.Now(), rand.Reader).String()
		}
		w.Header().Set("X-Correlation-Id", id)
		next.ServeHTTP(w, r.WithContext(logging.WithCorrelationID(r.Context(), id)))
	})
}

// securityHeaders sets SE-013 headers on every response; the UI handler adds the CSP script hashes.
func (s *Server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Cross-Origin-Opener-Policy", "same-origin")
		h.Set("Cross-Origin-Resource-Policy", "same-origin")
		h.Set("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), publickey-credentials-get=(self), publickey-credentials-create=(self)")
		h.Set("Content-Security-Policy", cspPolicy)
		if s.cfg.CookieSecure && !strings.HasPrefix(s.cfg.Origins[0], "http://") {
			h.Set("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
		}
		if strings.HasPrefix(r.URL.Path, "/api/") || strings.HasPrefix(r.URL.Path, "/auth/") {
			h.Set("Cache-Control", "no-store")
		}
		s.ensureCSRFCookie(w, r)
		next.ServeHTTP(w, r)
	})
}

// cspPolicy is strict: the Vite SPA ships no inline scripts or styles (SE-013).
const cspPolicy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
	"font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; " +
	"frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'"

// ---- CSRF (SE-006) ----

func (s *Server) csrfCookieName() string {
	if s.cfg.CookieSecure {
		return "__Host-tb_csrf"
	}
	return "tb_csrf"
}

func (s *Server) ensureCSRFCookie(w http.ResponseWriter, r *http.Request) {
	if c, err := r.Cookie(s.csrfCookieName()); err == nil && len(c.Value) >= 32 {
		return
	}
	tok, err := randomToken(32)
	if err != nil {
		return
	}
	http.SetCookie(w, &http.Cookie{ //nolint:gosec // Secure is config-driven (off only for plain-http localhost); readable by JS by design (double-submit)
		Name: s.csrfCookieName(), Value: tok, Path: "/", Secure: s.cfg.CookieSecure,
		HttpOnly: false, SameSite: http.SameSiteStrictMode})
}

func (s *Server) checkCSRF(r *http.Request) bool {
	origin := r.Header.Get("Origin")
	if origin == "" || !s.originAllowed(origin) {
		return false
	}
	c, err := r.Cookie(s.csrfCookieName())
	if err != nil || len(c.Value) < 32 {
		return false
	}
	hdr := r.Header.Get("X-CSRF-Token")
	return subtle.ConstantTimeCompare([]byte(hdr), []byte(c.Value)) == 1
}

func (s *Server) originAllowed(origin string) bool {
	for _, o := range s.cfg.Origins {
		if strings.EqualFold(o, origin) {
			return true
		}
	}
	return false
}

// ---- sessions (SE-005) ----

func (s *Server) sessionCookieName() string {
	if s.cfg.CookieSecure {
		return "__Host-tb_session"
	}
	return "tb_session"
}

func (s *Server) sessionFromRequest(r *http.Request) (*Session, error) {
	c, err := r.Cookie(s.sessionCookieName())
	if err != nil || len(c.Value) < 40 || len(c.Value) > 64 {
		return nil, errNotFound
	}
	return s.store.activeSession(r.Context(), sha256Sum(c.Value), s.cfg.SessionIdle)
}

// startSession issues a new session (fresh ID on every login) and sets the cookie.
func (s *Server) startSession(ctx context.Context, w http.ResponseWriter, rc *reqCtx, ownerID, method string, stepUp policy.StepUpMethod) (*Session, error) {
	tok, err := randomToken(32)
	if err != nil {
		return nil, err
	}
	now := s.now()
	sess := &Session{
		IDHash: sha256Sum(tok), PublicID: ulid.MustNew(ulid.Timestamp(now), rand.Reader).String(), OwnerID: ownerID,
		AuthMethod: method, IdleExpires: now.Add(s.cfg.SessionIdle), AbsExpires: now.Add(s.cfg.SessionAbsolute),
		IP: rc.ip, UserAgent: rc.ua,
	}
	if stepUp != policy.StepUpNone {
		until := now.Add(s.cfg.StepUpWindow)
		sess.StepUpMethod, sess.StepUpUntil = string(stepUp), &until
	}
	if err := s.store.createSession(ctx, sess); err != nil {
		return nil, err
	}
	http.SetCookie(w, &http.Cookie{ //nolint:gosec // Secure is config-driven (off only for plain-http localhost)
		Name: s.sessionCookieName(), Value: tok, Path: "/", Secure: s.cfg.CookieSecure,
		HttpOnly: true, SameSite: http.SameSiteStrictMode, MaxAge: int(s.cfg.SessionAbsolute.Seconds())})
	return sess, nil
}

func (s *Server) clearSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{ //nolint:gosec // see startSession
		Name: s.sessionCookieName(), Value: "", Path: "/", Secure: s.cfg.CookieSecure,
		HttpOnly: true, SameSite: http.SameSiteStrictMode, MaxAge: -1})
}

// ---- authorization ----

// authorize applies policy.Authorize (SE-001) and writes 403 for deny or step-up.
func (s *Server) authorize(w http.ResponseWriter, rc *reqCtx, action policy.Action, res policy.Resource) bool {
	d := policy.Authorize(rc.actor(s.now()), action, res, s.now())
	switch d.Effect {
	case policy.Allow:
		return true
	case policy.RequireStepUp:
		method := "any"
		if policy.RequiresPasskeyStepUp(action, res) {
			method = "passkey"
		}
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "step_up_required", "method": method, "action": string(action)})
		return false
	case policy.Deny:
		writeError(w, http.StatusForbidden, "forbidden", d.Reason)
		return false
	default:
		panic("unhandled policy effect " + string(d.Effect))
	}
}

// ---- audit and alerts ----

// record appends an audit event; callers MUST abort the action when it returns an error (SE-021).
func (s *Server) record(ctx context.Context, rc *reqCtx, actor, action, target string, details map[string]any, before, after string) error {
	_, _, err := s.audit.Append(ctx, audit.Event{
		Actor: actor, Action: action, Target: target, BeforeHash: before, AfterHash: after,
		IP: rc.ip, Device: rc.ua, Details: details,
	})
	if err != nil {
		s.log.ErrorContext(ctx, "audit append failed", "action", action, "error", err)
	}
	return err
}

func (s *Server) securityAlert(ctx context.Context, subject, msg, dedupe string) {
	if s.alerts == nil {
		return
	}
	a := &alertsv1.Alert{
		AlertId: ulid.MustNew(ulid.Now(), rand.Reader).String(), Severity: alertsv1.Severity_SEVERITY_WARNING,
		Source: "api-gateway", Subject: subject, Message: msg, DedupeKey: dedupe,
		RunbookUrl: "docs/runbooks/security-incident.md", OccurredAt: s.now().UnixNano(), CorrelationId: logging.CorrelationID(ctx),
	}
	if err := s.alerts.Publish(ctx, a); err != nil {
		s.log.WarnContext(ctx, "security alert publish failed", "subject", subject, "error", err)
	}
}

// ---- rate limiting (SE-011, per-IP token bucket on /auth/*) ----

func (s *Server) allow(ip string) bool {
	s.limMu.Lock()
	defer s.limMu.Unlock()
	l, ok := s.limiters[ip]
	if !ok {
		if len(s.limiters) > 10000 {
			s.limiters = map[string]*rate.Limiter{}
		}
		l = rate.NewLimiter(rate.Every(s.rateEvery), s.rateBurst)
		s.limiters[ip] = l
	}
	return l.Allow()
}

func (s *Server) clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	addr, err := netip.ParseAddr(host)
	if err != nil || !s.trusted(addr) {
		return host
	}
	parts := strings.Split(r.Header.Get("X-Forwarded-For"), ",")
	for i := len(parts) - 1; i >= 0; i-- {
		p := strings.TrimSpace(parts[i])
		a, err := netip.ParseAddr(p)
		if err != nil {
			break
		}
		if !s.trusted(a) {
			return p
		}
	}
	return host
}

func (s *Server) trusted(a netip.Addr) bool {
	for _, p := range s.proxies {
		if p.Contains(a) {
			return true
		}
	}
	return false
}

// ---- JSON helpers ----

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeError sends {"error","message"}; messages are redacted (SE-012).
func writeError(w http.ResponseWriter, status int, code, msg string) {
	writeJSON(w, status, map[string]string{"error": code, "message": logging.Redact(msg)})
}

func (s *Server) internalError(w http.ResponseWriter, r *http.Request, what string, err error) {
	s.log.ErrorContext(r.Context(), what, "error", err)
	writeError(w, http.StatusInternalServerError, "internal", "internal error")
}

// decode reads a JSON body, rejecting unknown fields.
func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return errors.New("invalid JSON body")
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request, _ *reqCtx) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}
