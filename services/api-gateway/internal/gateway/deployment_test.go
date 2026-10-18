package gateway

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	mlv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ml/v1"
	optv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

type fakeDeployOpt struct {
	optv1.OptimizerServiceClient
	stage mlv1.RegistryStage
	sets  []*optv1.SetDeploymentLimitsRequest
}

func (f *fakeDeployOpt) GetCandidate(_ context.Context, r *optv1.GetCandidateRequest, _ ...grpc.CallOption) (*optv1.GetCandidateResponse, error) {
	entry := `{"strategy_id":"ml-abc","short_id":"mabc1234","name":"lgbm abc","horizon":"swing","broker_account_id":"ibkr-paper-bot",
		"mode":"paper","allocation":"50000","version":"cand_1","limits":{"max_drawdown":"10000","max_consecutive_losses":"15"}}`
	return &optv1.GetCandidateResponse{Candidate: &optv1.Candidate{CandidateId: r.GetCandidateId(), Stage: f.stage, RegistryEntryJson: entry}}, nil
}

func (f *fakeDeployOpt) SetDeploymentLimits(_ context.Context, r *optv1.SetDeploymentLimitsRequest, _ ...grpc.CallOption) (*optv1.SetDeploymentLimitsResponse, error) {
	f.sets = append(f.sets, r)
	if r.GetLimits().GetMaxConsecutiveLosses() > 15 && r.GetCaller().GetStepUp() == nil {
		return nil, status.Error(codes.PermissionDenied, "raising deployment limits needs a passkey step-up (RM-007)")
	}
	return &optv1.SetDeploymentLimitsResponse{Limits: &optv1.DeploymentLimits{LineageId: r.GetLineageId()}, Raised: r.GetLimits().GetMaxConsecutiveLosses() > 15}, nil
}

type fakeRegExec struct {
	fakeExec
	regs []*ordersv1.RegisterStrategyRequest
}

func (f *fakeRegExec) RegisterStrategy(_ context.Context, r *ordersv1.RegisterStrategyRequest, _ ...grpc.CallOption) (*ordersv1.RegisterStrategyResponse, error) {
	f.regs = append(f.regs, r)
	return &ordersv1.RegisterStrategyResponse{Strategy: r.GetStrategy()}, nil
}

func newDeployRig(t *testing.T, stage mlv1.RegistryStage) (*tradingRig, *fakeDeployOpt, *fakeRegExec) {
	t.Helper()
	rig := &tradingRig{ledger: &fakeLedger{alloc: "0"}, now: time.Date(2026, 10, 19, 15, 0, 0, 0, time.UTC)}
	opt, ex := &fakeDeployOpt{stage: stage}, &fakeRegExec{}
	rig.exec = &ex.fakeExec
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Research: &ResearchClients{Opt: opt}, Trading: &TradingClients{Exec: ex, Ledger: rig.ledger, MD: fakeMD{}}})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = func() time.Time { return rig.now }
	rig.srv = srv
	return rig, opt, ex
}

// SM-016, EX-064, RM-019: registering a paper-stage candidate with execution needs a fresh passkey and
// applies its registry entry: strategy (paper, routed account), allocation, and strategy-scope limits.
func TestSM016RegisterCandidateWithExecution(t *testing.T) {
	rig, _, ex := newDeployRig(t, mlv1.RegistryStage_REGISTRY_STAGE_PAPER)
	s := rig.srv
	if rec := rig.call(s.handleCandidateRegister, "POST", "/", ``, desktopUA, "", "id", "cand_1"); rec.Code != 403 || !strings.Contains(rec.Body.String(), "step_up_required") {
		t.Fatalf("register without step-up: %d %s", rec.Code, rec.Body)
	}
	rec := rig.call(s.handleCandidateRegister, "POST", "/", ``, desktopUA, "passkey", "id", "cand_1")
	if rec.Code != 200 {
		t.Fatalf("register with passkey: %d %s", rec.Code, rec.Body)
	}
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	if out["strategy_id"] != "ml-abc" || out["broker_account_id"] != "ibkr-paper-bot" || out["allocation"] != "50000" {
		t.Fatalf("response %v", out)
	}
	st := ex.regs[0].GetStrategy()
	if st.GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_PAPER || st.GetHorizon().String() != "HORIZON_SWING" || !st.GetRuntimeHosted() {
		t.Fatalf("registered %v", st)
	}
	if a := rig.ledger.sets[0]; a.GetOwner() != "ml-abc" || a.GetAmount().GetValue() != "50000" {
		t.Fatalf("allocation %v", a)
	}
	lim := ex.limits[0]
	if lim.GetScope() != "strategy:ml-abc" || lim.GetLimits().GetMaxConsecutiveLosses() != 15 || lim.GetLimits().GetMaxDrawdown().GetValue() != "10000" ||
		lim.GetActor().GetStepUpMethod() != "passkey" {
		t.Fatalf("limits %v", lim)
	}
	rig2, _, _ := newDeployRig(t, mlv1.RegistryStage_REGISTRY_STAGE_VALIDATED)
	if rec := rig2.call(rig2.srv.handleCandidateRegister, "POST", "/", ``, desktopUA, "passkey", "id", "cand_1"); rec.Code != 409 {
		t.Fatalf("validated candidate must not register: %d", rec.Code)
	}
}

// EX-062, RM-007: tightening deployment limits needs no step-up; a raise refused by the optimizer comes
// back as a passkey step-up request.
func TestEX062DeploymentLimitsRaiseNeedsPasskey(t *testing.T) {
	rig, opt, _ := newDeployRig(t, mlv1.RegistryStage_REGISTRY_STAGE_PAPER)
	s := rig.srv
	if rec := rig.call(s.handleDeploymentLimitsPut, "PUT", "/", `{"limits":{"maxConsecutiveLosses":10}}`, desktopUA, "", "id", "lin_1"); rec.Code != 200 {
		t.Fatalf("tighten: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(s.handleDeploymentLimitsPut, "PUT", "/", `{"limits":{"maxConsecutiveLosses":20}}`, desktopUA, "", "id", "lin_1"); rec.Code != 403 ||
		!strings.Contains(rec.Body.String(), "step_up_required") {
		t.Fatalf("raise without step-up: %d %s", rec.Code, rec.Body)
	}
	if rec := rig.call(s.handleDeploymentLimitsPut, "PUT", "/", `{"limits":{"maxConsecutiveLosses":20}}`, desktopUA, "passkey", "id", "lin_1"); rec.Code != 200 {
		t.Fatalf("raise with passkey: %d %s", rec.Code, rec.Body)
	}
	if c := opt.sets[len(opt.sets)-1].GetCaller(); c.GetActor() != mlv1.Actor_ACTOR_OWNER || c.GetStepUp().GetMethod() != "passkey" {
		t.Fatalf("caller %v", c)
	}
}
