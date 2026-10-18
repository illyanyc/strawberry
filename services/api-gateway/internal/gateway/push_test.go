package gateway

import (
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/services/api-gateway/internal/webpush"
)

type memPushStore struct {
	mu   sync.Mutex
	subs map[string]PushSub
}

func (m *memPushStore) Upsert(_ context.Context, s PushSub) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for id, p := range m.subs {
		if p.EndpointHash == s.EndpointHash {
			s.ID = id
		}
	}
	s.CreatedAt = time.Now()
	m.subs[s.ID] = s
	return s.ID, nil
}

func (m *memPushStore) List(_ context.Context, owner string) ([]PushSub, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []PushSub{}
	for _, p := range m.subs {
		if p.OwnerID == owner {
			out = append(out, p)
		}
	}
	return out, nil
}

func (m *memPushStore) Delete(_ context.Context, owner, id string) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p, ok := m.subs[id]
	if ok && p.OwnerID == owner {
		delete(m.subs, id)
		return true, nil
	}
	return false, nil
}

func (m *memPushStore) Result(_ context.Context, id string, ok bool, errText string, at time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	p := m.subs[id]
	if ok {
		p.LastSuccessAt, p.Failures, p.LastError = &at, 0, ""
	} else {
		p.Failures, p.LastError = p.Failures+1, errText
	}
	m.subs[id] = p
	return nil
}

type fakePusher struct {
	mu   sync.Mutex
	sent []webpush.Message
	subs []webpush.Subscription
	err  map[string]error
}

func (f *fakePusher) Send(_ context.Context, sub webpush.Subscription, m webpush.Message) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if e, ok := f.err[sub.Endpoint]; ok {
		return e
	}
	f.sent = append(f.sent, m)
	f.subs = append(f.subs, sub)
	return nil
}

type pushRig struct {
	srv    *Server
	store  *memPushStore
	pusher *fakePusher
	audits []audit.Event
}

func newPushRig(t *testing.T, redact bool) *pushRig {
	t.Helper()
	rig := &pushRig{store: &memPushStore{subs: map[string]PushSub{}}, pusher: &fakePusher{err: map[string]error{}}}
	srv, err := New(testConfig("https://mini.example.ts.net"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Push: PushDeps{Store: rig.store, Pusher: rig.pusher, Public: "BPUB", Redact: redact, AMToken: []byte("am-token")}})
	if err != nil {
		t.Fatal(err)
	}
	srv.appendAudit = func(_ context.Context, e audit.Event) (int64, string, error) {
		rig.audits = append(rig.audits, e)
		return 1, "h", nil
	}
	rig.srv = srv
	return rig
}

func (rig *pushRig) call(h func(http.ResponseWriter, *http.Request, *reqCtx), method, body string, pv ...string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, "/", strings.NewReader(body))
	for i := 0; i+1 < len(pv); i += 2 {
		req.SetPathValue(pv[i], pv[i+1])
	}
	rec := httptest.NewRecorder()
	h(rec, req, &reqCtx{session: &Session{OwnerID: "owner", IdleExpires: time.Now().Add(time.Hour)}, ip: "127.0.0.1"})
	return rec
}

func subBody(t *testing.T, endpoint string) string {
	t.Helper()
	k, _ := ecdh.P256().GenerateKey(rand.Reader)
	auth := make([]byte, 16)
	_, _ = rand.Read(auth)
	b, _ := json.Marshal(map[string]string{"endpoint": endpoint, "p256dh": base64.RawURLEncoding.EncodeToString(k.PublicKey().Bytes()),
		"auth": base64.RawURLEncoding.EncodeToString(auth), "label": "Chrome on macOS"})
	return string(b)
}

