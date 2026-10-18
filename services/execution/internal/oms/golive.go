package oms

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	monitorv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/monitor/v1"
	optimizerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/optimizer/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/outbox"
)

// Section 15 go-live (ADR-0054, GL-*). PromoteToLive is the only writer of STRATEGY_MODE_LIVE.
// The paper record comes from the monitor's graduation evidence (MN-040); DSR on the combined
// research + paper record and the registry stage come from the optimizer (GL-002). Missing
// evidence fails the criterion (fail closed).

const (
	dsrMin          = 0.95
	capLow          = "0.10"
	capHigh         = "0.20"
	liveCollar      = "0.01"
	registryLive    = "REGISTRY_STAGE_LIVE_ELIGIBLE"
	readOnlyStage   = "read_only"
	tradingStage    = "trading"
	minLiveSessions = 10
)

// GraduationEvidence loads the monitor's paper/shadow evidence (MN-040).
type GraduationEvidence func(ctx context.Context, strategyID string) (*monitorv1.GraduationEvidence, error)

// GraduationResearch loads the optimizer's combined-DSR and registry stage (GL-002).
type GraduationResearch func(ctx context.Context, strategyID, horizon string, sessionReturns []float64) (*optimizerv1.GraduationStatistics, error)

// LiveDB persists readiness, clean read-only sessions, and the audited enablement row.
type LiveDB interface {
	Load(ctx context.Context) (ready map[string]*ordersv1.LiveReadiness, sessions map[string][]string, enable map[string]Enablement, err error)
	SaveReadiness(ctx context.Context, r *ordersv1.LiveReadiness) error
	AddSession(ctx context.Context, account, date string) error
	PutEnablement(ctx context.Context, account string, en Enablement) (Enablement, error)
}

// Enablement is one broker.live_enablement row (GL-011, GL-012).
type Enablement struct {
	Enabled, Orders bool
	By, Reason      string
	Seq             int64
	Updated         int64
}

func (e *Engine) initLive() {
	e.readiness = map[string]*ordersv1.LiveReadiness{}
	e.cleanSess = map[string]map[string]struct{}{}
	e.enablement = map[string]Enablement{}
}

// SetLiveSources wires the monitor and optimizer inputs and the persistence (nil evidence fails closed).
func (e *Engine) SetLiveSources(ev GraduationEvidence, research GraduationResearch, db LiveDB) {
	e.mu.Lock()
	e.evidence, e.research, e.liveDB = ev, research, db
	e.mu.Unlock()
}

// LoadLive restores persisted go-live state. Missing tables leave the in-memory state empty.
func (e *Engine) LoadLive(ctx context.Context) error {
	e.mu.Lock()
	db := e.liveDB
	e.mu.Unlock()
	if db == nil {
		return nil
	}
	ready, sessions, enable, err := db.Load(ctx)
	if err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	for k, v := range ready {
		e.readiness[k] = v
	}
	for acct, dates := range sessions {
		set := map[string]struct{}{}
		for _, d := range dates {
			set[d] = struct{}{}
		}
		e.cleanSess[acct] = set
	}
	for k, v := range enable {
		e.enablement[k] = v
	}
	return nil
}

func (e *Engine) horizonName(h commonv1.Horizon) string {
	if h == commonv1.Horizon_HORIZON_SWING {
		return "swing"
	}
	return "intraday"
}

