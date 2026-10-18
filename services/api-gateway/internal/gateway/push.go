package gateway

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/oklog/ulid/v2"
	"google.golang.org/protobuf/proto"

	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/webpush"
)

// PushSub is one stored Web Push subscription (SE-061). Endpoint and keys are sealed at rest.
type PushSub struct {
	ID            string
	OwnerID       string
	EndpointHash  string
	Service       string
	Label         string
	CreatedAt     time.Time
	LastSuccessAt *time.Time
	LastError     string
	Failures      int
	Sealed        []byte
}

// PushStore persists subscriptions (Postgres in production, a map in tests).
type PushStore interface {
	Upsert(ctx context.Context, s PushSub) (string, error)
	List(ctx context.Context, ownerID string) ([]PushSub, error)
	Delete(ctx context.Context, ownerID, id string) (bool, error)
	Result(ctx context.Context, id string, ok bool, errText string, at time.Time) error
}

// Pusher is the delivery side (webpush.Sender in production).
type Pusher interface {
	Send(ctx context.Context, sub webpush.Subscription, m webpush.Message) error
}

// PushDeps configure Web Push; a nil Pusher disables it (no VAPID key).
type PushDeps struct {
	Store   PushStore
	Pusher  Pusher
	Public  string // VAPID public key, base64url
	Redact  bool
	Hosts   []string
	AMToken []byte // bearer token Alertmanager presents on the internal webhook
	Metrics func(result string)
}

type sealedPush struct {
	Endpoint string `json:"e"`
	P256dh   []byte `json:"p"`
	Auth     []byte `json:"a"`
}

func (s *Server) pushEnabled() bool { return s.push.Pusher != nil && s.push.Store != nil }

func (s *Server) registerPushRoutes(priv func(m, p string, h func(http.ResponseWriter, *http.Request, *reqCtx))) {
	priv("GET", "/api/v1/push/config", s.handlePushConfig)
	priv("GET", "/api/v1/push/subscriptions", s.handlePushList)
	priv("POST", "/api/v1/push/subscriptions", s.handlePushSubscribe)
	priv("DELETE", "/api/v1/push/subscriptions/{id}", s.handlePushDelete)
	priv("POST", "/api/v1/push/test", s.handlePushTest)
}

func (s *Server) handlePushConfig(w http.ResponseWriter, _ *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActNotificationsManage, policy.Resource{}) {
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"enabled": s.pushEnabled(), "vapid_public_key": s.push.Public, "redact": s.push.Redact})
}

func (s *Server) handlePushList(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActNotificationsManage, policy.Resource{}) {
		return
	}
	out := []map[string]any{}
	if s.push.Store != nil {
		subs, err := s.push.Store.List(r.Context(), rc.session.OwnerID)
		if err != nil {
			s.internalError(w, r, "push list", err)
			return
		}
		for _, p := range subs {
			out = append(out, map[string]any{"id": p.ID, "label": p.Label, "service": p.Service, "endpoint_hash": p.EndpointHash,
				"created_at": p.CreatedAt, "last_success_at": p.LastSuccessAt, "last_error": p.LastError, "failures": p.Failures})
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"subscriptions": out})
}

type pushSubscribeReq struct {
	Endpoint string `json:"endpoint"`
	P256dh   string `json:"p256dh"`
	Auth     string `json:"auth"`
	Label    string `json:"label"`
}

