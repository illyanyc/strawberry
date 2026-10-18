package gateway

import (
	"context"
	"io"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"

	mlv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ml/v1"
	optv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
)

type fakeML struct {
	mlv1.MLServiceClient
	mu      sync.Mutex
	submits []*mlv1.SubmitTrainingJobRequest
}

func (f *fakeML) SubmitTrainingJob(_ context.Context, r *mlv1.SubmitTrainingJobRequest, _ ...grpc.CallOption) (*mlv1.SubmitTrainingJobResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.submits = append(f.submits, r)
	return &mlv1.SubmitTrainingJobResponse{Job: &mlv1.TrainingJob{JobId: "J1", Config: r.GetConfig()}}, nil
}

func (f *fakeML) GetTearsheet(context.Context, *mlv1.GetTearsheetRequest, ...grpc.CallOption) (*mlv1.GetTearsheetResponse, error) {
	return &mlv1.GetTearsheetResponse{Html: "<!doctype html><svg></svg>", Engine: "event"}, nil
}

type fakeOpt struct {
	optv1.OptimizerServiceClient
	mu       sync.Mutex
	promotes []*optv1.PromoteCandidateRequest
	autonomy []*optv1.SetLineageAutonomyRequest
}

func (f *fakeOpt) PromoteCandidate(_ context.Context, r *optv1.PromoteCandidateRequest, _ ...grpc.CallOption) (*optv1.PromoteCandidateResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.promotes = append(f.promotes, r)
	return &optv1.PromoteCandidateResponse{Decision: &optv1.PermissionDecision{Allowed: true}}, nil
}

func (f *fakeOpt) GetLineage(_ context.Context, r *optv1.GetLineageRequest, _ ...grpc.CallOption) (*optv1.GetLineageResponse, error) {
	return &optv1.GetLineageResponse{Lineage: &optv1.Lineage{LineageId: r.GetLineageId(), AutonomyLevel: 1}}, nil
}

func (f *fakeOpt) SetLineageAutonomy(_ context.Context, r *optv1.SetLineageAutonomyRequest, _ ...grpc.CallOption) (*optv1.SetLineageAutonomyResponse, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.autonomy = append(f.autonomy, r)
	return &optv1.SetLineageAutonomyResponse{Lineage: &optv1.Lineage{LineageId: r.GetLineageId(), AutonomyLevel: r.GetLevel()}}, nil
}

type fakeHold struct {
	optv1.HoldoutServiceClient
	evals []*optv1.EvaluateOnHoldoutRequest
}

func (f *fakeHold) EvaluateOnHoldout(_ context.Context, r *optv1.EvaluateOnHoldoutRequest, _ ...grpc.CallOption) (*optv1.EvaluateOnHoldoutResponse, error) {
	f.evals = append(f.evals, r)
	return &optv1.EvaluateOnHoldoutResponse{Result: &optv1.HoldoutResult{CandidateId: r.GetCandidateId(), Passed: true, Dsr: 0.97}}, nil
}

func newResearchRig(t *testing.T) (*tradingRig, *fakeML, *fakeOpt, *fakeHold) {
	t.Helper()
	cfg := testConfig("http://localhost:8080")
	rig := &tradingRig{now: time.Date(2026, 10, 19, 15, 0, 0, 0, time.UTC)}
	ml, opt, hold := &fakeML{}, &fakeOpt{}, &fakeHold{}
	srv, err := New(cfg, Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Research: &ResearchClients{ML: ml, Opt: opt, Hold: hold}})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = func() time.Time { return rig.now }
	rig.srv = srv
	return rig, ml, opt, hold
}