// GraduationReport is the section 15 report (GL-001..GL-003): monitor evidence plus the DSR and
// registry-stage criteria the monitor deliberately leaves open.
func (e *Engine) GraduationReport(ctx context.Context, strategyID string) (*ordersv1.GraduationReport, error) {
	e.mu.Lock()
	s := e.strategies[strategyID]
	ev, research := e.evidence, e.research
	if s == nil {
		e.mu.Unlock()
		return nil, ErrNotFound
	}
	horizon := e.horizonName(s.GetHorizon())
	e.mu.Unlock()
	rep := &ordersv1.GraduationReport{StrategyId: strategyID, ComputedAt: e.now()}
	var evidence *monitorv1.GraduationEvidence
	if ev == nil {
		rep.Criteria = append(rep.Criteria, failCrit("monitor_evidence", "MN-040 graduation evidence", "not configured", "monitor"))
	} else if got, err := ev(ctx, strategyID); err != nil || got == nil {
		detail := "unavailable"
		if err != nil {
			detail = err.Error()
		}
		rep.Criteria = append(rep.Criteria, failCrit("monitor_evidence", "MN-040 graduation evidence", detail, "monitor"))
	} else {
		evidence = got
		rep.PaperSessions, rep.PaperTrades = got.GetSessions(), got.GetTrades()
		rep.WindowStart, rep.WindowEnd = got.GetPaperSince(), got.GetGeneratedAt()
		rep.PaperSessionReturns = got.GetSessionReturnsBps()
		for _, id := range got.GetDivergenceReportIds() {
			rep.EvidenceLinks = append(rep.EvidenceLinks, "monitor:divergence:"+id)
		}
		rep.EvidenceLinks = append(rep.EvidenceLinks, "monitor:graduation:"+strategyID)
		for _, c := range got.GetCriteria() {
			if c.GetEvidenceOnly() {
				continue // DSR is recomputed here (GL-002), never trusted from the monitor.
			}
			rep.Criteria = append(rep.Criteria, &ordersv1.GraduationCriterion{
				Name: c.GetName(), Required: c.GetRequired(), Actual: c.GetActual(), Passed: c.GetPassed(), Source: "monitor", Detail: c.GetEvidenceId(),
			})
		}
	}
	var returns []float64
	if evidence != nil {
		returns = evidence.GetSessionReturnsBps()
	}
	switch research {
	case nil:
		rep.Criteria = append(rep.Criteria, failCrit("dsr_combined", ">= 0.95", "optimizer not configured", "optimizer"))
		rep.Criteria = append(rep.Criteria, failCrit("registry_stage", registryLive, "optimizer not configured", "optimizer"))
	default:
		st, err := research(ctx, strategyID, horizon, returns)
		if err != nil || st == nil {
			detail := "unavailable"
			if err != nil {
				detail = err.Error()
			}
			rep.Criteria = append(rep.Criteria, failCrit("dsr_combined", ">= 0.95", detail, "optimizer"))
			rep.Criteria = append(rep.Criteria, failCrit("registry_stage", registryLive, detail, "optimizer"))
		} else {
			rep.RegistryStage = st.GetRegistryStage()
			rep.Criteria = append(rep.Criteria, &ordersv1.GraduationCriterion{
				Name: "dsr_combined", Required: ">= 0.95", Actual: fmt.Sprintf("%.4f (N %.2f, %d obs)", st.GetDsrCombined(), st.GetNEffective(), st.GetNObservations()),
				Passed: st.GetDsrAvailable() && st.GetDsrCombined() >= dsrMin, Source: "optimizer",
			})
			rep.Criteria = append(rep.Criteria, &ordersv1.GraduationCriterion{
				Name: "registry_stage", Required: registryLive, Actual: st.GetRegistryStage(),
				Passed: st.GetRegistryStage() == registryLive, Source: "optimizer",
			})
			if st.GetCandidateId() != "" {
				rep.EvidenceLinks = append(rep.EvidenceLinks, "optimizer:candidate:"+st.GetCandidateId())
			}
		}
	}
	rep.Overall = len(rep.Criteria) > 0
	for _, c := range rep.Criteria {
		if !c.GetPassed() {
			rep.Overall = false
		}
	}
	rep.ReportHash = hashProto(rep)
	return rep, nil
}

func failCrit(name, required, actual, source string) *ordersv1.GraduationCriterion {
	return &ordersv1.GraduationCriterion{Name: name, Required: required, Actual: actual, Passed: false, Source: source}
}