// handlePushSubscribe stores a browser's subscription. The endpoint must be https on an allowed
// push service (SE-061: no SSRF through the gateway); keys must have the RFC 8291 sizes.
func (s *Server) handlePushSubscribe(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActNotificationsManage, policy.Resource{}) {
		return
	}
	if !s.pushEnabled() {
		writeError(w, http.StatusServiceUnavailable, "push_disabled", "Web Push is not configured (VAPID key missing)")
		return
	}
	var req pushSubscribeReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	p256, err1 := base64.RawURLEncoding.DecodeString(strings.TrimRight(req.P256dh, "="))
	auth, err2 := base64.RawURLEncoding.DecodeString(strings.TrimRight(req.Auth, "="))
	if !webpush.AllowedEndpoint(req.Endpoint, s.push.Hosts) {
		writeError(w, http.StatusBadRequest, "bad_endpoint", "the endpoint is not an https URL on an allowed push service")
		return
	}
	if err1 != nil || err2 != nil || len(p256) != 65 || p256[0] != 4 || len(auth) != 16 {
		writeError(w, http.StatusBadRequest, "bad_request", "p256dh must be a 65-byte uncompressed P-256 point and auth 16 bytes (base64url)")
		return
	}
	raw, _ := json.Marshal(sealedPush{Endpoint: req.Endpoint, P256dh: p256, Auth: auth})
	hash := webpush.EndpointHash(req.Endpoint)
	sealed, err := seal(s.keys.pushEnc, raw, "tb1-push|"+rc.session.OwnerID+"|"+hash)
	if err != nil {
		s.internalError(w, r, "push seal", err)
		return
	}
	label := truncate(strings.TrimSpace(req.Label), 60)
	id, err := s.push.Store.Upsert(r.Context(), PushSub{ID: ulid.MustNew(ulid.Timestamp(s.now()), rand.Reader).String(), OwnerID: rc.session.OwnerID,
		EndpointHash: hash, Service: webpush.ServiceHost(req.Endpoint), Label: label, Sealed: sealed})
	if err != nil {
		s.internalError(w, r, "push store", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "push.subscribe", "push:"+id, map[string]any{"service": webpush.ServiceHost(req.Endpoint), "label": label}, "", ""); err != nil {
		_, _ = s.push.Store.Delete(r.Context(), rc.session.OwnerID, id)
		writeError(w, http.StatusInternalServerError, "audit_failed", "audit log unavailable; nothing was changed")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id})
}

func (s *Server) handlePushDelete(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid subscription id")
		return
	}
	if !s.authorize(w, rc, policy.ActNotificationsManage, policy.Resource{ID: id}) || s.push.Store == nil {
		return
	}
	if err := s.record(r.Context(), rc, "owner", "push.unsubscribe", "push:"+id, nil, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_failed", "audit log unavailable; nothing was changed")
		return
	}
	ok, err := s.push.Store.Delete(r.Context(), rc.session.OwnerID, id)
	if err != nil {
		s.internalError(w, r, "push delete", err)
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "no such subscription")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handlePushTest(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActNotificationsManage, policy.Resource{}) {
		return
	}
	if !s.pushEnabled() {
		writeError(w, http.StatusServiceUnavailable, "push_disabled", "Web Push is not configured (VAPID key missing)")
		return
	}
	n := notification{Title: "Strawberry test notification", Body: "Web Push works on this device.", Tag: "tb-test", Severity: "info"}
	sent, failed := s.deliver(r.Context(), rc.session.OwnerID, n)
	writeJSON(w, http.StatusOK, map[string]int{"sent": sent, "failed": failed})
}

// notification is what the service worker (or Safari's declarative push) displays.
type notification struct {
	Title    string
	Body     string
	Tag      string
	Severity string
	Path     string
}

// payload builds the Declarative Web Push message (`web_push: 8030`), which Safari 18.4+ shows
// without a service worker and other browsers hand to public/sw.js (SE-062: no secrets in it).
func (s *Server) payload(n notification) []byte {
	path := n.Path
	if path == "" {
		path = "/"
	}
	b, _ := json.Marshal(map[string]any{"web_push": 8030, "notification": map[string]any{
		"title": n.Title, "body": n.Body, "navigate": strings.TrimRight(s.cfg.Origins[0], "/") + path, "tag": n.Tag,
		"require_interaction": n.Severity == "critical", "timestamp": s.now().UnixMilli(), "lang": "en-US", "dir": "ltr",
	}})
	return b
}

// deliver pushes one notification to every subscription of the owner, in parallel. Gone
// subscriptions are deleted; failures are recorded per subscription without the endpoint.
func (s *Server) deliver(ctx context.Context, ownerID string, n notification) (sent, failed int) {
	subs, err := s.push.Store.List(ctx, ownerID)
	if err != nil {
		s.log.WarnContext(ctx, "push list failed", "error", err)
		return 0, 0
	}
	msg := webpush.Message{Payload: s.payload(n), TTL: 4 * time.Hour, Urgency: webpush.UrgencyNormal, Topic: topic(n.Tag)}
	if n.Severity == "critical" {
		msg.TTL, msg.Urgency = time.Hour, webpush.UrgencyHigh
	}
	var mu sync.Mutex
	var wg sync.WaitGroup
	for _, p := range subs {
		wg.Go(func() {
			ok := s.sendOne(ctx, p, msg)
			mu.Lock()
			if ok {
				sent++
			} else {
				failed++
			}
			mu.Unlock()
		})
	}
	wg.Wait()
	return sent, failed
}