// SE-060, SE-061: subscriptions are validated, sealed at rest, listed without the endpoint, audited.
func TestSE061PushSubscriptionsAreValidatedSealedAndAudited(t *testing.T) {
	rig := newPushRig(t, false)
	s := rig.srv
	for name, body := range map[string]string{
		"ssrf endpoint": subBody(t, "https://169.254.169.254/latest"),
		"http endpoint": subBody(t, "http://fcm.googleapis.com/fcm/send/x"),
		"short key":     `{"endpoint":"https://fcm.googleapis.com/fcm/send/x","p256dh":"AAAA","auth":"AAAAAAAAAAAAAAAAAAAAAA","label":""}`,
		"unknown field": `{"endpoint":"https://fcm.googleapis.com/x","p256dh":"","auth":"","label":"","extra":1}`,
	} {
		if rec := rig.call(s.handlePushSubscribe, "POST", body); rec.Code != 400 {
			t.Errorf("%s: %d %s", name, rec.Code, rec.Body)
		}
	}
	ep := "https://fcm.googleapis.com/fcm/send/device-1"
	rec := rig.call(s.handlePushSubscribe, "POST", subBody(t, ep))
	if rec.Code != 200 {
		t.Fatalf("subscribe: %d %s", rec.Code, rec.Body)
	}
	for _, p := range rig.store.subs {
		if strings.Contains(string(p.Sealed), "device-1") || p.Service != "fcm.googleapis.com" || p.EndpointHash != webpush.EndpointHash(ep) {
			t.Fatalf("stored row leaks or misses fields: %+v", p)
		}
	}
	// Resubscribing the same endpoint keeps one row.
	if rec := rig.call(s.handlePushSubscribe, "POST", subBody(t, ep)); rec.Code != 200 || len(rig.store.subs) != 1 {
		t.Fatalf("resubscribe: %d rows=%d", rec.Code, len(rig.store.subs))
	}
	list := rig.call(s.handlePushList, "GET", "")
	if strings.Contains(list.Body.String(), "device-1") || !strings.Contains(list.Body.String(), `"endpoint_hash"`) {
		t.Fatalf("list must not return the endpoint: %s", list.Body)
	}
	if len(rig.audits) == 0 || rig.audits[0].Action != "push.subscribe" {
		t.Fatalf("subscribe not audited: %+v", rig.audits)
	}
	var id string
	for k := range rig.store.subs {
		id = k
	}
	if rec := rig.call(s.handlePushDelete, "DELETE", "", "id", id); rec.Code != 204 || len(rig.store.subs) != 0 {
		t.Fatalf("delete: %d", rec.Code)
	}
	if rec := rig.call(s.handlePushDelete, "DELETE", "", "id", id); rec.Code != 404 {
		t.Fatalf("delete again: %d", rec.Code)
	}
}