func hashProto(m proto.Message) string {
	c := proto.Clone(m)
	if r, ok := c.(*ordersv1.GraduationReport); ok {
		r.ReportHash = ""
	}
	if r, ok := c.(*ordersv1.LiveReadiness); ok {
		r.RecordHash = ""
	}
	b, _ := proto.MarshalOptions{Deterministic: true}.Marshal(c)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// ---- readiness (GL-010)

func attestedItems() []*ordersv1.ReadinessItem {
	type row struct{ id, section, title string }
	rows := []row{
		{"gl-security", "security", "Section 02 Stage B signed off; no unresolved high findings; credential-leak drill done; master key escrowed"},
		{"gl-ops", "operations", "Off-site backup restore drill within 30 days; market-hours deploy freeze; outage, re-auth, reconciliation, kill-switch, and rollback runbooks rehearsed"},
		{"gl-data", "data", "Feeds healthy for 10 consecutive sessions; live feed matches the validated feed, or the difference is documented and tested"},
		{"gl-risk-drill", "risk", "Live limit profile reviewed; kill-switch drill on this account (freeze and cancel, no orders left working)"},
		{"gl-monitoring", "monitoring", "Critical alerts verified on the owner's phone; dead-man alert verified; containment set to demote on breach"},
		{"gl-compliance", "compliance", "PDT status and day-trade count shown; wash-sale flags visible; orders, fills, and audit retained"},
		{"gl-docs", "documentation", "File map, business-logic docs, decisions, ADRs, and changelog current for this release"},
	}
	out := make([]*ordersv1.ReadinessItem, 0, len(rows))
	for _, r := range rows {
		out = append(out, &ordersv1.ReadinessItem{Id: r.id, Section: r.section, Title: r.title, Status: "pending"})
	}
	return out
}

func (e *Engine) baseReadiness(account string) *ordersv1.LiveReadiness {
	r := e.readiness[account]
	if r == nil {
		r = &ordersv1.LiveReadiness{BrokerAccountId: account}
		e.readiness[account] = r
	}
	have := map[string]*ordersv1.ReadinessItem{}
	for _, it := range r.Items {
		if !it.GetAutomated() {
			have[it.GetId()] = it
		}
	}
	items := attestedItems()
	for _, it := range items {
		if prev := have[it.GetId()]; prev != nil {
			it.Status, it.CheckedBy, it.CheckedAt, it.Note, it.EvidenceUrl = prev.GetStatus(), prev.GetCheckedBy(), prev.GetCheckedAt(), prev.GetNote(), prev.GetEvidenceUrl()
		}
	}
	r.Items = items
	return r
}

// LiveReadiness evaluates automated items and merges the owner's attestations (GL-010).
func (e *Engine) LiveReadiness(ctx context.Context, account string) (*ordersv1.LiveReadiness, error) {
	if account == "" {
		return nil, fmt.Errorf("%w: broker_account_id is required", ErrInvalid)
	}
	auto := e.automatedReadiness(ctx, account)
	e.mu.Lock()
	defer e.mu.Unlock()
	r := e.baseReadiness(account)
	r.Items = append(append([]*ordersv1.ReadinessItem{}, r.Items...), auto...)
	sort.Slice(r.Items, func(i, j int) bool { return r.Items[i].GetId() < r.Items[j].GetId() })
	r.Complete = true
	for _, it := range r.Items {
		if it.GetStatus() != "passed" {
			r.Complete = false
		}
	}
	r.UpdatedAt = e.now()
	r.RecordHash = hashProto(r)
	return proto.Clone(r).(*ordersv1.LiveReadiness), nil
}

func (e *Engine) automatedReadiness(ctx context.Context, account string) []*ordersv1.ReadinessItem {
	e.mu.Lock()
	a := e.accts[account]
	live := a != nil && a.info != nil && a.info.GetEnvironment() == brokerv1.Environment_ENVIRONMENT_LIVE
	sessions := len(e.cleanSess[account])
	flag := e.cfg.LiveFlag
	e.mu.Unlock()
	item := func(id, title, status, detail string) *ordersv1.ReadinessItem {
		return &ordersv1.ReadinessItem{Id: id, Section: "automated", Title: title, Status: status, Automated: true, Detail: detail}
	}
	flagStatus := "failed"
	if flag {
		flagStatus = "passed"
	}
	envStatus, envDetail := "failed", "account is not a live-environment adapter"
	if live {
		envStatus, envDetail = "passed", ""
	}
	roDetail := fmt.Sprintf("%d clean read-only sessions", sessions)
	roStatus := "failed"
	if sessions >= 1 {
		roStatus = "passed"
	}
	brStatus, brDetail := "passed", "0"
	if e.ledger != nil {
		if bl, ok := e.ledger.(interface {
			Breaks(context.Context, string) ([]*ledgerv1.Break, error)
		}); ok {
			bs, err := bl.Breaks(ctx, account)
			open := 0
			if err != nil {
				brStatus, brDetail = "failed", err.Error()
			} else {
				for _, b := range bs {
					if b.GetStatus() == ledgerv1.BreakStatus_BREAK_STATUS_OPEN {
						open++
					}
				}
				brDetail = fmt.Sprintf("%d", open)
				if open > 0 {
					brStatus = "failed"
				}
			}
		}
	}
	ownStatus, ownDetail := e.ownershipReady(ctx, account)
	return []*ordersv1.ReadinessItem{
		item("gl-flag", "TB_TRADING_LIVE_ENABLED is true", flagStatus, ""),
		item("gl-env", "Adapter environment is live", envStatus, envDetail),
		item("gl-readonly", "At least one clean read-only session reconciled", roStatus, roDetail),
		item("gl-breaks", "No unexplained reconciliation breaks", brStatus, brDetail),
		item("gl-ownership", "Strategies cannot sell or short manual holdings", ownStatus, ownDetail),
	}
}

func (e *Engine) ownershipReady(ctx context.Context, account string) (string, string) {
	if e.ledger == nil {
		return "failed", "no ledger"
	}
	pv, err := e.ledger.Portfolio(ctx, Manual)
	if err != nil {
		return "passed", "no manual book"
	}
	for _, pos := range pv.GetPositions() {
		if pd(pos.GetQty()).IsZero() {
			continue
		}
		c, err := e.ledger.Constraints(ctx, account, pos.GetInstrumentId(), "")
		if err != nil {
			return "failed", err.Error()
		}
		// A strategy must not be able to sell the manual quantity (VP-003). Max sellable for an
		// owner with no position of its own is zero while manual holds the shares.
		if pd(c.GetManualQty()).IsPositive() && pd(c.GetMaxSellableByStrategy()).IsPositive() && pd(c.GetStrategyQty()).IsZero() {
			return "failed", "max sellable includes quantity the strategy does not own in " + pos.GetInstrumentId()
		}
	}
	return "passed", ""
}

// SetReadinessItem records an owner attestation. Automated items and waivers are rejected (GL-010).
func (e *Engine) SetReadinessItem(ctx context.Context, account, itemID, status, note, evidenceURL string, actor *ordersv1.Actor) (*ordersv1.LiveReadiness, error) {
	if err := ownerStepUp(actor); err != nil {
		return nil, err
	}
	if status != "passed" && status != "failed" {
		return nil, fmt.Errorf("%w: status must be passed or failed", ErrInvalid)
	}
	ok := false
	for _, it := range attestedItems() {
		if it.GetId() == itemID {
			ok = true
		}
	}
	if !ok {
		return nil, fmt.Errorf("%w: %q is not an owner-attested checklist item", ErrInvalid, itemID)
	}
	e.mu.Lock()
	r := e.baseReadiness(account)
	for _, it := range r.Items {
		if it.GetId() == itemID {
			it.Status, it.CheckedBy, it.CheckedAt, it.Note, it.EvidenceUrl = status, actor.GetName(), e.now(), note, evidenceURL
		}
	}
	db := e.liveDB
	saved := proto.Clone(r).(*ordersv1.LiveReadiness)
	err := e.commit(ctx, &Tx{Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.live.readiness", Target: account + "/" + itemID,
		IP: actor.GetIp(), Device: actor.GetDevice(), Details: map[string]any{"status": status, "note": note}}}})
	e.mu.Unlock()
	if err != nil {
		return nil, err
	}
	if db != nil {
		_ = db.SaveReadiness(ctx, saved)
	}
	return e.LiveReadiness(ctx, account)
}