func (s *Server) sendOne(ctx context.Context, p PushSub, msg webpush.Message) bool {
	raw, err := openSealed(s.keys.pushEnc, p.Sealed, "tb1-push|"+p.OwnerID+"|"+p.EndpointHash)
	var sp sealedPush
	if err == nil {
		err = json.Unmarshal(raw, &sp)
	}
	if err != nil {
		s.pushMetric("error")
		_ = s.push.Store.Result(ctx, p.ID, false, "stored subscription unreadable", s.now())
		return false
	}
	cctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	err = s.push.Pusher.Send(cctx, webpush.Subscription{Endpoint: sp.Endpoint, P256dh: sp.P256dh, Auth: sp.Auth}, msg)
	switch {
	case err == nil:
		s.pushMetric("ok")
		_ = s.push.Store.Result(ctx, p.ID, true, "", s.now())
		return true
	case errors.Is(err, webpush.ErrGone):
		s.pushMetric("gone")
		_, _ = s.push.Store.Delete(ctx, p.OwnerID, p.ID)
		return false
	default:
		s.pushMetric("error")
		_ = s.push.Store.Result(ctx, p.ID, false, truncate(err.Error(), 200), s.now())
		return false
	}
}

func (s *Server) pushMetric(result string) {
	if s.push.Metrics != nil {
		s.push.Metrics(result)
	}
}

// topic derives an RFC 8030 Topic (≤ 32 URL-safe chars) from a tag, so a newer state of the same
// alert group replaces an undelivered older one.
func topic(tag string) string {
	if tag == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(tag))
	return base64.RawURLEncoding.EncodeToString(sum[:])[:32]
}

// StartFillNotifier pushes a notification per execution while `alerts.push_fills_enabled` is on
// (section 14: fills are configurable). It reads the same exec.> subjects as the chart stream.
func (s *Server) StartFillNotifier(ctx context.Context) {
	if s.market == nil || !s.pushEnabled() {
		return
	}
	unsub, err := s.market.Subscribe("exec.>", func(data []byte) {
		var e ordersv1.ExecutionReport
		if proto.Unmarshal(data, &e) != nil {
			return
		}
		go s.notifyFill(ctx, &e)
	})
	if err != nil {
		s.log.Warn("fill notifier disabled", "error", err)
		return
	}
	go func() {
		<-ctx.Done()
		unsub()
	}()
}

func (s *Server) notifyFill(ctx context.Context, e *ordersv1.ExecutionReport) {
	if _, fills := s.ownerFlags(ctx); !fills {
		return
	}
	n := fillNotification(e, s.push.Redact)
	_, _ = s.deliver(ctx, s.cfg.OwnerID, n)
}

// fillNotification describes an execution: owner (attribution short ID or manual), side, quantity,
// instrument, and price; redaction leaves only "a fill was booked".
func fillNotification(e *ordersv1.ExecutionReport, redact bool) notification {
	owner := "manual"
	if parts := strings.Split(e.GetClientOrderId(), "-"); len(parts) >= 3 && parts[0] == "tb1" {
		owner = parts[1]
	}
	side := strings.ToLower(strings.TrimPrefix(e.GetSide().String(), "SIDE_"))
	n := notification{Title: "Fill: " + owner + " " + side, Tag: "fill-" + e.GetBrokerExecId(), Severity: "info", Path: "/orders"}
	if redact {
		n.Body = "A fill was booked. Open Strawberry for details."
		return n
	}
	n.Body = fmt.Sprintf("%s %s @ %s", e.GetQty().GetValue(), e.GetInstrumentId(), e.GetPrice().GetValue())
	return n
}

// ---- Alertmanager webhook (internal listener only) ----

type amAlert struct {
	Status      string            `json:"status"`
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	Fingerprint string            `json:"fingerprint"`
}

type amPayload struct {
	Version      string            `json:"version"`
	GroupKey     string            `json:"groupKey"`
	Status       string            `json:"status"`
	CommonLabels map[string]string `json:"commonLabels"`
	Alerts       []amAlert         `json:"alerts"`
}

