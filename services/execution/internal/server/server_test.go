package server

import (
	"context"
	"testing"

	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// SE-040, RM-018: only api-gateway may assert an owner actor or a step-up; the monitor's containment
// calls are always audited as the monitor, whatever the request claims.
func TestRM018ActorForcedForNonGatewayCallers(t *testing.T) {
	claim := &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"}
	got := actor(svcauth.WithCaller(context.Background(), "monitor"), claim)
	if got.GetName() != "service:monitor" || got.GetStepUpMethod() != "" {
		t.Fatalf("monitor caller recorded as %q step-up %q", got.GetName(), got.GetStepUpMethod())
	}
	gw := actor(svcauth.WithCaller(context.Background(), "api-gateway"), &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"})
	if gw.GetName() != "owner" || gw.GetStepUpMethod() != "passkey" {
		t.Fatalf("gateway actor lost: %v", gw)
	}
	if def := actor(svcauth.WithCaller(context.Background(), "api-gateway"), nil); def.GetName() != "service:api-gateway" {
		t.Fatalf("default actor %q", def.GetName())
	}
}

// SE-040: the monitor may call only the containment and read RPCs.
func TestRM018MonitorAllowlist(t *testing.T) {
	for m, callers := range AllowedCallers {
		has := false
		for _, c := range callers {
			has = has || c == "monitor"
		}
		want := map[string]bool{svc + "DemoteStrategy": true, svc + "TriggerKillSwitch": true, svc + "ListOrders": true, svc + "ListIntents": true,
			svc + "ListFills": true, svc + "ListStrategies": true, svc + "ListKillSwitches": true, svc + "GetTradingStatus": true,
			svc + "DemoteFromLive": true}[m]
		if has != want {
			t.Fatalf("%s: monitor allowed=%v, want %v", m, has, want)
		}
	}
}