// ---- enablement (GL-011, GL-012, GL-031)

func ownerStepUp(actor *ordersv1.Actor) error {
	if err := notAnalyst(actor); err != nil {
		return err
	}
	if actor.GetName() != "owner" {
		return fmt.Errorf("%w: only the owner can do this", ErrForbidden)
	}
	if actor.GetStepUpMethod() != "passkey" {
		return ErrStepUp
	}
	return nil
}

func notAnalyst(actor *ordersv1.Actor) error {
	n := strings.ToLower(actor.GetName())
	if n == "assistant" || strings.Contains(n, "analyst") {
		return fmt.Errorf("%w: the analyst cannot enable or promote live trading", ErrForbidden)
	}
	return nil
}

func canRollback(actor *ordersv1.Actor) error {
	if err := notAnalyst(actor); err != nil {
		return err
	}
	n := actor.GetName()
	switch {
	case n == "owner", n == "service:monitor", n == "service:execution", strings.HasPrefix(n, "cli:"):
		return nil
	default:
		return fmt.Errorf("%w: %s cannot roll back live trading", ErrForbidden, n)
	}
}

// EnableLiveAccount opens the audited gate (GL-011). read_only lets the adapter stream and reconcile
// with orders refused; trading requires the complete readiness record, including a clean read-only day.
func (e *Engine) EnableLiveAccount(ctx context.Context, account, confirmation, stage string, actor *ordersv1.Actor) (*ordersv1.LiveAccountStatus, error) {
	if err := ownerStepUp(actor); err != nil {
		return nil, err
	}
	if confirmation != account || account == "" {
		return nil, fmt.Errorf("%w: type the account id to confirm", ErrInvalid)
	}
	if !e.cfg.LiveFlag {
		return nil, fmt.Errorf("%w: TB_TRADING_LIVE_ENABLED is not true", ErrForbidden)
	}
	e.mu.Lock()
	a := e.accts[account]
	live := a != nil && a.info != nil && a.info.GetEnvironment() == brokerv1.Environment_ENVIRONMENT_LIVE
	e.mu.Unlock()
	if !live {
		return nil, fmt.Errorf("%w: %s is not a live-environment account", ErrInvalid, account)
	}
	orders := false
	switch stage {
	case "", readOnlyStage:
		stage = readOnlyStage
	case tradingStage:
		r, err := e.LiveReadiness(ctx, account)
		if err != nil {
			return nil, err
		}
		if !r.GetComplete() {
			return nil, fmt.Errorf("%w: readiness checklist is not complete", ErrInvalid)
		}
		orders = true
	default:
		return nil, fmt.Errorf("%w: stage must be read_only or trading", ErrInvalid)
	}
	en := Enablement{Enabled: true, Orders: orders, By: actor.GetName(), Reason: stage, Updated: e.now()}
	e.mu.Lock()
	db := e.liveDB
	e.mu.Unlock()
	if db != nil {
		saved, err := db.PutEnablement(ctx, account, en)
		if err != nil {
			return nil, err
		}
		en = saved
	} else {
		en.Seq = e.now()
	}
	if en.Seq <= 0 {
		return nil, fmt.Errorf("%w: enablement was not audited", ErrInvalid)
	}
	e.mu.Lock()
	e.enablement[account] = en
	_ = e.commit(ctx, &Tx{Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.live.enable", Target: account,
		IP: actor.GetIp(), Device: actor.GetDevice(), Details: map[string]any{"stage": stage, "orders": orders, "audit_seq": en.Seq}}}})
	e.mu.Unlock()
	return e.liveAccount(account), nil
}

