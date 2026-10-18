// Package policy is the single authorization function for the platform (rule SE-001):
// Authorize(actor, action, resource) -> Allow | Deny(reason) | RequireStepUp.
//
// Every service routes every authorization decision through it; the UI never decides. The action
// list is closed: unknown actions are denied.
package policy

import (
	"fmt"
	"strings"
	"time"
)

// Action is an enumerated operation.
type Action string

// Actions. Keep in sync with docs/business-logic/security.md (SE-002, SE-003).
const (
	ActAuthSessionRead       Action = "auth.session.read"
	ActAuthSessionRevoke     Action = "auth.session.revoke"
	ActAuthPasskeyRegister   Action = "auth.passkey.register"
	ActAuthPasskeyDelete     Action = "auth.passkey.delete"
	ActAuthTOTPEnroll        Action = "auth.totp.enroll"
	ActAuthRecoveryRegen     Action = "auth.recovery.regenerate"
	ActAuditRead             Action = "audit.read"
	ActSettingsRead          Action = "settings.read"
	ActSettingsWrite         Action = "settings.write"
	ActTradingLiveEnable     Action = "trading.live.enable"
	ActTradingLiveDisable    Action = "trading.live.disable"
	ActStrategyPromoteLive   Action = "strategy.promote.live"
	ActStrategyDemoteLive    Action = "strategy.demote.live"
	ActRiskLimitsRaise       Action = "risk.limits.raise"
	ActRiskLimitsLower       Action = "risk.limits.lower"
	ActAllocationIncrease    Action = "allocation.increase"
	ActAllocationDecrease    Action = "allocation.decrease"
	ActKillSwitchEngage      Action = "killswitch.engage"
	ActKillSwitchDisable     Action = "killswitch.disable"
	ActBrokerCredWrite       Action = "broker.credentials.write"
	ActBrokerCredDelete      Action = "broker.credentials.delete"
	ActBrokerCredStatus      Action = "broker.credentials.status"
	ActOrdersSubmitManual    Action = "orders.submit.manual"
	ActMobileOrderEnable     Action = "mobile.order_entry.enable"
	ActAssistantWriteEnable  Action = "assistant.write_tools.enable"
	ActAssistantExperiment   Action = "assistant.experiment.launch"
	ActDataExportPositions   Action = "data.export.positions"
	ActSignalsCreate         Action = "signals.create"
	ActCredentialsUseForAuth Action = "broker.credentials.use"
	ActTradingRead           Action = "trading.read"
	ActOrdersCancel          Action = "orders.cancel"
	ActStrategyModeSet       Action = "strategy.mode.set"
	ActLedgerBreakResolve    Action = "ledger.break.resolve"
	// Research (sections 10, 11): views, launches, and governance actions.
	ActResearchRead       Action = "research.read"
	ActResearchLaunch     Action = "research.launch"
	ActResearchCycleOpen  Action = "research.cycle.open"
	ActModelPromote       Action = "model.promote"
	ActModelDemote        Action = "model.demote"
	ActHoldoutEvaluate    Action = "holdout.evaluate"
	ActHoldoutSchedule    Action = "holdout.schedule"
	ActAnalystAutonomySet Action = "analyst.autonomy.set"
	// Candidate deployment limits (EX-062) and registering a promoted candidate with execution (SM-016).
	ActDeploymentLimitsSet Action = "research.deployment_limits.set"
	ActStrategyRegister    Action = "strategy.register"
	// Monitoring (section 13 Phase 5, ADR-0045): views, alert acknowledgement and silencing,
	// containment policies (loosening escalates, MN-036).
	ActMonitorRead    Action = "monitor.read"
	ActAlertAck       Action = "alerts.ack"
	ActAlertSilence   Action = "alerts.silence"
	ActContainmentSet Action = "monitor.containment.set"
	// AI Research Analyst (section 12, AN-060..AN-063, AN-073, AN-080).
	ActAnalystRead          Action = "analyst.read"
	ActAnalystActionsReview Action = "analyst.actions.review"
	ActAnalystLoopPause     Action = "analyst.loop.pause"
	ActAnalystAnalysis      Action = "analyst.analysis.request"
	ActAnalystCodeReview    Action = "analyst.code.review"
	ActAssistantChat        Action = "assistant.chat"
	// Section 14 Phase 5: Web Push subscriptions (SE-060), the TradingView widget's CSP relaxation
	// (SE-065), and the TradingView intake status page (UI-048).
	ActNotificationsManage   Action = "notifications.manage"
	ActUITradingViewWidget   Action = "ui.tradingview_widget.enable"
	ActTradingViewStatusRead Action = "tradingview.status.read"
)

// stepUp says which fresh second factor an owner action needs.
type stepUp int

const (
	noStepUp stepUp = iota
	// stepUpAny accepts a fresh passkey, TOTP, or recovery-code step-up (account recovery actions).
	stepUpAny
	// stepUpPasskey requires a fresh passkey assertion (everything that can move money or weaken safety).
	stepUpPasskey
)