// EX-030, EX-052, EX-011, EX-033, UI-021: launches need no step-up and forward the owner; promotion to
// paper, holdout evaluation, and raising autonomy need a fresh passkey, which the gateway attests.
func TestEX052ResearchRoutesStepUpAndCaller(t *testing.T) {
	rig, ml, opt, hold := newResearchRig(t)
	s := rig.srv
	if rec := rig.call(s.handleTrainingSubmit, "POST", "/", `{"config":{"family":"ridge","dataSource":"synthetic:intraday_small"}}`, desktopUA, ""); rec.Code != 200 {
		t.Fatalf("submit: %d %s", rec.Code, rec.Body)
	}
	if c := ml.submits[0].GetCaller(); c.GetActor() != mlv1.Actor_ACTOR_OWNER || c.GetStepUp() != nil || c.GetCorrelationId() == "" {
		t.Fatalf("forwarded caller %v", c)
	}
	promote := func(stage, stepUp string) int {
		return rig.call(s.handleCandidatePromote, "POST", "/", `{"toStage":"`+stage+`"}`, desktopUA, stepUp, "id", "cand_1").Code
	}
	if code := promote("shadow", ""); code != 200 {
		t.Fatalf("shadow without step-up: %d", code)
	}
	if code := promote("paper", ""); code != 403 {
		t.Fatalf("paper without step-up: %d", code)
	}
	if code := promote("paper", "totp"); code != 403 {
		t.Fatalf("paper with TOTP step-up: %d", code)
	}
	if code := promote("paper", "passkey"); code != 200 {
		t.Fatalf("paper with passkey: %d", code)
	}
	if code := promote("live", "passkey"); code != 400 {
		t.Fatalf("live must be refused at the gateway (section 15 only): %d", code)
	}
	last := opt.promotes[len(opt.promotes)-1]
	if last.GetToStage() != mlv1.RegistryStage_REGISTRY_STAGE_PAPER || last.GetCaller().GetStepUp().GetMethod() != "passkey" {
		t.Fatalf("paper promotion forwarded as %v", last)
	}
	if opt.promotes[0].GetCaller().GetStepUp() != nil {
		t.Fatal("no step-up attestation without a fresh step-up")
	}
	eval := func(body, stepUp string) int {
		return rig.call(s.handleHoldoutEvaluate, "POST", "/", body, desktopUA, stepUp, "id", "cand_1").Code
	}
	if code := eval(`{"reason":"quarterly"}`, ""); code != 403 {
		t.Fatalf("holdout without step-up: %d", code)
	}
	if code := eval(`{}`, "passkey"); code != 400 {
		t.Fatalf("holdout without a reason: %d", code)
	}
	if code := eval(`{"reason":"quarterly"}`, "passkey"); code != 200 || len(hold.evals) != 1 {
		t.Fatalf("holdout with passkey: %d", code)
	}
	auto := func(level, stepUp string) int {
		return rig.call(s.handleLineageAutonomy, "PUT", "/", `{"level":`+level+`}`, desktopUA, stepUp, "id", "lin_1").Code
	}
	if code := auto("0", ""); code != 200 {
		t.Fatalf("lowering autonomy: %d", code)
	}
	if code := auto("2", ""); code != 403 {
		t.Fatalf("raising autonomy without step-up: %d", code)
	}
	if code := auto("2", "passkey"); code != 200 {
		t.Fatalf("raising autonomy with passkey: %d", code)
	}
	if code := rig.call(s.handleRunTearsheet, "GET", "/", "", desktopUA, "", "id", "../etc").Code; code != 400 {
		t.Fatalf("path traversal id: %d", code)
	}
}

// UI-022: tearsheets are served as their own sandboxed document (no scripts, no external loads).
func TestUI022TearsheetIsSandboxed(t *testing.T) {
	rig, _, _, _ := newResearchRig(t)
	rec := rig.call(rig.srv.handleRunTearsheet, "GET", "/", "", desktopUA, "", "id", "run_1")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "<svg") {
		t.Fatalf("tearsheet: %d %s", rec.Code, rec.Body)
	}
	csp := rec.Header().Get("Content-Security-Policy")
	for _, want := range []string{"default-src 'none'", "sandbox", "frame-ancestors 'none'"} {
		if !strings.Contains(csp, want) {
			t.Fatalf("CSP %q lacks %q", csp, want)
		}
	}
	if strings.Contains(csp, "script-src") {
		t.Fatalf("tearsheet CSP must not allow scripts: %q", csp)
	}
}