// DisableLiveAccount revokes enablement, freezes the account's platform orders, and demotes its
// live strategies (GL-031). Manual positions are never flattened.
func (e *Engine) DisableLiveAccount(ctx context.Context, account, reason string, actor *ordersv1.Actor) (*ordersv1.LiveAccountStatus, int32, []string, error) {
	if err := canRollback(actor); err != nil {
		return nil, 0, nil, err
	}
	if reason == "" {
		return nil, 0, nil, fmt.Errorf("%w: a reason is required", ErrInvalid)
	}
	en := Enablement{Enabled: false, Orders: false, By: actor.GetName(), Reason: reason, Updated: e.now()}
	e.mu.Lock()
	db := e.liveDB
	ids := e.liveStrategyIDs(account)
	e.mu.Unlock()
	if db != nil {
		if saved, err := db.PutEnablement(ctx, account, en); err == nil {
			en = saved
		}
	}
	e.mu.Lock()
	e.enablement[account] = en
	e.mu.Unlock()
	resp, err := e.Trigger(ctx, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT, account,
		ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN, "live account disabled: "+reason, actor)
	if err != nil {
		return nil, 0, nil, err
	}
	var demoted []string
	for _, id := range ids {
		if _, _, _, derr := e.DemoteFromLive(ctx, id, "live account disabled: "+reason, "hold", actor); derr == nil {
			demoted = append(demoted, id)
		}
	}
	return e.liveAccount(account), resp.GetOrdersCanceled(), demoted, nil
}

