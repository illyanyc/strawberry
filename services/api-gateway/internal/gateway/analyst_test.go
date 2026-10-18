package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"

	analystv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/analyst/v1"
	mlv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ml/v1"
	optimizerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
)

type fakeAnalyst struct {
	analystv1.AnalystServiceClient
	launches []*analystv1.PreparedLaunch
	recorded []*analystv1.RecordLaunchesRequest
	reviews  []*analystv1.ReviewActionsRequest
}

func (f *fakeAnalyst) ListLineages(context.Context, *analystv1.ListLineagesRequest, ...grpc.CallOption) (*analystv1.ListLineagesResponse, error) {
	return &analystv1.ListLineagesResponse{Lineages: []*analystv1.LoopStatus{{LineageId: "L1", State: analystv1.LoopState_LOOP_STATE_IDLE}}}, nil
}

func (f *fakeAnalyst) ReviewActions(_ context.Context, r *analystv1.ReviewActionsRequest, _ ...grpc.CallOption) (*analystv1.ReviewActionsResponse, error) {
	f.reviews = append(f.reviews, r)
	return &analystv1.ReviewActionsResponse{Plan: &analystv1.ImprovementPlan{RoundId: r.GetRoundId()}, Launches: f.launches}, nil
}

func (f *fakeAnalyst) RecordLaunches(_ context.Context, r *analystv1.RecordLaunchesRequest, _ ...grpc.CallOption) (*analystv1.RecordLaunchesResponse, error) {
	f.recorded = append(f.recorded, r)
	return &analystv1.RecordLaunchesResponse{Plan: &analystv1.ImprovementPlan{RoundId: r.GetRoundId()}}, nil
}

type analystOpt struct {
	optimizerv1.OptimizerServiceClient
	creates []*optimizerv1.CreateStudyRequest
	starts  []*optimizerv1.StartStudyRequest
}

func (f *analystOpt) CreateStudy(_ context.Context, r *optimizerv1.CreateStudyRequest, _ ...grpc.CallOption) (*optimizerv1.CreateStudyResponse, error) {
	f.creates = append(f.creates, r)
	return &optimizerv1.CreateStudyResponse{Study: &optimizerv1.Study{StudyId: "study-9"}}, nil
}

func (f *analystOpt) StartStudy(_ context.Context, r *optimizerv1.StartStudyRequest, _ ...grpc.CallOption) (*optimizerv1.StartStudyResponse, error) {
	f.starts = append(f.starts, r)
	return &optimizerv1.StartStudyResponse{Study: &optimizerv1.Study{StudyId: r.GetStudyId()}}, nil
}

type analystML struct {
	mlv1.MLServiceClient
	submits []*mlv1.SubmitTrainingJobRequest
}

func (f *analystML) SubmitTrainingJob(_ context.Context, r *mlv1.SubmitTrainingJobRequest, _ ...grpc.CallOption) (*mlv1.SubmitTrainingJobResponse, error) {
	f.submits = append(f.submits, r)
	return &mlv1.SubmitTrainingJobResponse{Job: &mlv1.TrainingJob{JobId: "job-7"}}, nil
}

type analystRig struct {
	*tradingRig
	an     *fakeAnalyst
	opt    *analystOpt
	ml     *analystML
	audits []audit.Event
	fail   bool
}

func newAnalystRig(t *testing.T) *analystRig {
	t.Helper()
	rig := &analystRig{tradingRig: &tradingRig{now: time.Date(2026, 10, 19, 15, 0, 0, 0, time.UTC)}, an: &fakeAnalyst{}, opt: &analystOpt{}, ml: &analystML{}}
	srv, err := New(testConfig("http://localhost:8080"), Deps{MasterKey: make([]byte, 32), Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Analyst: &AnalystClients{Analyst: rig.an}, Research: &ResearchClients{ML: rig.ml, Opt: rig.opt}})
	if err != nil {
		t.Fatal(err)
	}
	srv.now = func() time.Time { return rig.now }
	srv.appendAudit = func(_ context.Context, e audit.Event) (int64, string, error) {
		if rig.fail {
			return 0, "", errors.New("audit down")
		}
		rig.audits = append(rig.audits, e)
		return int64(len(rig.audits)), "", nil
	}
	rig.srv = srv
	return rig
}

