package app

import (
	"context"

	"google.golang.org/grpc"

	monitorv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/monitor/v1"
	optimizerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	"github.com/illyanyc/strawberry/services/execution/internal/oms"
)

// WireLive connects graduation inputs (MN-040 evidence, GL-002 statistics) and the enablement store.
func WireLive(e *Engine, monitor monitorv1.MonitorServiceClient, opt optimizerv1.OptimizerServiceClient, signer *svcauth.Signer, db oms.LiveDB) {
	var ev oms.GraduationEvidence
	var research oms.GraduationResearch
	call := func(aud string) []grpc.CallOption {
		if signer == nil {
			return nil
		}
		return []grpc.CallOption{signer.PerRPC(aud)}
	}
	if monitor != nil {
		ev = func(ctx context.Context, id string) (*monitorv1.GraduationEvidence, error) {
			r, err := monitor.GetGraduationEvidence(ctx, &monitorv1.GetGraduationEvidenceRequest{StrategyId: id}, call("monitor")...)
			if err != nil {
				return nil, err
			}
			return r.GetEvidence(), nil
		}
	}
	if opt != nil {
		research = func(ctx context.Context, id, horizon string, returns []float64) (*optimizerv1.GraduationStatistics, error) {
			r, err := opt.GetGraduationStatistics(ctx, &optimizerv1.GetGraduationStatisticsRequest{StrategyId: id, Horizon: horizon, PaperSessionReturns: returns}, call("optimizer")...)
			if err != nil {
				return nil, err
			}
			return r.GetStatistics(), nil
		}
	}
	e.SetLiveSources(ev, research, db)
}