// ownerActions lists every action the owner may perform and its step-up requirement (SE-003).
var ownerActions = map[Action]stepUp{
	ActAuthSessionRead:      noStepUp,
	ActAuthSessionRevoke:    noStepUp,
	ActAuthPasskeyRegister:  stepUpAny,
	ActAuthPasskeyDelete:    stepUpPasskey,
	ActAuthTOTPEnroll:       stepUpAny,
	ActAuthRecoveryRegen:    stepUpAny,
	ActAuditRead:            noStepUp,
	ActSettingsRead:         noStepUp,
	ActSettingsWrite:        noStepUp,
	ActTradingLiveEnable:    stepUpPasskey,
	ActTradingLiveDisable:   noStepUp,
	ActStrategyPromoteLive:  stepUpPasskey,
	ActStrategyDemoteLive:   noStepUp,
	ActRiskLimitsRaise:      stepUpPasskey,
	ActRiskLimitsLower:      noStepUp,
	ActAllocationIncrease:   stepUpPasskey,
	ActAllocationDecrease:   noStepUp,
	ActKillSwitchEngage:     noStepUp,
	ActKillSwitchDisable:    stepUpPasskey,
	ActBrokerCredWrite:      stepUpPasskey,
	ActBrokerCredDelete:     stepUpPasskey,
	ActBrokerCredStatus:     stepUpPasskey,
	ActOrdersSubmitManual:   noStepUp, // live orders escalate to stepUpPasskey below
	ActMobileOrderEnable:    stepUpPasskey,
	ActAssistantWriteEnable: stepUpPasskey,
	ActAssistantExperiment:  noStepUp,
	ActDataExportPositions:  stepUpPasskey,
	// Read-only trading views, risk-reducing cancels, and non-live mode changes (disabled, shadow,
	// paper, halted); live promotion is ActStrategyPromoteLive.
	ActTradingRead:     noStepUp,
	ActOrdersCancel:    noStepUp,
	ActStrategyModeSet: noStepUp,
	// Resolving a reconciliation break moves ownership or cash (VP-019, RM-007).
	ActLedgerBreakResolve: stepUpPasskey,
	// Research views and launches need no step-up; opening a research cycle (D31 rule 3), holdout
	// access and scheduling (EX-011, EX-014), promotion to paper or live_eligible (N4, EX-052/053),
	// and raising analyst autonomy (EX-033) need a passkey (escalations in Authorize).
	ActResearchRead:       noStepUp,
	ActResearchLaunch:     noStepUp,
	ActResearchCycleOpen:  stepUpPasskey,
	ActModelPromote:       noStepUp,
	ActModelDemote:        noStepUp,
	ActHoldoutEvaluate:    stepUpPasskey,
	ActHoldoutSchedule:    stepUpPasskey,
	ActAnalystAutonomySet: noStepUp,
	// Raising a lineage's deployment limits or allocation needs a passkey (RM-007, escalated below);
	// registering a candidate with execution sets its allocation and strategy-scope limits (RM-019).
	ActDeploymentLimitsSet: noStepUp,
	ActStrategyRegister:    stepUpPasskey,
	// Monitoring views and acknowledgements need no step-up; silencing an alert and loosening a
	// containment policy (escalated below) need a passkey (MN-036, MN-042).
	ActMonitorRead:    noStepUp,
	ActAlertAck:       noStepUp,
	ActAlertSilence:   stepUpPasskey,
	ActContainmentSet: noStepUp,
	// Analyst views, pausing, reviewing proposals, and chat reduce or do not change risk; raising its
	// autonomy is analyst.autonomy.set in raise mode (AN-061, escalated above).
	ActAnalystRead:          noStepUp,
	ActAnalystActionsReview: noStepUp,
	ActAnalystLoopPause:     noStepUp,
	ActAnalystAnalysis:      noStepUp,
	ActAnalystCodeReview:    noStepUp,
	ActAssistantChat:        noStepUp,
	// Managing push subscriptions changes where alerts go, not risk; loading a third-party widget
	// relaxes the CSP, so it needs a passkey (SE-065). The kill-switch entry above escalates on phones.
	ActNotificationsManage:   noStepUp,
	ActUITradingViewWidget:   stepUpPasskey,
	ActTradingViewStatusRead: noStepUp,
}

// escalated reports actions whose step-up depends on the resource (live manual orders, promotion to
// paper or live_eligible, raising analyst autonomy or budgets, engaging a kill switch from a phone).
func escalated(action Action, res Resource) bool {
	switch action {
	case ActKillSwitchEngage:
		// RM-020, UI-050: a phone halts only after a fresh passkey assertion (a lost or borrowed
		// unlocked phone must not be able to flatten strategy positions); desktop stays one confirmation.
		return res.Mode == "mobile"
	case ActOrdersSubmitManual:
		return strings.EqualFold(res.Mode, "live")
	case ActModelPromote:
		return res.Mode == "paper" || res.Mode == "live_eligible"
	case ActAnalystAutonomySet, ActDeploymentLimitsSet, ActContainmentSet:
		return res.Mode == "raise"
	}
	return false
}

