package gateway

import (
	"context"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	monitorv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/monitor/v1"
)

type fakeMon struct {
	monitorv1.MonitorServiceClient
	acks     []*monitorv1.AckAlertRequest
	silences []*monitorv1.SilenceAlertRequest
	policies []*monitorv1.SetContainmentPolicyRequest
	grads    int
}

func (f *fakeMon) ListStrategyHealth(context.Context, *monitorv1.ListStrategyHealthRequest, ...grpc.CallOption) (*monitorv1.ListStrategyHealthResponse, error) {
	return &monitorv1.ListStrategyHealthResponse{Strategies: []*monitorv1.StrategyHealth{{StrategyId: "orb-15m", Score: 88, Status: monitorv1.HealthStatus_HEALTH_STATUS_OK}}}, nil
}

func (f *fakeMon) GetGraduationEvidence(_ context.Context, r *monitorv1.GetGraduationEvidenceRequest, _ ...grpc.CallOption) (*monitorv1.GetGraduationEvidenceResponse, error) {
	f.grads++
	return &monitorv1.GetGraduationEvidenceResponse{Evidence: &monitorv1.GraduationEvidence{StrategyId: r.GetStrategyId(), Sessions: 5}}, nil
}

func (f *fakeMon) AckAlert(_ context.Context, r *monitorv1.AckAlertRequest, _ ...grpc.CallOption) (*monitorv1.AckAlertResponse, error) {
	f.acks = append(f.acks, r)
	return &monitorv1.AckAlertResponse{Alert: &monitorv1.AlertRecord{AckedBy: r.GetActor().GetName()}}, nil
}

func (f *fakeMon) SilenceAlert(_ context.Context, r *monitorv1.SilenceAlertRequest, _ ...grpc.CallOption) (*monitorv1.SilenceAlertResponse, error) {
	f.silences = append(f.silences, r)
	return &monitorv1.SilenceAlertResponse{SilenceId: "sil-1"}, nil
}

func (f *fakeMon) SetContainmentPolicy(_ context.Context, r *monitorv1.SetContainmentPolicyRequest, _ ...grpc.CallOption) (*monitorv1.SetContainmentPolicyResponse, error) {
	f.policies = append(f.policies, r)
	if r.GetPolicy().GetDrawdownBreachMultiple() > 1.5 && r.GetActor().GetStepUpMethod() != "passkey" {
		return nil, status.Error(codes.PermissionDenied, "loosening drawdown_breach_multiple needs a passkey step-up (MN-036)")
	}
	return &monitorv1.SetContainmentPolicyResponse{Policy: r.GetPolicy(), Loosened: r.GetPolicy().GetDrawdownBreachMultiple() > 1.5}, nil
}

func newMonRig(t *testing.T) (*tradingRig, *fakeMon) {
	t.Helper()
	rig := &tradingRig{now: time.Date(2026, 10, 19, 15, 0, 0, 0, time.UTC)}
	m := &fakeMon{}
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Monitor: &MonitorClients{Monitor: m}})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = func() time.Time { return rig.now }
	rig.srv = srv
	return rig, m
}

// MN-036, MN-042, MN-040, UI-028: monitoring views and acks need no step-up; silencing and loosening a
// containment policy need a passkey; the owner actor is forwarded; unknown reports are 404.
func TestMN036MonitoringRoutesStepUpAndActor(t *testing.T) {
	rig, m := newMonRig(t)
	s := rig.srv
	if rec := rig.call(s.handleMonStrategies, "GET", "/", ``, desktopUA, ""); rec.Code != 200 || !strings.Contains(rec.Body.String(), "orb-15m") {
		t.Fatalf("strategies: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(s.handleMonReport, "GET", "/", ``, desktopUA, "", "id", "orb-15m", "report", "graduation"); rec.Code != 200 || m.grads != 1 {
		t.Fatalf("graduation: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(s.handleMonReport, "GET", "/", ``, desktopUA, "", "id", "orb-15m", "report", "secrets"); rec.Code != 404 {
		t.Fatalf("unknown report: %d", rec.Code)
	}
	if rec := rig.call(s.handleMonAck, "POST", "/", `{"note":"seen"}`, desktopUA, "", "key", "ibkr-paper-main-session"); rec.Code != 200 || m.acks[0].GetActor().GetName() != "owner" {
		t.Fatalf("ack: %d %s", rec.Code, rec.Body)
	}
	body := `{"durationSeconds":3600,"reason":"IB Gateway maintenance"}`
	if rec := rig.call(s.handleMonSilence, "POST", "/", body, desktopUA, "", "key", "prom:abc"); rec.Code != 403 || !strings.Contains(rec.Body.String(), "step_up_required") {
		t.Fatalf("silence without step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(s.handleMonSilence, "POST", "/", `{"durationSeconds":172800,"reason":"x"}`, desktopUA, "passkey", "key", "prom:abc"); rec.Code != 400 {
		t.Fatalf("silence longer than 24 h: %d", rec.Code)
	}
	if rec := rig.call(s.handleMonSilence, "POST", "/", body, desktopUA, "passkey", "key", "prom:abc"); rec.Code != 200 || len(m.silences) != 1 {
		t.Fatalf("silence with passkey: %d %s", rec.Code, rec.Body)
	}
	tight := `{"policy":{"enabled":true,"drawdownWarningMultiple":1,"drawdownBreachMultiple":1.2}}`
	if rec := rig.call(s.handleMonPolicyPut, "PUT", "/", tight, desktopUA, "", "id", "orb-15m"); rec.Code != 200 {
		t.Fatalf("tighten: %d %s", rec.Code, rec.Body)
	}
	loose := `{"policy":{"enabled":true,"drawdownWarningMultiple":1,"drawdownBreachMultiple":3}}`
	if rec := rig.call(s.handleMonPolicyPut, "PUT", "/", loose, desktopUA, "", "id", "orb-15m"); rec.Code != 403 || !strings.Contains(rec.Body.String(), "step_up_required") {
		t.Fatalf("loosen without step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(s.handleMonPolicyPut, "PUT", "/", loose, desktopUA, "passkey", "id", "default"); rec.Code != 200 {
		t.Fatalf("loosen the default with passkey: %d %s", rec.Code, rec.Body)
	}
	if last := m.policies[len(m.policies)-1]; last.GetPolicy().GetStrategyId() != "" || last.GetActor().GetStepUpMethod() != "passkey" {
		t.Fatalf("default policy forwarded as %v", last)
	}
}
