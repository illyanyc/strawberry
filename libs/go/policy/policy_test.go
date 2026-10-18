package policy

import (
	"testing"
	"time"
)

// The step-up list from section 02 Stage A, written independently of the policy table so a
// silent edit to ownerActions fails this test (SE-003).
var mustRequirePasskeyStepUp = []Action{
	ActTradingLiveEnable, ActStrategyPromoteLive, ActRiskLimitsRaise, ActAllocationIncrease,
	ActKillSwitchDisable, ActBrokerCredWrite, ActBrokerCredDelete, ActBrokerCredStatus,
	ActMobileOrderEnable, ActAssistantWriteEnable, ActDataExportPositions, ActAuthPasskeyDelete,
	ActLedgerBreakResolve,
}

var recoveryActions = []Action{ActAuthPasskeyRegister, ActAuthTOTPEnroll, ActAuthRecoveryRegen}

// SE-001/SE-003: every action × actor × step-up state.
func TestSE003AuthorizeMatrix(t *testing.T) {
	now := time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC)
	states := map[string]Actor{
		"owner/no-stepup":       {Kind: KindOwner, Name: "owner"},
		"owner/expired-passkey": {Kind: KindOwner, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(-time.Second)},
		"owner/fresh-passkey":   {Kind: KindOwner, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(time.Minute)},
		"owner/fresh-totp":      {Kind: KindOwner, StepUpMethod: StepUpTOTP, StepUpUntil: now.Add(time.Minute)},
		"owner/fresh-recovery":  {Kind: KindOwner, StepUpMethod: StepUpRecovery, StepUpUntil: now.Add(time.Minute)},
		"anonymous":             {Kind: KindAnonymous},
		"assistant":             {Kind: KindAssistant, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(time.Minute)},
		"webhook:tradingview":   {Kind: KindWebhook, Name: "tradingview"},
		"service:api-gateway":   {Kind: KindService, Name: "api-gateway"},
	}
	all := append(OwnerActions(), ActSignalsCreate, ActCredentialsUseForAuth, Action("made.up"))
	for name, actor := range states {
		for _, act := range all {
			got := Authorize(actor, act, Resource{Mode: "paper"}, now).Effect
			want := expected(name, act)
			if got != want {
				t.Errorf("%s %s: got %s want %s", name, act, got, want)
			}
		}
	}
}

func expected(state string, act Action) Effect {
	passkeyStepUp := contains(mustRequirePasskeyStepUp, act)
	recovery := contains(recoveryActions, act)
	_, isOwnerAction := ownerActions[act]
	switch state {
	case "anonymous", "service:api-gateway":
		return Deny
	case "assistant":
		return map[bool]Effect{true: Allow, false: Deny}[act == ActAssistantExperiment]
	case "webhook:tradingview":
		return map[bool]Effect{true: Allow, false: Deny}[act == ActSignalsCreate]
	}
	if !isOwnerAction {
		return Deny
	}
	switch state {
	case "owner/fresh-passkey":
		return Allow
	case "owner/fresh-totp", "owner/fresh-recovery":
		if passkeyStepUp {
			return RequireStepUp
		}
		return Allow
	default:
		if passkeyStepUp || recovery {
			return RequireStepUp
		}
		return Allow
	}
}

func TestLiveManualOrderNeedsPasskeyStepUp(t *testing.T) {
	now := time.Now()
	owner := Actor{Kind: KindOwner}
	if d := Authorize(owner, ActOrdersSubmitManual, Resource{Mode: "paper"}, now); d.Effect != Allow {
		t.Fatalf("paper manual order: %v", d)
	}
	if d := Authorize(owner, ActOrdersSubmitManual, Resource{Mode: "LIVE"}, now); d.Effect != RequireStepUp {
		t.Fatalf("live manual order: %v", d)
	}
	totp := Actor{Kind: KindOwner, StepUpMethod: StepUpTOTP, StepUpUntil: now.Add(time.Minute)}
	if d := Authorize(totp, ActOrdersSubmitManual, Resource{Mode: "live"}, now); d.Effect != RequireStepUp {
		t.Fatalf("live manual order with TOTP step-up: %v", d)
	}
}

func TestStepUpListCoversSection02(t *testing.T) {
	for _, a := range mustRequirePasskeyStepUp {
		if !RequiresPasskeyStepUp(a, Resource{}) {
			t.Errorf("%s must require passkey step-up", a)
		}
	}
}
