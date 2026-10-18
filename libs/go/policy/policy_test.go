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
	ActLedgerBreakResolve, ActResearchCycleOpen, ActHoldoutEvaluate, ActHoldoutSchedule,
	ActUITradingViewWidget, ActStrategyRegister,
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
	// The matrix uses Resource{Mode: "paper"}: promotion to paper escalates (EX-052).
	passkeyStepUp := contains(mustRequirePasskeyStepUp, act) || act == ActModelPromote
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

// Research governance actions (EX-011, EX-014, EX-033, EX-052, EX-053, D31 rule 3).
func TestEX052ResearchStepUps(t *testing.T) {
	now := time.Now()
	plain := Actor{Kind: KindOwner}
	fresh := Actor{Kind: KindOwner, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(time.Minute)}
	cases := []struct {
		a    Action
		res  Resource
		need bool
	}{
		{ActResearchRead, Resource{}, false},
		{ActResearchLaunch, Resource{}, false},
		{ActModelPromote, Resource{Mode: "validated"}, false},
		{ActModelPromote, Resource{Mode: "shadow"}, false},
		{ActModelPromote, Resource{Mode: "paper"}, true},
		{ActModelPromote, Resource{Mode: "live_eligible"}, true},
		{ActModelDemote, Resource{}, false},
		{ActHoldoutEvaluate, Resource{}, true},
		{ActHoldoutSchedule, Resource{}, true},
		{ActResearchCycleOpen, Resource{}, true},
		{ActAnalystAutonomySet, Resource{Mode: "lower"}, false},
		{ActAnalystAutonomySet, Resource{Mode: "raise"}, true},
		{ActDeploymentLimitsSet, Resource{Mode: "lower"}, false},
		{ActDeploymentLimitsSet, Resource{Mode: "raise"}, true},
	}
	for _, c := range cases {
		d := Authorize(plain, c.a, c.res, now)
		if c.need != (d.Effect == RequireStepUp) {
			t.Errorf("%s %v without step-up: %v", c.a, c.res, d)
		}
		if Authorize(fresh, c.a, c.res, now).Effect != Allow {
			t.Errorf("%s %v with a fresh passkey should be allowed", c.a, c.res)
		}
		if RequiresPasskeyStepUp(c.a, c.res) != c.need {
			t.Errorf("RequiresPasskeyStepUp(%s, %v) != %v", c.a, c.res, c.need)
		}
	}
	if Authorize(Actor{Kind: KindAssistant}, ActHoldoutEvaluate, Resource{}, now).Effect != Deny {
		t.Error("the assistant must never evaluate the holdout")
	}
}

// AN-032 / AN-061: the analyst (actor assistant) may perform no analyst owner action, never raises its
// own autonomy, and never reaches an order-path, limit, kill-switch, promotion, or credential action.
func TestAN032AssistantDeniedEveryOwnerAndAnalystAction(t *testing.T) {
	now := time.Now()
	a := Actor{Kind: KindAssistant, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(time.Minute)}
	for _, act := range OwnerActions() {
		if act == ActAssistantExperiment {
			continue
		}
		if d := Authorize(a, act, Resource{Mode: "paper"}, now); d.Effect != Deny {
			t.Errorf("assistant %s: got %s, want deny", act, d.Effect)
		}
	}
	if d := Authorize(Actor{Kind: KindOwner}, ActAnalystAutonomySet, Resource{Mode: "raise"}, now); d.Effect != RequireStepUp {
		t.Errorf("owner raising analyst autonomy without step-up: %v", d)
	}
	for _, act := range []Action{ActAnalystLoopPause, ActAnalystActionsReview, ActAnalystCodeReview, ActAssistantChat} {
		if d := Authorize(Actor{Kind: KindOwner}, act, Resource{}, now); d.Effect != Allow {
			t.Errorf("owner %s: %v", act, d)
		}
	}
}

func TestStepUpListCoversSection02(t *testing.T) {
	for _, a := range mustRequirePasskeyStepUp {
		if !RequiresPasskeyStepUp(a, Resource{}) {
			t.Errorf("%s must require passkey step-up", a)
		}
	}
}

// RM-020 / UI-050: engaging a kill switch from a phone needs a fresh passkey; from a desktop it needs
// none (one confirmation). TOTP or recovery step-ups never satisfy the phone path.
func TestRM020MobileKillEngageNeedsFreshPasskey(t *testing.T) {
	now := time.Now()
	plain := Actor{Kind: KindOwner}
	fresh := Actor{Kind: KindOwner, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(time.Minute)}
	stale := Actor{Kind: KindOwner, StepUpMethod: StepUpPasskey, StepUpUntil: now.Add(-time.Second)}
	totp := Actor{Kind: KindOwner, StepUpMethod: StepUpTOTP, StepUpUntil: now.Add(time.Minute)}
	if d := Authorize(plain, ActKillSwitchEngage, Resource{ID: "global:"}, now); d.Effect != Allow {
		t.Fatalf("desktop engage: %v", d)
	}
	for name, a := range map[string]Actor{"no step-up": plain, "expired passkey": stale, "fresh totp": totp} {
		if d := Authorize(a, ActKillSwitchEngage, Resource{Mode: "mobile", ID: "global:"}, now); d.Effect != RequireStepUp {
			t.Errorf("phone engage with %s: %v", name, d)
		}
	}
	if d := Authorize(fresh, ActKillSwitchEngage, Resource{Mode: "mobile", ID: "global:"}, now); d.Effect != Allow {
		t.Fatalf("phone engage with a fresh passkey: %v", d)
	}
	if !RequiresPasskeyStepUp(ActKillSwitchEngage, Resource{Mode: "mobile"}) || RequiresPasskeyStepUp(ActKillSwitchEngage, Resource{}) {
		t.Fatal("RequiresPasskeyStepUp must escalate only the phone path")
	}
	if d := Authorize(plain, ActNotificationsManage, Resource{}, now); d.Effect != Allow {
		t.Fatalf("notifications.manage: %v", d)
	}
}