// SE-063: the Alertmanager webhook needs its bearer token and fans a group out to every device;
// gone subscriptions are removed, failures recorded without the endpoint.
func TestSE063AlertmanagerWebhookDeliversToEveryDevice(t *testing.T) {
	rig := newPushRig(t, false)
	s := rig.srv
	for _, ep := range []string{"https://fcm.googleapis.com/fcm/send/a", "https://web.push.apple.com/b", "https://updates.push.services.mozilla.com/wpush/v2/c"} {
		if rec := rig.call(s.handlePushSubscribe, "POST", subBody(t, ep)); rec.Code != 200 {
			t.Fatal(rec.Body)
		}
	}
	rig.pusher.err["https://web.push.apple.com/b"] = webpush.ErrGone
	rig.pusher.err["https://updates.push.services.mozilla.com/wpush/v2/c"] = io.ErrUnexpectedEOF
	payload := `{"version":"4","groupKey":"{}:{alertname=\"LedgerReconciliationBroken\"}","status":"firing",
		"commonLabels":{"alertname":"LedgerReconciliationBroken","severity":"critical"},
		"alerts":[{"status":"firing","labels":{"alertname":"LedgerReconciliationBroken","severity":"critical"},"annotations":{"summary":"Reconciliation broken on alpaca-paper"}}]}`
	h := s.InternalHandler()
	post := func(tok string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/internal/alertmanager/webhook", strings.NewReader(payload))
		if tok != "" {
			req.Header.Set("Authorization", "Bearer "+tok)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		return rec
	}
	if rec := post(""); rec.Code != 401 {
		t.Fatalf("no token: %d", rec.Code)
	}
	if rec := post("wrong"); rec.Code != 401 {
		t.Fatalf("wrong token: %d", rec.Code)
	}
	rec := post("am-token")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"sent":1`) || !strings.Contains(rec.Body.String(), `"failed":2`) {
		t.Fatalf("deliver: %d %s", rec.Code, rec.Body)
	}
	m := rig.pusher.sent[0]
	if m.Urgency != webpush.UrgencyHigh || m.TTL != time.Hour || len(m.Topic) != 32 {
		t.Fatalf("critical message options: %+v", m)
	}
	var body struct {
		WebPush      int `json:"web_push"`
		Notification struct {
			Title, Body, Navigate string
			RequireInteraction    bool `json:"require_interaction"`
		} `json:"notification"`
	}
	if err := json.Unmarshal(m.Payload, &body); err != nil {
		t.Fatal(err)
	}
	if body.WebPush != 8030 || body.Notification.Title != "[CRITICAL] LedgerReconciliationBroken" || !body.Notification.RequireInteraction ||
		body.Notification.Navigate != "https://mini.example.ts.net/" || !strings.Contains(body.Notification.Body, "alpaca-paper") {
		t.Fatalf("payload %+v", body)
	}
	if len(rig.store.subs) != 2 {
		t.Fatalf("gone subscription must be deleted, have %d", len(rig.store.subs))
	}
	for _, p := range rig.store.subs {
		if p.Failures == 1 && (p.LastError == "" || strings.Contains(p.LastError, "mozilla")) {
			t.Fatalf("failure recorded badly: %q", p.LastError)
		}
	}
	// info alerts are not pushed.
	info := strings.ReplaceAll(payload, "critical", "info")
	req := httptest.NewRequest("POST", "/internal/alertmanager/webhook", strings.NewReader(info))
	req.Header.Set("Authorization", "Bearer am-token")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 204 {
		t.Fatalf("info alert: %d", rec.Code)
	}
}

// SE-062: with lock-screen redaction, summaries never reach the notification.
func TestSE062RedactedNotificationsCarryNoSummary(t *testing.T) {
	p := amPayload{GroupKey: "g", Status: "firing", CommonLabels: map[string]string{"alertname": "BrokerSessionNotReady", "severity": "warning"},
		Alerts: []amAlert{{Annotations: map[string]string{"summary": "alpaca-paper session down; account ****1234"}}}}
	n, ok := notificationFor(p, true)
	if !ok || strings.Contains(n.Body, "alpaca") || strings.Contains(n.Body, "1234") || n.Title != "[WARNING] BrokerSessionNotReady" {
		t.Fatalf("redacted: %+v", n)
	}
	n, _ = notificationFor(p, false)
	if !strings.Contains(n.Body, "alpaca-paper session down") {
		t.Fatalf("unredacted: %+v", n)
	}
	p.Status = "resolved"
	if n, _ := notificationFor(p, true); n.Title != "Resolved: BrokerSessionNotReady" {
		t.Fatalf("resolved: %+v", n)
	}
	report := amPayload{CommonLabels: map[string]string{"alertname": "EndOfDaySummary", "kind": "report", "severity": "info"}}
	if n, ok := notificationFor(report, false); !ok || n.Severity != "info" {
		t.Fatalf("daily reports are pushed at normal urgency: %+v %v", n, ok)
	}
	f := fillNotification(&ordersv1.ExecutionReport{ClientOrderId: "tb1-orb15aa-01J00000000000000000000000-1", Side: commonv1.Side_SIDE_BUY,
		InstrumentId: "I1", Qty: &commonv1.Decimal{Value: "10"}, Price: &commonv1.Decimal{Value: "101.25"}, BrokerExecId: "e1"}, false)
	if f.Title != "Fill: orb15aa buy" || f.Body != "10 I1 @ 101.25" || f.Path != "/orders" {
		t.Fatalf("fill: %+v", f)
	}
	if f := fillNotification(&ordersv1.ExecutionReport{ClientOrderId: "abc", Side: commonv1.Side_SIDE_SELL}, true); f.Title != "Fill: manual sell" || strings.Contains(f.Body, "@") {
		t.Fatalf("redacted manual fill: %+v", f)
	}
}

func TestPushDisabledWithoutVAPIDKey(t *testing.T) {
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Push: PushDeps{Store: &memPushStore{subs: map[string]PushSub{}}}})
	if err != nil {
		t.Fatal(err)
	}
	rig := &pushRig{srv: srv}
	if rec := rig.call(srv.handlePushConfig, "GET", ""); !strings.Contains(rec.Body.String(), `"enabled":false`) {
		t.Fatalf("config: %s", rec.Body)
	}
	if rec := rig.call(srv.handlePushTest, "POST", "{}"); rec.Code != 503 {
		t.Fatalf("test without key: %d", rec.Code)
	}
}