func (e *Engine) liveStrategyIDs(account string) []string {
	var ids []string
	for id, s := range e.strategies {
		if s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE && s.GetBrokerAccountId() == account {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	return ids
}

func (e *Engine) liveAccount(account string) *ordersv1.LiveAccountStatus {
	e.mu.Lock()
	defer e.mu.Unlock()
	en := e.enablement[account]
	st := &ordersv1.LiveAccountStatus{BrokerAccountId: account, Enabled: en.Enabled, OrdersEnabled: en.Orders, EnabledBy: en.By,
		AuditSeq: en.Seq, UpdatedAt: en.Updated, ReadOnlySessions: int32(len(e.cleanSess[account]))}
	if a := e.accts[account]; a != nil {
		if a.session != nil {
			st.SessionState = a.session.GetState().String()
		}
		if a.info != nil {
			if a.info.GetEnvironment() == brokerv1.Environment_ENVIRONMENT_LIVE {
				st.Environment = "live"
			} else {
				st.Environment = "paper"
			}
		}
	}
	for _, s := range e.strategies {
		if s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE && s.GetBrokerAccountId() == account {
			st.LiveStrategies = append(st.LiveStrategies, s.GetStrategyId())
		}
	}
	sort.Strings(st.LiveStrategies)
	return st
}

// LiveStatus is the go-live screen's payload (GL-011).
func (e *Engine) LiveStatus(ctx context.Context) *ordersv1.LiveStatus {
	e.mu.Lock()
	out := &ordersv1.LiveStatus{FlagEnabled: e.cfg.LiveFlag, AsOf: e.now()}
	seen := map[string]bool{}
	for id := range e.accts {
		seen[id] = true
	}
	for id := range e.enablement {
		seen[id] = true
	}
	var ids []string
	for id := range seen {
		ids = append(ids, id)
	}
	var papers []string
	for id, s := range e.strategies {
		if s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			out.LiveStrategies = append(out.LiveStrategies, proto.Clone(s).(*ordersv1.Strategy))
		}
		if s.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_PAPER && s.GetPaperTwinOf() == "" {
			papers = append(papers, id)
		}
	}
	e.mu.Unlock()
	sort.Strings(ids)
	for _, id := range ids {
		st := e.liveAccount(id)
		if r, err := e.LiveReadiness(ctx, id); err == nil {
			st.ReadinessComplete = r.GetComplete()
		}
		out.Accounts = append(out.Accounts, st)
	}
	sort.Strings(papers)
	for _, id := range papers {
		if rep, err := e.GraduationReport(ctx, id); err == nil {
			out.Candidates = append(out.Candidates, rep)
		}
	}
	return out
}

// ---- promotion (GL-020..GL-023) and rollback (GL-030, GL-040)

// PromoteToLive is the only path to mode live (GL-020).
func (e *Engine) PromoteToLive(ctx context.Context, req *ordersv1.PromoteToLiveRequest) (*ordersv1.PromoteToLiveResponse, error) {
	if err := ownerStepUp(req.GetActor()); err != nil {
		return nil, err
	}
	e.mu.Lock()
	s := e.strategies[req.GetStrategyId()]
	if s == nil {
		e.mu.Unlock()
		return nil, ErrNotFound
	}
	name := s.GetName()
	mode := s.GetMode()
	otherLive := false
	for id, o := range e.strategies {
		if id != s.GetStrategyId() && o.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			otherLive = true
		}
	}
	en := e.enablement[req.GetLiveAccountId()]
	clone := proto.Clone(s).(*ordersv1.Strategy)
	e.mu.Unlock()
	if req.GetConfirmation() != name || name == "" {
		return nil, fmt.Errorf("%w: type the strategy name to confirm", ErrInvalid)
	}
	if mode == ordersv1.StrategyMode_STRATEGY_MODE_LIVE && clone.GetBrokerAccountId() == req.GetLiveAccountId() {
		return &ordersv1.PromoteToLiveResponse{Strategy: clone}, nil
	}
	if mode != ordersv1.StrategyMode_STRATEGY_MODE_PAPER {
		return nil, fmt.Errorf("%w: only a paper strategy can be promoted", ErrInvalid)
	}
	if otherLive {
		return nil, fmt.Errorf("%w: one live strategy at a time on the first go-live", ErrInvalid)
	}
	if !e.cfg.LiveFlag || !en.Enabled || !en.Orders || en.Seq <= 0 {
		return nil, fmt.Errorf("%w: live account %s is not enabled for trading", ErrForbidden, req.GetLiveAccountId())
	}
	cap, intended := pd(req.GetCapitalCap()), pd(req.GetIntendedAllocation())
	if !intended.IsPositive() || !cap.IsPositive() {
		return nil, fmt.Errorf("%w: capital cap and intended allocation are required", ErrInvalid)
	}
	if cap.LessThan(intended.Mul(decimal.RequireFromString(capLow))) || cap.GreaterThan(intended.Mul(decimal.RequireFromString(capHigh))) {
		return nil, fmt.Errorf("%w: capital cap must be 10–20%% of the intended allocation", ErrInvalid)
	}
	rep, err := e.GraduationReport(ctx, req.GetStrategyId())
	if err != nil {
		return nil, err
	}
	if !rep.GetOverall() {
		return nil, fmt.Errorf("%w: graduation report has not passed", ErrInvalid)
	}
	profile := liveProfile(e, clone)
	if req.GetLimitOverrides() != nil && Raised(profile, req.GetLimitOverrides()) {
		return nil, fmt.Errorf("%w: limit overrides may only tighten the live profile", ErrInvalid)
	}
	if req.GetLimitOverrides() != nil {
		profile = req.GetLimitOverrides()
	}
	if _, err := e.SetLimits(ctx, "strategy:"+clone.GetStrategyId(), profile, req.GetActor()); err != nil {
		return nil, err
	}
	var twin *ordersv1.Strategy
	if !req.GetSkipPaperTwin() {
		twin, err = e.paperTwin(ctx, clone, intended, req.GetActor().GetName())
		if err != nil {
			return nil, err
		}
	}
	if err := e.ledger.SetAllocation(ctx, &ledgerv1.SetAllocationRequest{Owner: clone.GetStrategyId(), BrokerAccountId: req.GetLiveAccountId(),
		Amount: d(cap), Actor: req.GetActor().GetName()}); err != nil {
		return nil, fmt.Errorf("live allocation: %w", err)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	cur := e.strategies[clone.GetStrategyId()]
	if cur == nil || cur.GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_PAPER {
		return nil, fmt.Errorf("%w: strategy changed during promotion", ErrInvalid)
	}
	cur.Mode = ordersv1.StrategyMode_STRATEGY_MODE_LIVE
	cur.ModeReason = "promoted to live"
	cur.BrokerAccountId = req.GetLiveAccountId()
	cur.PaperAccountId = clone.GetBrokerAccountId()
	cur.LiveCapitalCap, cur.IntendedAllocation = d(cap), d(intended)
	cur.LadderStep, cur.LadderWindowStart = 0, e.now()
	cur.LivePromotedAt = e.now()
	cur.LiveEvidenceHash = rep.GetReportHash()
	cur.UpdatedAt = e.now()
	if err := e.commit(ctx, &Tx{Strategies: []*ordersv1.Strategy{cur}, Audit: []audit.Event{{Actor: req.GetActor().GetName(), Action: "execution.live.promote", Target: cur.GetStrategyId(),
		IP: req.GetActor().GetIp(), Device: req.GetActor().GetDevice(),
		Details: map[string]any{"account": req.GetLiveAccountId(), "cap": cap.String(), "intended": intended.String(), "evidence": rep.GetReportHash()}}}}); err != nil {
		cur.Mode = ordersv1.StrategyMode_STRATEGY_MODE_PAPER
		return nil, err
	}
	return &ordersv1.PromoteToLiveResponse{Strategy: proto.Clone(cur).(*ordersv1.Strategy), Report: rep, LiveLimits: profile, PaperTwin: twin}, nil
}

func liveProfile(e *Engine, s *ordersv1.Strategy) *ordersv1.RiskLimits {
	e.mu.Lock()
	defer e.mu.Unlock()
	cur, _ := e.limits["strategy:"+s.GetStrategyId()], e.limits["global"]
	base := proto.Clone(orEmpty(cur)).(*ordersv1.RiskLimits)
	half := func(v *commonv1.Decimal) *commonv1.Decimal {
		x := pd(v)
		if !x.IsPositive() {
			return nil
		}
		return d(x.Div(decimal.NewFromInt(2)))
	}
	base.MaxOrderNotional = half(base.GetMaxOrderNotional())
	base.MaxDailyLoss = half(base.GetMaxDailyLoss())
	base.MaxDrawdown = half(base.GetMaxDrawdown())
	if base.GetMaxPositions() > 1 {
		base.MaxPositions /= 2
	}
	collar := decimal.RequireFromString(liveCollar)
	if c := pd(base.GetPriceCollarPct()); !c.IsPositive() || c.GreaterThan(collar) {
		base.PriceCollarPct = d(collar)
	}
	if base.GetMaxOrdersPerMinute() == 0 || base.GetMaxOrdersPerMinute() > 10 {
		base.MaxOrdersPerMinute = 10
	}
	return base
}

func (e *Engine) paperTwin(ctx context.Context, s *ordersv1.Strategy, alloc decimal.Decimal, actor string) (*ordersv1.Strategy, error) {
	id := s.GetStrategyId() + "-paper"
	short := s.GetShortId()
	if len(short) >= 8 {
		short = short[:7] + "p"
	} else {
		short += "p"
	}
	twin := &ordersv1.Strategy{StrategyId: id, ShortId: short, Name: s.GetName() + " paper twin", Horizon: s.GetHorizon(),
		Mode: ordersv1.StrategyMode_STRATEGY_MODE_PAPER, BrokerAccountId: s.GetBrokerAccountId(), Version: s.GetVersion(),
		CodeCommit: s.GetCodeCommit(), ParamsHash: s.GetParamsHash(), AllowShort: s.GetAllowShort(),
		SegregationAllowlist: s.GetSegregationAllowlist(), RuntimeHosted: s.GetRuntimeHosted(), PaperTwinOf: s.GetStrategyId()}
	return e.RegisterStrategy(ctx, twin, alloc, actor)
}

// DemoteFromLive returns a live strategy to paper (GL-030). No step-up: it only reduces risk.
func (e *Engine) DemoteFromLive(ctx context.Context, id, reason, policy string, actor *ordersv1.Actor) (*ordersv1.Strategy, int32, int32, error) {
	if err := canRollback(actor); err != nil {
		return nil, 0, 0, err
	}
	if reason == "" {
		return nil, 0, 0, fmt.Errorf("%w: a reason is required", ErrInvalid)
	}
	if policy == "" {
		policy = "hold"
	}
	if policy != "hold" && policy != "flatten" {
		return nil, 0, 0, fmt.Errorf("%w: position policy must be hold or flatten", ErrInvalid)
	}
	e.mu.Lock()
	s := e.strategies[id]
	if s == nil {
		e.mu.Unlock()
		return nil, 0, 0, ErrNotFound
	}
	if s.GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
		out := proto.Clone(s).(*ordersv1.Strategy)
		e.mu.Unlock()
		return out, 0, 0, nil
	}
	paperAcct := s.GetPaperAccountId()
	var cancel []*ordersv1.Order
	for _, o := range e.orders {
		if o.GetStrategyId() == id && !IsFinal(o.GetState()) {
			cancel = append(cancel, o)
		}
	}
	e.mu.Unlock()
	n := int32(0)
	for _, o := range cancel {
		if e.cancelOrder(ctx, o) {
			n++
		}
	}
	flat := int32(0)
	if policy == "flatten" {
		e.mu.Lock()
		cur := e.strategies[id]
		e.mu.Unlock()
		flat = int32(e.flatten(ctx, []*ordersv1.Strategy{cur}, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, id, "demote"))
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	cur := e.strategies[id]
	cur.Mode = ordersv1.StrategyMode_STRATEGY_MODE_PAPER
	cur.ModeReason = "demoted from live: " + reason
	if paperAcct != "" {
		cur.BrokerAccountId = paperAcct
	}
	cur.UpdatedAt = e.now()
	if err := e.commit(ctx, &Tx{Strategies: []*ordersv1.Strategy{cur}, Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.live.demote", Target: id,
		Details: map[string]any{"reason": reason, "policy": policy, "orders_canceled": n}}}}); err != nil {
		return nil, n, flat, err
	}
	return proto.Clone(cur).(*ordersv1.Strategy), n, flat, nil
}