// AN-060: at level 0 the owner launches approved actions; the gateway replaces the caller with the
// owner, so a prepared request can never carry the analyst (or anyone else) as the actor.
func TestAN060OwnerSubmitsPreparedLaunchesAsOwner(t *testing.T) {
	rig := newAnalystRig(t)
	sneaky := &mlv1.SubmitTrainingJobRequest{Config: &mlv1.TrainingConfig{Name: "x", LineageId: "L1"},
		Caller: &mlv1.CallerContext{Actor: mlv1.Actor_ACTOR_ANALYST}, ConfirmOverBudget: true}
	tb, _ := proto.Marshal(sneaky)
	study := &optimizerv1.CreateStudyRequest{Spec: &optimizerv1.StudySpec{Name: "s", LineageId: "L1", Budget: &optimizerv1.Budget{MaxTrials: 5}}}
	sb, _ := proto.Marshal(study)
	rig.an.launches = []*analystv1.PreparedLaunch{
		{RoundId: "R1", ActionId: "A1", Kind: "training", Request: tb},
		{RoundId: "R1", ActionId: "A2", Kind: "study", Request: sb},
		{RoundId: "OTHER", ActionId: "A3", Kind: "training", Request: tb},
		{RoundId: "R1", ActionId: "A4", Kind: "order", Request: tb},
	}
	rec := rig.call(rig.srv.handleAnalystReview, "POST", "/", `{"actionIds":["A1","A2","A3","A4"],"approve":true,"reason":"go"}`, desktopUA, "", "id", "R1")
	if rec.Code != http.StatusOK {
		t.Fatalf("review: %d %s", rec.Code, rec.Body.String())
	}
	sub := rig.ml.submits[0]
	if len(rig.ml.submits) != 1 || sub.GetCaller().GetActor() != mlv1.Actor_ACTOR_OWNER || sub.GetCaller().GetAnalystRoundId() != "R1" || sub.GetConfirmOverBudget() {
		t.Fatalf("training submit: %v", rig.ml.submits)
	}
	if len(rig.opt.starts) != 1 || rig.opt.starts[0].GetBudget().GetMaxTrials() != 5 || rig.opt.creates[0].GetCaller().GetActor() != mlv1.Actor_ACTOR_OWNER {
		t.Fatalf("study: %v %v", rig.opt.creates, rig.opt.starts)
	}
	recs := rig.an.recorded[0].GetLaunches()
	if recs[0].GetJobIds()[0] != "job-7" || recs[1].GetJobIds()[0] != "study-9" || recs[2].GetError() == "" || recs[3].GetError() == "" {
		t.Fatalf("records: %v", recs)
	}
	if rig.audits[0].Action != "analyst.actions.review" {
		t.Fatalf("audit: %v", rig.audits)
	}
}

func TestAnalystReadsAndValidation(t *testing.T) {
	rig := newAnalystRig(t)
	rec := rig.call(rig.srv.handleAnalystLineages, "GET", "/", "", desktopUA, "")
	var body map[string]any
	if rec.Code != http.StatusOK || json.Unmarshal(rec.Body.Bytes(), &body) != nil || body["lineages"] == nil {
		t.Fatalf("lineages: %d %s", rec.Code, rec.Body.String())
	}
	if rec := rig.call(rig.srv.handleAnalystReview, "POST", "/", `{"actionIds":["a.b"],"approve":true}`, desktopUA, "", "id", "R1"); rec.Code != http.StatusBadRequest {
		t.Fatalf("bad action id: %d", rec.Code)
	}
	if rec := rig.call(rig.srv.handleChatPost, "POST", "/", `{"content":""}`, desktopUA, "", "id", "C1"); rec.Code != http.StatusBadRequest {
		t.Fatalf("empty chat: %d", rec.Code)
	}
	if rec := rig.call(rig.srv.handleAnalystAnalyze, "POST", "/", `{"refKind":"run","refId":"x y"}`, desktopUA, "", "id", "L1"); rec.Code != http.StatusBadRequest {
		t.Fatalf("bad ref: %d", rec.Code)
	}
	empty := newAnalystRig(t)
	empty.srv.analyst = nil
	if rec := empty.call(empty.srv.handleAnalystLineages, "GET", "/", "", desktopUA, ""); rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("no assistant: %d", rec.Code)
	}
}