// serviceActions lists what each internal service actor may do through the policy layer.
var serviceActions = map[string][]Action{
	"broker-gateway": {ActCredentialsUseForAuth},
}

// assistantActions: the assistant may only launch experiments within budget (SM-010).
var assistantActions = []Action{ActAssistantExperiment}

// webhookActions: a TradingView webhook may only create signals, never orders (section 02 Stage B).
var webhookActions = map[string][]Action{"tradingview": {ActSignalsCreate}}

// ActorKind classifies callers.
type ActorKind string

// Actor kinds.
const (
	KindAnonymous ActorKind = "anonymous"
	KindOwner     ActorKind = "owner"
	KindService   ActorKind = "service"
	KindAssistant ActorKind = "assistant"
	KindWebhook   ActorKind = "webhook"
)

// StepUpMethod records how the last step-up was performed.
type StepUpMethod string

// Step-up methods.
const (
	StepUpNone     StepUpMethod = ""
	StepUpPasskey  StepUpMethod = "passkey"
	StepUpTOTP     StepUpMethod = "totp"
	StepUpRecovery StepUpMethod = "recovery"
)

// Actor is who is asking.
type Actor struct {
	Kind ActorKind
	// Name is the service or webhook name, or the owner ID.
	Name         string
	StepUpMethod StepUpMethod
	StepUpUntil  time.Time
}

// String renders the actor as used in audit records, e.g. "owner", "service:broker-gateway".
func (a Actor) String() string {
	switch a.Kind {
	case KindOwner, KindAnonymous, KindAssistant:
		return string(a.Kind)
	default:
		return string(a.Kind) + ":" + a.Name
	}
}

// Resource carries attributes some decisions depend on.
type Resource struct {
	// Mode is "paper" or "live" when the action targets something with a mode; "raise" for autonomy
	// increases; "mobile" when the request comes from a phone (kill-switch engagement).
	Mode string
	// ID identifies the target, for audit and future per-resource rules.
	ID string
}

// Effect is the outcome kind.
type Effect string

// Effects.
const (
	Allow         Effect = "allow"
	Deny          Effect = "deny"
	RequireStepUp Effect = "require_step_up"
)

// Decision is the authorization result.
type Decision struct {
	Effect Effect
	Reason string
}

// Authorize decides whether actor may perform action on resource at time now.
func Authorize(actor Actor, action Action, res Resource, now time.Time) Decision {
	switch actor.Kind {
	case KindOwner:
		req, ok := ownerActions[action]
		if !ok {
			return deny("unknown action %q", action)
		}
		if escalated(action, res) {
			req = stepUpPasskey
		}
		return checkStepUp(actor, req, now)
	case KindService:
		return allowIf(contains(serviceActions[actor.Name], action), actor, action)
	case KindAssistant:
		return allowIf(contains(assistantActions, action), actor, action)
	case KindWebhook:
		return allowIf(contains(webhookActions[actor.Name], action), actor, action)
	default:
		return deny("unauthenticated")
	}
}

func checkStepUp(actor Actor, req stepUp, now time.Time) Decision {
	if req == noStepUp {
		return Decision{Effect: Allow}
	}
	fresh := now.Before(actor.StepUpUntil)
	switch {
	case fresh && actor.StepUpMethod == StepUpPasskey:
		return Decision{Effect: Allow}
	case fresh && req == stepUpAny && actor.StepUpMethod != StepUpNone:
		return Decision{Effect: Allow}
	case req == stepUpPasskey:
		return Decision{Effect: RequireStepUp, Reason: "passkey step-up required"}
	default:
		return Decision{Effect: RequireStepUp, Reason: "step-up required"}
	}
}

func allowIf(ok bool, actor Actor, action Action) Decision {
	if ok {
		return Decision{Effect: Allow}
	}
	return deny("%s may not %s", actor, action)
}

func deny(format string, args ...any) Decision {
	return Decision{Effect: Deny, Reason: fmt.Sprintf(format, args...)}
}

func contains(list []Action, a Action) bool {
	for _, x := range list {
		if x == a {
			return true
		}
	}
	return false
}

// OwnerActions returns every owner action (for route-inventory and documentation tests).
func OwnerActions() []Action {
	out := make([]Action, 0, len(ownerActions))
	for a := range ownerActions {
		out = append(out, a)
	}
	return out
}

// RequiresPasskeyStepUp reports whether action needs a passkey step-up for the owner on resource.
func RequiresPasskeyStepUp(action Action, res Resource) bool {
	if escalated(action, res) {
		return true
	}
	return ownerActions[action] == stepUpPasskey
}