// RaiseLiveCap moves one ladder step, at most 2×, after the graduation report still passes (GL-040).
func (e *Engine) RaiseLiveCap(ctx context.Context, id string, newCap decimal.Decimal, confirmation string, actor *ordersv1.Actor) (*ordersv1.Strategy, *ordersv1.GraduationReport, error) {
	if err := ownerStepUp(actor); err != nil {
		return nil, nil, err
	}
	e.mu.Lock()
	s := e.strategies[id]
	if s == nil {
		e.mu.Unlock()
		return nil, nil, ErrNotFound
	}
	if s.GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
		e.mu.Unlock()
		return nil, nil, fmt.Errorf("%w: strategy is not live", ErrInvalid)
	}
	if confirmation != s.GetName() {
		e.mu.Unlock()
		return nil, nil, fmt.Errorf("%w: type the strategy name to confirm", ErrInvalid)
	}
	cur, intended := pd(s.GetLiveCapitalCap()), pd(s.GetIntendedAllocation())
	start := s.GetLadderWindowStart()
	acct := s.GetBrokerAccountId()
	e.mu.Unlock()
	if !newCap.IsPositive() || newCap.LessThanOrEqual(cur) || newCap.GreaterThan(cur.Mul(decimal.NewFromInt(2))) || (intended.IsPositive() && newCap.GreaterThan(intended)) {
		return nil, nil, fmt.Errorf("%w: a step is at most 2× and never above the intended allocation", ErrInvalid)
	}
	rep, err := e.GraduationReport(ctx, id)
	if err != nil {
		return nil, nil, err
	}
	if !rep.GetOverall() {
		return nil, nil, fmt.Errorf("%w: the live window has not passed the graduation report", ErrInvalid)
	}
	if rep.GetWindowEnd() < start || rep.GetPaperSessions() < minLiveSessions {
		return nil, nil, fmt.Errorf("%w: the ladder window needs %d sessions of evidence", ErrInvalid, minLiveSessions)
	}
	if err := e.ledger.SetAllocation(ctx, &ledgerv1.SetAllocationRequest{Owner: id, BrokerAccountId: acct, Amount: d(newCap), Actor: actor.GetName()}); err != nil {
		return nil, nil, err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	s = e.strategies[id]
	s.LiveCapitalCap = d(newCap)
	s.LadderStep++
	s.LadderWindowStart = e.now()
	s.UpdatedAt = e.now()
	if err := e.commit(ctx, &Tx{Strategies: []*ordersv1.Strategy{s}, Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.live.cap", Target: id,
		Details: map[string]any{"cap": newCap.String(), "step": s.GetLadderStep()}}}}); err != nil {
		return nil, nil, err
	}
	return proto.Clone(s).(*ordersv1.Strategy), rep, nil
}