// notificationFor turns an Alertmanager group into one notification (SE-062). With redaction on,
// only the severity and alert name are shown; summaries stay in the app and in Pushover.
func notificationFor(p amPayload, redact bool) (notification, bool) {
	sev := p.CommonLabels["severity"]
	kind := p.CommonLabels["kind"]
	if sev == "" && len(p.Alerts) > 0 {
		sev = p.Alerts[0].Labels["severity"]
	}
	if kind != "report" && sev != "critical" && sev != "warning" {
		return notification{}, false
	}
	name := p.CommonLabels["alertname"]
	if name == "" && len(p.Alerts) > 0 {
		name = p.Alerts[0].Labels["alertname"]
	}
	title := fmt.Sprintf("[%s] %s", strings.ToUpper(sev), name)
	if kind == "report" {
		title = name
		sev = "info"
	}
	if p.Status == "resolved" {
		title = "Resolved: " + name
	}
	var lines []string
	if !redact {
		seen := map[string]bool{}
		for _, a := range p.Alerts {
			sum := strings.TrimSpace(a.Annotations["summary"])
			if sum != "" && !seen[sum] {
				seen[sum] = true
				lines = append(lines, sum)
			}
		}
		sort.Strings(lines)
		if len(lines) > 3 {
			lines = append(lines[:3], fmt.Sprintf("and %d more", len(lines)-3))
		}
	} else {
		lines = []string{fmt.Sprintf("%d alert(s). Open Strawberry for details.", len(p.Alerts))}
	}
	return notification{Title: truncate(title, 120), Body: truncate(strings.Join(lines, "\n"), 400), Tag: "am-" + topic("am|" + p.GroupKey + "|" + name)[:16], Severity: sev}, true
}

// InternalHandler serves the internal listener (backend network only, never published or
// tunnelled): the Alertmanager webhook that fans alerts out as Web Push (D13, SE-063).
func (s *Server) InternalHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /internal/alertmanager/webhook", s.handleAlertmanagerWebhook)
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})
	return mux
}

func (s *Server) handleAlertmanagerWebhook(w http.ResponseWriter, r *http.Request) {
	tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if len(s.push.AMToken) == 0 || subtle.ConstantTimeCompare([]byte(tok), s.push.AMToken) != 1 {
		writeError(w, http.StatusUnauthorized, "unauthenticated", "bad webhook token")
		return
	}
	var p amPayload
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&p); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid Alertmanager payload")
		return
	}
	n, ok := notificationFor(p, s.push.Redact)
	if !ok || !s.pushEnabled() {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	sent, failed := s.deliver(r.Context(), s.cfg.OwnerID, n)
	writeJSON(w, http.StatusOK, map[string]int{"sent": sent, "failed": failed})
}

// ---- Postgres store ----

// PGPushStore is the auth.push_subscriptions store.
type PGPushStore struct{ Pool *pgxpool.Pool }

// Upsert inserts or, for a known endpoint, refreshes the keys and label (keeping the ID).
func (p PGPushStore) Upsert(ctx context.Context, s PushSub) (string, error) {
	var id string
	err := p.Pool.QueryRow(ctx, `INSERT INTO auth.push_subscriptions (id, owner_id, endpoint_hash, service, sealed, label)
		VALUES ($1,$2,$3,$4,$5,$6)
		ON CONFLICT (endpoint_hash) DO UPDATE SET sealed=EXCLUDED.sealed, label=EXCLUDED.label, service=EXCLUDED.service,
			owner_id=EXCLUDED.owner_id, failures=0, last_error=''
		RETURNING id`, s.ID, s.OwnerID, s.EndpointHash, s.Service, s.Sealed, s.Label).Scan(&id)
	return id, err
}

func (p PGPushStore) List(ctx context.Context, ownerID string) ([]PushSub, error) {
	rows, err := p.Pool.Query(ctx, `SELECT id, owner_id, endpoint_hash, service, sealed, label, created_at, last_success_at, last_error, failures
		FROM auth.push_subscriptions WHERE owner_id=$1 ORDER BY created_at`, ownerID)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, func(r pgx.CollectableRow) (PushSub, error) {
		var s PushSub
		err := r.Scan(&s.ID, &s.OwnerID, &s.EndpointHash, &s.Service, &s.Sealed, &s.Label, &s.CreatedAt, &s.LastSuccessAt, &s.LastError, &s.Failures)
		return s, err
	})
}

func (p PGPushStore) Delete(ctx context.Context, ownerID, id string) (bool, error) {
	tag, err := p.Pool.Exec(ctx, `DELETE FROM auth.push_subscriptions WHERE owner_id=$1 AND id=$2`, ownerID, id)
	return tag.RowsAffected() > 0, err
}

func (p PGPushStore) Result(ctx context.Context, id string, ok bool, errText string, at time.Time) error {
	if ok {
		_, err := p.Pool.Exec(ctx, `UPDATE auth.push_subscriptions SET last_success_at=$2, failures=0, last_error='' WHERE id=$1`, id, at)
		return err
	}
	_, err := p.Pool.Exec(ctx, `UPDATE auth.push_subscriptions SET failures=failures+1, last_error=$2 WHERE id=$1`, id, errText)
	return err
}
