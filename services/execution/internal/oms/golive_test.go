package oms

import (
	"context"
	"errors"
	"testing"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	monitorv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/monitor/v1"
	optimizerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
)

func liveEngine(t *testing.T, flag bool) *Engine {
	t.Helper()
	cfg := DefaultConfig()
	cfg.LiveFlag = flag
	e, err := New(context.Background(), cfg, NewMemory(), nil, nopLedger{}, StaticInstruments{})
	if err != nil {
		t.Fatal(err)
	}
	e.accts["alpaca-live"] = &acctState{info: &brokerv1.BrokerAccount{BrokerAccountId: "alpaca-live", Environment: brokerv1.Environment_ENVIRONMENT_LIVE}}
	e.SetLiveSources(func(context.Context, string) (*monitorv1.GraduationEvidence, error) {
		ev := &monitorv1.GraduationEvidence{StrategyId: "orb", Sessions: 21, Trades: 100, SessionReturnsBps: []float64{1, 2, 3},
			DivergenceReportIds: []string{"d1"}}
		for _, c := range []string{"sessions", "trades", "divergence", "reconciliation", "drawdown", "implementation_shortfall"} {
			ev.Criteria = append(ev.Criteria, &monitorv1.GraduationCriterion{Name: c, Passed: true, Required: "met", Actual: "met"})
		}
		ev.Criteria = append(ev.Criteria, &monitorv1.GraduationCriterion{Name: "dsr_combined", EvidenceOnly: true})
		return ev, nil
	}, func(context.Context, string, string, []float64) (*optimizerv1.GraduationStatistics, error) {
		return &optimizerv1.GraduationStatistics{DsrCombined: 0.97, DsrAvailable: true, NEffective: 4, NObservations: 21,
			RegistryStage: "REGISTRY_STAGE_LIVE_ELIGIBLE", CandidateId: "cand"}, nil
	}, nil)
	if _, err := e.RegisterStrategy(context.Background(), &ordersv1.Strategy{StrategyId: "orb", ShortId: "orb15aa", Name: "ORB",
		Horizon: commonv1.Horizon_HORIZON_INTRADAY, Mode: ordersv1.StrategyMode_STRATEGY_MODE_PAPER, BrokerAccountId: "sim-paper"},
		decimal.Zero, "test"); err != nil {
		t.Fatal(err)
	}
	return e
}

func owner() *ordersv1.Actor { return &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"} }

func TestGL020PromoteIsTheOnlyPathToLive(t *testing.T) {
	e := liveEngine(t, true)
	ctx := context.Background()
	if _, err := e.SetMode(ctx, "orb", ordersv1.StrategyMode_STRATEGY_MODE_LIVE, "config", "owner"); !errors.Is(err, ErrInvalid) {
		t.Fatalf("SetMode live: %v", err)
	}
	if _, err := e.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: "orb", LiveAccountId: "alpaca-live",
		CapitalCap: dec("2000"), IntendedAllocation: dec("10000"), Confirmation: "ORB", Actor: &ordersv1.Actor{Name: "owner"}}); !errors.Is(err, ErrStepUp) {
		t.Fatalf("without step-up: %v", err)
	}
	if _, err := e.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: "orb", LiveAccountId: "alpaca-live",
		CapitalCap: dec("2000"), IntendedAllocation: dec("10000"), Confirmation: "ORB",
		Actor: &ordersv1.Actor{Name: "assistant", StepUpMethod: "passkey"}}); !errors.Is(err, ErrForbidden) {
		t.Fatalf("analyst: %v", err)
	}
	if _, err := e.EnableLiveAccount(ctx, "alpaca-live", "alpaca-live", "trading", owner()); err == nil {
		t.Fatal("trading stage without readiness must fail")
	}
	if _, err := e.EnableLiveAccount(ctx, "alpaca-live", "nope", "read_only", owner()); err == nil {
		t.Fatal("wrong confirmation must fail")
	}
	off := liveEngine(t, false)
	if _, err := off.EnableLiveAccount(ctx, "alpaca-live", "alpaca-live", "read_only", owner()); !errors.Is(err, ErrForbidden) {
		t.Fatalf("flag off: %v", err)
	}
	if _, err := e.EnableLiveAccount(ctx, "alpaca-live", "alpaca-live", "read_only", owner()); err != nil {
		t.Fatal(err)
	}
	e.cleanSess["alpaca-live"] = map[string]struct{}{"2026-10-16": {}}
	for _, it := range attestedItems() {
		if _, err := e.SetReadinessItem(ctx, "alpaca-live", it.GetId(), "passed", "rehearsed", "docs/runbooks/go-live.md", owner()); err != nil {
			t.Fatal(err)
		}
	}
	rd, err := e.LiveReadiness(ctx, "alpaca-live")
	if err != nil || !rd.GetComplete() {
		t.Fatalf("readiness complete=%v err=%v", rd.GetComplete(), err)
	}
	if _, err := e.EnableLiveAccount(ctx, "alpaca-live", "alpaca-live", "trading", owner()); err != nil {
		t.Fatal(err)
	}
	if _, err := e.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: "orb", LiveAccountId: "alpaca-live",
		CapitalCap: dec("5000"), IntendedAllocation: dec("10000"), Confirmation: "ORB", Actor: owner()}); err == nil {
		t.Fatal("cap above 20% must fail")
	}
	resp, err := e.PromoteToLive(ctx, &ordersv1.PromoteToLiveRequest{StrategyId: "orb", LiveAccountId: "alpaca-live",
		CapitalCap: dec("1500"), IntendedAllocation: dec("10000"), Confirmation: "ORB", Actor: owner()})
	if err != nil {
		t.Fatal(err)
	}
	if resp.GetStrategy().GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_LIVE || resp.GetStrategy().GetLiveCapitalCap().GetValue() != "1500" {
		t.Fatalf("promoted %+v", resp.GetStrategy())
	}
	if resp.GetPaperTwin().GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_PAPER || resp.GetPaperTwin().GetPaperTwinOf() != "orb" {
		t.Fatalf("twin %+v", resp.GetPaperTwin())
	}
	if !resp.GetReport().GetOverall() {
		t.Fatalf("report %+v", resp.GetReport())
	}
	// A stored live mode with no promotion record is demoted on the next guard (GL-050).
	e.strategies["sneak"] = &ordersv1.Strategy{StrategyId: "sneak", ShortId: "sneak01", Mode: ordersv1.StrategyMode_STRATEGY_MODE_LIVE, BrokerAccountId: "alpaca-live"}
	e.guardLive(ctx)
	if e.strategies["sneak"].GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
		t.Fatal("unguarded live mode survived")
	}
	st, n, _, err := e.DemoteFromLive(ctx, "orb", "rollback drill", "hold", &ordersv1.Actor{Name: "owner"})
	if err != nil || st.GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_PAPER || st.GetBrokerAccountId() != "sim-paper" {
		t.Fatalf("demote %v %+v canceled %d", err, st, n)
	}
}