// guardLive demotes any strategy whose live mode is not backed by the flag, the audited trading
// enablement, a promotion timestamp, and a cap (GL-050). A direct edit of the mode column is undone
// on the next tick and alerted.
func (e *Engine) guardLive(ctx context.Context) {
	e.noteCleanSession()
	e.mu.Lock()
	var bad []string
	for id, s := range e.strategies {
		if s.GetMode() != ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			continue
		}
		en := e.enablement[s.GetBrokerAccountId()]
		if !e.cfg.LiveFlag || !en.Orders || en.Seq <= 0 || s.GetLivePromotedAt() == 0 || !pd(s.GetLiveCapitalCap()).IsPositive() {
			bad = append(bad, id)
		}
	}
	e.mu.Unlock()
	for _, id := range bad {
		if _, _, _, err := e.DemoteFromLive(ctx, id, "live mode without an audited promotion", "hold", &ordersv1.Actor{Name: "service:execution"}); err == nil {
			e.mu.Lock()
			_ = e.commit(ctx, &Tx{Outbox: []outbox.Message{e.alert(alertsv1.Severity_SEVERITY_CRITICAL, "live mode rejected",
				id+" was live without the deploy flag, audited trading enablement, promotion record, and capital cap; demoted to paper",
				"live-guard-"+id, "docs/runbooks/live-rollback.md", "")}})
			e.mu.Unlock()
		}
	}
}

// noteCleanSession records a ready, freshly reconciled read-only live session once per date (GL-012).
func (e *Engine) noteCleanSession() {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.cfg.Calendar == nil {
		return
	}
	date := e.cfg.Now().UTC().Format(time.DateOnly)
	for acct, en := range e.enablement {
		if !en.Enabled || en.Orders {
			continue
		}
		a := e.accts[acct]
		if a == nil || a.session == nil || a.recon == nil {
			continue
		}
		if a.session.GetState() != brokerv1.SessionState_SESSION_STATE_READY {
			continue
		}
		if a.recon.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK {
			continue
		}
		if e.cleanSess[acct] == nil {
			e.cleanSess[acct] = map[string]struct{}{}
		}
		if _, ok := e.cleanSess[acct][date]; ok {
			continue
		}
		e.cleanSess[acct][date] = struct{}{}
		if e.liveDB != nil {
			_ = e.liveDB.AddSession(context.Background(), acct, date)
		}
	}
}
