package oms

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"github.com/shopspring/decimal"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/attribution"
	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/libs/go/outbox"
	"github.com/illyanyc/strawberry/services/execution/internal/risk"
)

// Errors returned to callers.
var (
	ErrNotFound          = errors.New("execution: not found")
	ErrInvalid           = errors.New("execution: invalid request")
	ErrStepUp            = errors.New("execution: desktop passkey step-up required")
	ErrForbidden         = errors.New("execution: forbidden")
	errUnknownInstrument = errors.New("execution: unknown instrument")
)

// Manual is the owner of owner-entered orders (VP-001).
const Manual = "manual"

type acctState struct {
	info           *brokerv1.BrokerAccount
	snap           *ledgerv1.AccountSnapshot
	recon          *ledgerv1.ReconciliationResult
	session        *brokerv1.SessionStatus
	polledAt       time.Time
	readyAt        int64
	notReadyAt     time.Time
	pagedDown      bool
	ordersRecon    bool
	lastOrderRecon time.Time
}

type mkt struct {
	last     decimal.Decimal
	close    int64
	interval int64
}

type roundTrip struct {
	qty, cost, pnl decimal.Decimal
}

type recentIntent struct {
	key string
	at  int64
}

// Engine is the OMS.
type Engine struct {
	cfg     Config
	store   Store
	brokers Brokers
	ledger  Ledger
	instr   Instruments
	log     *slog.Logger
	// Heartbeat publishes the trading status on sys.health.execution (dead-man, RM-017).
	Heartbeat func(*ordersv1.TradingStatus)

	seq sync.Mutex // serializes intent processing
	mu  sync.Mutex // guards everything below

	strategies map[string]*ordersv1.Strategy
	limits     map[string]*ordersv1.RiskLimits
	intents    map[string]*ordersv1.IntentRecord
	orders     map[string]*ordersv1.Order
	byBroker   map[string]string
	byClient   map[string][]string
	fills      map[string]*ordersv1.Fill
	kills      map[string]*ordersv1.KillSwitch
	halts      map[string]*ordersv1.Halt
	accts      map[string]*acctState
	market     map[string]*mkt
	recent     map[string][]recentIntent
	orderTimes map[string][]int64
	rejects    map[string][]int64
	sizes      map[string][]decimal.Decimal
	protect    map[string]*ordersv1.ProtectiveSpec
	trips      map[string]*roundTrip
	losses     map[string]int
	instrCache map[string]*brokerv1.InstrumentInfo
	flattened  map[string]string
	alerted    map[string]bool
	evidence   GraduationEvidence
	research   GraduationResearch
	liveDB     LiveDB
	readiness  map[string]*ordersv1.LiveReadiness
	cleanSess  map[string]map[string]struct{}
	enablement map[string]Enablement
	runtimeAt  time.Time
	started    time.Time
	deadman    bool
	recovering bool
	lastBeat   time.Time
	lastAccts  time.Time
}

// New loads the OMS state. The engine starts in recovery: no intents are approved until every
// account's open orders were reconciled with the broker (OE-020).
func New(ctx context.Context, cfg Config, store Store, brokers Brokers, ledger Ledger, instr Instruments) (*Engine, error) {
	if cfg.Log == nil {
		cfg.Log = slog.New(slog.DiscardHandler)
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.Wall == nil {
		cfg.Wall = time.Now
	}
	if cfg.ClockOK == nil {
		cfg.ClockOK = func() bool { return true }
	}
	e := &Engine{cfg: cfg, store: store, brokers: brokers, ledger: ledger, instr: instr, log: cfg.Log,
		strategies: map[string]*ordersv1.Strategy{}, limits: map[string]*ordersv1.RiskLimits{}, intents: map[string]*ordersv1.IntentRecord{},
		orders: map[string]*ordersv1.Order{}, byBroker: map[string]string{}, byClient: map[string][]string{}, fills: map[string]*ordersv1.Fill{},
		kills: map[string]*ordersv1.KillSwitch{}, halts: map[string]*ordersv1.Halt{}, accts: map[string]*acctState{}, market: map[string]*mkt{},
		recent: map[string][]recentIntent{}, orderTimes: map[string][]int64{}, rejects: map[string][]int64{}, sizes: map[string][]decimal.Decimal{},
		protect: map[string]*ordersv1.ProtectiveSpec{}, trips: map[string]*roundTrip{}, losses: map[string]int{}, instrCache: map[string]*brokerv1.InstrumentInfo{},
		flattened: map[string]string{}, alerted: map[string]bool{}, recovering: true, started: cfg.Wall()}
	st, err := store.Load(ctx)
	if err != nil {
		return nil, err
	}
	for _, s := range st.Strategies {
		e.strategies[s.GetStrategyId()] = s
	}
	for k, v := range st.Limits {
		e.limits[k] = v
	}
	if _, ok := e.limits["global"]; !ok && cfg.GlobalLimits != nil {
		e.limits["global"] = cfg.GlobalLimits
	}
	if _, ok := e.limits["manual"]; !ok && cfg.ManualLimits != nil {
		e.limits["manual"] = cfg.ManualLimits
	}
	for _, r := range st.Intents {
		e.intents[r.GetIntent().GetIntentId()] = r
	}
	for _, o := range st.Orders {
		e.index(o)
		if o.GetRole() == ordersv1.OrderRole_ORDER_ROLE_ENTRY {
			if it := e.intents[o.GetIntentId()]; it != nil && it.GetIntent().GetProtective() != nil && !e.hasProtective(o) {
				e.protect[o.GetOrderId()] = it.GetIntent().GetProtective()
			}
		}
	}
	for _, f := range st.Fills {
		e.fills[f.GetBrokerAccountId()+"|"+f.GetBrokerExecId()] = f
		e.trackTrip(f)
	}
	for _, k := range st.Kills {
		e.kills[k.GetEventId()] = k
	}
	for _, h := range st.Halts {
		e.halts[h.GetHaltId()] = h
	}
	e.initLive()
	return e, nil
}

func (e *Engine) now() int64 { return e.cfg.Now().UnixNano() }

func newID() string { return ulid.MustNew(ulid.Now(), rand.Reader).String() }

func (e *Engine) index(o *ordersv1.Order) {
	e.orders[o.GetOrderId()] = o
	if b := o.GetBrokerOrderId(); b != "" {
		e.byBroker[o.GetBrokerAccountId()+"|"+b] = o.GetOrderId()
	}
	key := o.GetBrokerAccountId() + "|" + o.GetClientOrderId()
	if !slices.Contains(e.byClient[key], o.GetOrderId()) {
		e.byClient[key] = append(e.byClient[key], o.GetOrderId())
	}
}

// IsFinal reports whether an order state is terminal.
func IsFinal(s ordersv1.OrderState) bool {
	switch s {
	case ordersv1.OrderState_ORDER_STATE_FILLED, ordersv1.OrderState_ORDER_STATE_CANCELED, ordersv1.OrderState_ORDER_STATE_REJECTED,
		ordersv1.OrderState_ORDER_STATE_EXPIRED, ordersv1.OrderState_ORDER_STATE_REPLACED:
		return true
	}
	return false
}

func isProtective(o *ordersv1.Order) bool {
	return o.GetRole() == ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP || o.GetRole() == ordersv1.OrderRole_ORDER_ROLE_TAKE_PROFIT
}

func (e *Engine) hasProtective(entry *ordersv1.Order) bool {
	for _, o := range e.orders {
		if o.GetParentOrderId() == entry.GetOrderId() && o.GetRole() == ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP {
			return true
		}
	}
	return false
}

func msg(subject, id string, m proto.Message, corr string) outbox.Message {
	b, _ := proto.Marshal(m)
	return outbox.Message{Subject: subject, MsgID: id, Payload: b, CorrelationID: corr}
}

func (e *Engine) alert(sev alertsv1.Severity, subject, text, dedupe, runbook, corr string) outbox.Message {
	a := &alertsv1.Alert{AlertId: newID(), Severity: sev, Source: "execution", Subject: subject, Message: text, DedupeKey: dedupe,
		RunbookUrl: runbook, OccurredAt: e.now(), CorrelationId: corr}
	sub := "alert.warning"
	switch sev {
	case alertsv1.Severity_SEVERITY_CRITICAL:
		sub = "alert.critical"
	case alertsv1.Severity_SEVERITY_INFO:
		sub = "alert.info"
	}
	return msg(sub, "alert:"+a.GetAlertId(), a, corr)
}

func (e *Engine) orderMsg(o *ordersv1.Order) outbox.Message {
	return msg("ord.order."+o.GetBrokerAccountId(), fmt.Sprintf("oms:%s:%s:%s:%d", o.GetOrderId(), o.GetState(), o.GetFilledQty().GetValue(), o.GetUpdatedAt()), o, o.GetCorrelationId())
}

// commit persists tx; on failure the in-memory change is kept but the caller fails closed (the
// intent is answered with an error and orders are not submitted).
func (e *Engine) commit(ctx context.Context, tx *Tx) error {
	if err := e.store.Commit(ctx, tx); err != nil {
		e.log.ErrorContext(ctx, "execution commit failed", "err", err)
		return err
	}
	return nil
}

// ---- registry

// RegisterStrategy creates or updates a registry entry and its ledger owner (VP-012, SM-001). A
// new entry may only start disabled, shadow, or paper (N4); an update never changes the mode.
func (e *Engine) RegisterStrategy(ctx context.Context, s *ordersv1.Strategy, initialAllocation decimal.Decimal, actor string) (*ordersv1.Strategy, error) {
	if s.GetStrategyId() == "" || s.GetStrategyId() == Manual || s.GetBrokerAccountId() == "" || s.GetHorizon() == commonv1.Horizon_HORIZON_UNSPECIFIED {
		return nil, fmt.Errorf("%w: strategy_id, broker_account_id, and horizon are required", ErrInvalid)
	}
	if _, err := attribution.Parse(attribution.ID{StrategyShortID: s.GetShortId(), IntentID: "00000000000000000000000000", Attempt: 1}.String(), 0); err != nil || s.GetShortId() == attribution.ManualShortID {
		return nil, fmt.Errorf("%w: short_id must be 6–8 lowercase alphanumerics", ErrInvalid)
	}
	e.mu.Lock()
	prev := e.strategies[s.GetStrategyId()]
	for id, x := range e.strategies {
		if id != s.GetStrategyId() && x.GetShortId() == s.GetShortId() {
			e.mu.Unlock()
			return nil, fmt.Errorf("%w: short_id %q is used by %s", ErrInvalid, s.GetShortId(), id)
		}
	}
	n := proto.Clone(s).(*ordersv1.Strategy)
	switch {
	case prev != nil:
		n.Mode, n.ModeReason = prev.GetMode(), prev.GetModeReason()
	case n.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_LIVE || n.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_UNSPECIFIED:
		n.Mode = ordersv1.StrategyMode_STRATEGY_MODE_DISABLED
	}
	n.UpdatedAt = e.now()
	e.mu.Unlock()
	if err := e.ledger.RegisterOwner(ctx, &ledgerv1.RegisterOwnerRequest{Owner: n.GetStrategyId(), ShortId: n.GetShortId(),
		BrokerAccountId: n.GetBrokerAccountId(), AllowShort: n.GetAllowShort()}); err != nil {
		return nil, fmt.Errorf("register ledger owner: %w", err)
	}
	if prev == nil && initialAllocation.IsPositive() {
		if err := e.ledger.SetAllocation(ctx, &ledgerv1.SetAllocationRequest{Owner: n.GetStrategyId(), BrokerAccountId: n.GetBrokerAccountId(),
			Amount: d(initialAllocation), Actor: actor}); err != nil {
			e.log.Warn("initial allocation rejected", "strategy", n.GetStrategyId(), "err", err)
		}
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	e.strategies[n.GetStrategyId()] = n
	return n, e.commit(ctx, &Tx{Strategies: []*ordersv1.Strategy{n}, Audit: []audit.Event{{Actor: actor, Action: "execution.strategy.register",
		Target: n.GetStrategyId(), Details: map[string]any{"short_id": n.GetShortId(), "account": n.GetBrokerAccountId(), "horizon": n.GetHorizon().String(), "mode": n.GetMode().String()}}}})
}

// SetMode changes a strategy's mode (SM-012). `live` is never accepted here: promotion to live is
// the section 15 go-live flow (SM-004, N4).
func (e *Engine) SetMode(ctx context.Context, id string, m ordersv1.StrategyMode, reason, actor string) (*ordersv1.Strategy, error) {
	switch m {
	case ordersv1.StrategyMode_STRATEGY_MODE_DISABLED, ordersv1.StrategyMode_STRATEGY_MODE_SHADOW, ordersv1.StrategyMode_STRATEGY_MODE_PAPER,
		ordersv1.StrategyMode_STRATEGY_MODE_HALTED:
	default:
		return nil, fmt.Errorf("%w: mode %s cannot be set here (live needs the section 15 go-live flow)", ErrInvalid, m)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	s := e.strategies[id]
	if s == nil {
		return nil, ErrNotFound
	}
	prev := s.GetMode()
	s.Mode, s.ModeReason, s.UpdatedAt = m, reason, e.now()
	return s, e.commit(ctx, &Tx{Strategies: []*ordersv1.Strategy{s}, Audit: []audit.Event{{Actor: actor, Action: "execution.strategy.mode", Target: id,
		Details: map[string]any{"from": prev.String(), "to": m.String(), "reason": reason}}}})
}

// Strategies lists the registry.
func (e *Engine) Strategies() []*ordersv1.Strategy {
	e.mu.Lock()
	defer e.mu.Unlock()
	out := make([]*ordersv1.Strategy, 0, len(e.strategies))
	for _, s := range e.strategies {
		c := proto.Clone(s).(*ordersv1.Strategy)
		c.Limits = e.limits["strategy:"+s.GetStrategyId()]
		out = append(out, c)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetStrategyId() < out[j].GetStrategyId() })
	return out
}

// ---- limits

func orEmpty(l *ordersv1.RiskLimits) *ordersv1.RiskLimits {
	if l == nil {
		return &ordersv1.RiskLimits{}
	}
	return l
}

// nonZero returns a copy of l without zero fields (proto.Merge would otherwise keep set-but-zero
// decimals as overrides): zero means "inherit the global default" in an owner scope.
func nonZero(l *ordersv1.RiskLimits) *ordersv1.RiskLimits {
	out := &ordersv1.RiskLimits{}
	if l == nil {
		return out
	}
	m, o := l.ProtoReflect(), out.ProtoReflect()
	m.Range(func(fd protoreflect.FieldDescriptor, v protoreflect.Value) bool {
		switch {
		case fd.Kind() == protoreflect.MessageKind:
			if d, ok := v.Message().Interface().(*commonv1.Decimal); ok && pd(d).IsZero() {
				return true
			}
		case v.Int() == 0:
			return true
		}
		o.Set(fd, v)
		return true
	})
	return out
}

func limitScopes(acct, strategy string, manual bool) []string {
	if manual {
		return []string{"global", "account:" + acct, "manual"}
	}
	return []string{"global", "account:" + acct, "strategy:" + strategy}
}

// effective merges limit scopes (RM-003, RM-019). The owner scope (`strategy:<id>` or `manual`)
// replaces the `global` default field by field; the `account:<id>` scope is a ceiling, so for each
// field the tighter non-zero value of (owner-or-global, account) wins. Loosening an owner scope past
// the global default therefore needs the same passkey step-up as any raise (SetLimits, RM-007).
func (e *Engine) effective(acct, strategy string, manual bool) risk.Limits {
	scopes := limitScopes(acct, strategy, manual)
	base := proto.Clone(orEmpty(e.limits[scopes[0]])).(*ordersv1.RiskLimits)
	proto.Merge(base, nonZero(e.limits[scopes[2]]))
	var l risk.Limits
	tight := func(cur *decimal.Decimal, v *commonv1.Decimal) {
		x := pd(v)
		if x.IsPositive() && (cur.IsZero() || x.LessThan(*cur)) {
			*cur = x
		}
	}
	tightI := func(cur *int, v int32) {
		if v > 0 && (*cur == 0 || int(v) < *cur) {
			*cur = int(v)
		}
	}
	for _, r := range []*ordersv1.RiskLimits{base, e.limits[scopes[1]]} {
		if r == nil {
			continue
		}
		tight(&l.MaxOrderNotional, r.GetMaxOrderNotional())
		tight(&l.MaxOrderQty, r.GetMaxOrderQty())
		tight(&l.MaxPositionNotional, r.GetMaxPositionNotional())
		tight(&l.MaxPositionPctAllocation, r.GetMaxPositionPctAllocation())
		tight(&l.MaxGrossExposure, r.GetMaxGrossExposure())
		tight(&l.MaxNetExposure, r.GetMaxNetExposure())
		tight(&l.MaxConcentrationPct, r.GetMaxConcentrationPct())
		tight(&l.MaxDailyLoss, r.GetMaxDailyLoss())
		tight(&l.MaxDrawdown, r.GetMaxDrawdown())
		tight(&l.PriceCollarPct, r.GetPriceCollarPct())
		tight(&l.FatFingerMultiple, r.GetFatFingerMultiple())
		tightI(&l.MaxPositions, r.GetMaxPositions())
		tightI(&l.MaxOrdersPerMinute, r.GetMaxOrdersPerMinute())
		tightI(&l.MaxCancelsPerMinute, r.GetMaxCancelsPerMinute())
		tightI(&l.MaxConsecutiveLosses, r.GetMaxConsecutiveLosses())
		tightI(&l.DuplicateWindowSeconds, r.GetDuplicateWindowSeconds())
		tightI(&l.MaxOpenOrders, r.GetMaxOpenOrders())
	}
	return l
}

// Limits returns a scope's limits and the effective values.
func (e *Engine) Limits(scope string) (*ordersv1.RiskLimits, *ordersv1.RiskLimits) {
	e.mu.Lock()
	defer e.mu.Unlock()
	l := e.limits[scope]
	if l == nil {
		l = &ordersv1.RiskLimits{}
	}
	acct, sid, manual := "", "", scope == "manual"
	if a, ok := strings.CutPrefix(scope, "account:"); ok {
		acct = a
	}
	if s, ok := strings.CutPrefix(scope, "strategy:"); ok {
		sid = s
		if x := e.strategies[s]; x != nil {
			acct = x.GetBrokerAccountId()
		}
	}
	ef := e.effective(acct, sid, manual)
	return proto.Clone(l).(*ordersv1.RiskLimits), limitsProto(ef)
}

func limitsProto(l risk.Limits) *ordersv1.RiskLimits {
	return &ordersv1.RiskLimits{MaxOrderNotional: d(l.MaxOrderNotional), MaxOrderQty: d(l.MaxOrderQty), MaxPositionNotional: d(l.MaxPositionNotional),
		MaxPositionPctAllocation: d(l.MaxPositionPctAllocation), MaxGrossExposure: d(l.MaxGrossExposure), MaxNetExposure: d(l.MaxNetExposure),
		MaxPositions: int32(l.MaxPositions), MaxConcentrationPct: d(l.MaxConcentrationPct), MaxDailyLoss: d(l.MaxDailyLoss), MaxDrawdown: d(l.MaxDrawdown),
		MaxOrdersPerMinute: int32(l.MaxOrdersPerMinute), MaxCancelsPerMinute: int32(l.MaxCancelsPerMinute), MaxConsecutiveLosses: int32(l.MaxConsecutiveLosses),
		PriceCollarPct: d(l.PriceCollarPct), DuplicateWindowSeconds: int32(l.DuplicateWindowSeconds), FatFingerMultiple: d(l.FatFingerMultiple), MaxOpenOrders: int32(l.MaxOpenOrders)}
}

// Raised reports whether next loosens any field of prev (zero = unlimited counts as loosening).
func Raised(prev, next *ordersv1.RiskLimits) bool {
	looser := func(a, b *commonv1.Decimal) bool {
		x, y := pd(a), pd(b)
		return (y.IsZero() && x.IsPositive()) || (x.IsPositive() && y.GreaterThan(x))
	}
	looserI := func(a, b int32) bool { return (b == 0 && a > 0) || (a > 0 && b > a) }
	return looser(prev.GetMaxOrderNotional(), next.GetMaxOrderNotional()) || looser(prev.GetMaxOrderQty(), next.GetMaxOrderQty()) ||
		looser(prev.GetMaxPositionNotional(), next.GetMaxPositionNotional()) || looser(prev.GetMaxPositionPctAllocation(), next.GetMaxPositionPctAllocation()) ||
		looser(prev.GetMaxGrossExposure(), next.GetMaxGrossExposure()) || looser(prev.GetMaxNetExposure(), next.GetMaxNetExposure()) ||
		looser(prev.GetMaxConcentrationPct(), next.GetMaxConcentrationPct()) || looser(prev.GetMaxDailyLoss(), next.GetMaxDailyLoss()) ||
		looser(prev.GetMaxDrawdown(), next.GetMaxDrawdown()) || looser(prev.GetPriceCollarPct(), next.GetPriceCollarPct()) ||
		looser(prev.GetFatFingerMultiple(), next.GetFatFingerMultiple()) || looserI(prev.GetMaxPositions(), next.GetMaxPositions()) ||
		looserI(prev.GetMaxOrdersPerMinute(), next.GetMaxOrdersPerMinute()) || looserI(prev.GetMaxCancelsPerMinute(), next.GetMaxCancelsPerMinute()) ||
		looserI(prev.GetMaxConsecutiveLosses(), next.GetMaxConsecutiveLosses()) || looserI(prev.GetMaxOpenOrders(), next.GetMaxOpenOrders()) ||
		(next.GetDuplicateWindowSeconds() < prev.GetDuplicateWindowSeconds())
}

// SetLimits replaces a scope's limits (RM-007: raising needs step-up, checked here as well as in
// the gateway). It returns whether any value was loosened.
func (e *Engine) SetLimits(ctx context.Context, scope string, l *ordersv1.RiskLimits, actor *ordersv1.Actor) (bool, error) {
	if scope != "global" && scope != "manual" && !strings.HasPrefix(scope, "account:") && !strings.HasPrefix(scope, "strategy:") {
		return false, fmt.Errorf("%w: scope %q", ErrInvalid, scope)
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	prev := e.limits[scope]
	if prev == nil {
		prev = &ordersv1.RiskLimits{}
	}
	raised := Raised(prev, l)
	// An owner scope replaces the global default (RM-019): compare what the gate would enforce, so a
	// first-time strategy limit above the global default is a raise too.
	sid, isStrategy := strings.CutPrefix(scope, "strategy:")
	if isStrategy || scope == "manual" {
		acct := ""
		if s := e.strategies[sid]; s != nil {
			acct = s.GetBrokerAccountId()
		}
		before := limitsProto(e.effective(acct, sid, scope == "manual"))
		e.limits[scope] = l
		after := limitsProto(e.effective(acct, sid, scope == "manual"))
		e.limits[scope] = prev
		raised = raised || Raised(before, after)
	}
	if raised && actor.GetStepUpMethod() != "passkey" {
		return true, ErrStepUp
	}
	e.limits[scope] = proto.Clone(l).(*ordersv1.RiskLimits)
	return raised, e.commit(ctx, &Tx{Limits: map[string]*ordersv1.RiskLimits{scope: l}, Audit: []audit.Event{{Actor: actor.GetName(), Action: "execution.limits.set",
		Target: scope, IP: actor.GetIp(), Device: actor.GetDevice(), BeforeHash: audit.StateHash(prev.String()), AfterHash: audit.StateHash(l.String()),
		Details: map[string]any{"raised": raised, "step_up": actor.GetStepUpMethod()}}}})
}

// ---- market, account, and recon state (bus inputs)

// OnBar records the latest final bar (freshness and last price, RM-004).
func (e *Engine) OnBar(instr string, close decimal.Decimal, tsClose, interval int64) {
	e.mu.Lock()
	defer e.mu.Unlock()
	m := e.market[instr]
	if m == nil {
		m = &mkt{}
		e.market[instr] = m
	}
	if tsClose >= m.close || interval < m.interval {
		m.last, m.close, m.interval = close, tsClose, interval
	}
}

// maxBarAge is the RM-004 freshness bound: two bar intervals plus the finality window. Daily (and
// longer) bars count trading sessions, not wall time, so a weekend or holiday is not a missing bar:
// the bar may be as old as the close of the session before the last closed one (one bar missing,
// the same allowance as two intervals mid-week).
func maxBarAge(cal *calendar.Calendar, now, interval, finality int64) int64 {
	wall := 2*interval + finality
	if interval < int64(24*time.Hour) || cal == nil {
		return wall
	}
	last, ok := cal.PreviousSession(time.Unix(0, now-finality))
	if !ok {
		return wall
	}
	prev, ok := cal.PreviousSession(last.Close.Add(-time.Nanosecond))
	if !ok {
		return wall
	}
	return max(wall, now-prev.Close.UnixNano()+finality)
}

func (e *Engine) acct(id string) *acctState {
	a := e.accts[id]
	if a == nil {
		a = &acctState{}
		e.accts[id] = a
	}
	return a
}

// OnSnapshot records an account snapshot. A broker margin event or trading restriction latches an
// account kill switch (freeze + cancel open strategy orders) and pages (RM-013).
func (e *Engine) OnSnapshot(ctx context.Context, s *ledgerv1.AccountSnapshot) error {
	e.mu.Lock()
	a := e.acct(s.GetBrokerAccountId())
	if a.snap != nil && a.snap.GetAsOf() > s.GetAsOf() {
		e.mu.Unlock()
		return nil
	}
	a.snap = s
	event := s.GetIntradayMarginDeficit() || s.GetMarginCall() || s.GetTradingBlocked() || s.GetAccountBlocked()
	already := false
	for _, k := range e.kills {
		if k.GetLatched() && k.GetScope() == ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT && k.GetTarget() == s.GetBrokerAccountId() && k.GetEngagedBy() == "risk:margin" {
			already = true
		}
	}
	e.mu.Unlock()
	if event && !already {
		reason := fmt.Sprintf("broker reports imd=%t margin_call=%t trading_blocked=%t account_blocked=%t", s.GetIntradayMarginDeficit(), s.GetMarginCall(), s.GetTradingBlocked(), s.GetAccountBlocked())
		_, err := e.Trigger(ctx, ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT, s.GetBrokerAccountId(), ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN,
			reason, &ordersv1.Actor{Name: "risk:margin"})
		return err
	}
	return nil
}

// OnRecon records a ledger reconciliation result (halted pairs, VP-008).
func (e *Engine) OnRecon(r *ledgerv1.ReconciliationResult) {
	e.mu.Lock()
	defer e.mu.Unlock()
	a := e.acct(r.GetBrokerAccountId())
	if a.recon == nil || r.GetAsOf() >= a.recon.GetAsOf() {
		a.recon = r
	}
}

// OnRuntimeHeartbeat records the strategy runtime heartbeat (wall clock, RM-017).
func (e *Engine) OnRuntimeHeartbeat() {
	e.mu.Lock()
	e.runtimeAt = e.cfg.Wall()
	e.mu.Unlock()
}

// ---- halts

func haltKey(acct, strategy, instr, reason string) string {
	return acct + "|" + strategy + "|" + instr + "|" + reason
}

func (e *Engine) addHalt(acct, strategy, instr, reason, source string, tx *Tx) {
	id := haltKey(acct, strategy, instr, source)
	if h := e.halts[id]; h != nil && h.GetClearedAt() == 0 {
		return
	}
	h := &ordersv1.Halt{HaltId: id, BrokerAccountId: acct, StrategyId: strategy, InstrumentId: instr, Reason: reason, Since: e.now(), Source: source}
	e.halts[id] = h
	tx.Halts = append(tx.Halts, h)
	tx.Audit = append(tx.Audit, audit.Event{Actor: "service:execution", Action: "execution.halt", Target: id, Details: map[string]any{"reason": reason}})
	tx.Outbox = append(tx.Outbox, e.alert(alertsv1.Severity_SEVERITY_CRITICAL, "strategy halted: "+source, fmt.Sprintf("%s %s %s: %s", acct, strategy, instr, reason),
		"halt-"+id, "docs/runbooks/kill-switch.md", ""))
}

func (e *Engine) clearHalt(id string, tx *Tx) {
	h := e.halts[id]
	if h == nil || h.GetClearedAt() != 0 {
		return
	}
	h.ClearedAt = e.now()
	tx.Halts = append(tx.Halts, h)
	tx.Audit = append(tx.Audit, audit.Event{Actor: "service:execution", Action: "execution.halt.clear", Target: id})
	delete(e.halts, id)
}

func (e *Engine) activeHalts(acct, strategy, instr string) []string {
	var out []string
	for _, h := range e.halts {
		if h.GetClearedAt() != 0 || h.GetBrokerAccountId() != acct {
			continue
		}
		if (h.GetStrategyId() == "" || h.GetStrategyId() == strategy) && (h.GetInstrumentId() == "" || h.GetInstrumentId() == instr) {
			out = append(out, h.GetReason())
		}
	}
	sort.Strings(out)
	return out
}

// ---- intent intake

// Submit runs an intent through the gate and, when approved, the OMS (OE-001, RM-001). It is
// idempotent on intent_id (OE-006).
func (e *Engine) Submit(ctx context.Context, it *ordersv1.OrderIntent, actor *ordersv1.Actor) (*ordersv1.SubmitIntentResponse, error) {
	e.seq.Lock()
	defer e.seq.Unlock()
	return e.process(ctx, it, actor, false)
}

// Preview is a dry run of the gate: no reservation, no order, no decision record (UI ticket).
func (e *Engine) Preview(ctx context.Context, it *ordersv1.OrderIntent) (*ordersv1.PreviewIntentResponse, error) {
	if it.GetIntentId() == "" {
		it = proto.Clone(it).(*ordersv1.OrderIntent)
		it.IntentId = newID()
	}
	r, err := e.process(ctx, it, nil, true)
	if err != nil {
		return nil, err
	}
	resp := &ordersv1.PreviewIntentResponse{Decision: r.GetDecision()}
	if len(r.GetOrders()) > 0 {
		o := r.GetOrders()[0]
		resp.EstimatedPrice = o.GetLimitPrice()
		resp.EstimatedNotional = d(pd(o.GetLimitPrice()).Mul(pd(o.GetQuantity())))
	}
	if it.GetStrategyId() == Manual || it.GetStrategyId() == "" {
		resp.OwnershipNote = "this order affects manual holdings (owner: manual)"
	} else {
		resp.OwnershipNote = "this order affects only strategy " + it.GetStrategyId() + "'s virtual portfolio"
	}
	return resp, nil
}

func (e *Engine) process(ctx context.Context, it *ordersv1.OrderIntent, actor *ordersv1.Actor, dry bool) (*ordersv1.SubmitIntentResponse, error) {
	e.mu.Lock()
	if !dry {
		if prev := e.intents[it.GetIntentId()]; prev != nil {
			resp := &ordersv1.SubmitIntentResponse{Decision: prev.GetDecision(), Duplicate: true}
			for _, o := range e.orders {
				if o.GetIntentId() == it.GetIntentId() {
					resp.Orders = append(resp.Orders, o)
				}
			}
			e.mu.Unlock()
			return resp, nil
		}
		if _, err := ulid.ParseStrict(it.GetIntentId()); err != nil {
			e.mu.Unlock()
			return nil, fmt.Errorf("%w: intent_id must be a ULID (VP-002)", ErrInvalid)
		}
	}
	in, plan := e.buildInput(ctx, it)
	ok, checks := risk.Evaluate(in)
	shadow := in.Strategy != nil && in.Strategy.GetMode() == ordersv1.StrategyMode_STRATEGY_MODE_SHADOW
	dec := &ordersv1.RiskDecision{IntentId: it.GetIntentId(), Approved: ok, Checks: checks, DecidedAt: e.now(), CorrelationId: it.GetCorrelationId(),
		StrategyId: it.GetStrategyId(), BrokerAccountId: it.GetBrokerAccountId(), Shadow: shadow, DryRun: dry}
	if dry {
		dec.FirstFailure = risk.First(checks)
		e.mu.Unlock()
		return &ordersv1.SubmitIntentResponse{Decision: dec, Orders: plan.preview()}, nil
	}
	if ok && !shadow {
		side := it.GetSide()
		res, err := e.ledger.Reserve(ctx, &ledgerv1.ReserveForOrderRequest{IntentId: it.GetIntentId(), Owner: plan.owner, BrokerAccountId: it.GetBrokerAccountId(),
			InstrumentId: it.GetInstrumentId(), Side: side, Qty: d(in.Qty), EstPrice: d(in.Price), CorrelationId: it.GetCorrelationId()})
		c := &ordersv1.RiskCheck{Name: "capital", Passed: err == nil && res.GetReserved(), Value: d(in.Notional).GetValue(),
			Limit: res.GetCapital().GetBuyingPowerAvailable().GetValue()}
		if err != nil {
			c.Detail = "ledger unavailable: " + status.Convert(err).Message()
		} else if !res.GetReserved() {
			c.Detail = res.GetRejectReason()
		}
		dec.Checks = append(dec.Checks, c)
		dec.Approved = c.Passed
	}
	dec.FirstFailure = risk.First(dec.Checks)
	state := IntentApproved
	switch {
	case !dec.GetApproved():
		state = IntentRejected
	case shadow:
		state = IntentShadow
	}
	rec := &ordersv1.IntentRecord{Intent: it, Decision: dec, State: state, ReceivedAt: e.now()}
	e.intents[it.GetIntentId()] = rec
	if state == IntentApproved {
		// Only approved intents count for duplicate and fat-finger checks: a corrected retry of a
		// rejected intent is not a duplicate.
		e.remember(it, in.Qty)
	}
	actorName := "service:strategy-runtime"
	if actor != nil && actor.GetName() != "" {
		actorName = actor.GetName()
	}
	checkMap := map[string]any{}
	for _, c := range dec.GetChecks() {
		checkMap[c.GetName()] = map[string]any{"passed": c.GetPassed(), "value": c.GetValue(), "limit": c.GetLimit()}
	}
	tx := &Tx{Intents: []*ordersv1.IntentRecord{rec}, Audit: []audit.Event{{Actor: actorName, Action: "execution.risk.decision", Target: it.GetIntentId(),
		CorrelationID: it.GetCorrelationId(), IP: actor.GetIp(), Device: actor.GetDevice(),
		Details: map[string]any{"approved": dec.GetApproved(), "shadow": shadow, "strategy": it.GetStrategyId(), "account": it.GetBrokerAccountId(),
			"instrument": it.GetInstrumentId(), "side": it.GetSide().String(), "qty": in.Qty.String(), "first_failure": dec.GetFirstFailure(), "checks": checkMap}}},
		Outbox: []outbox.Message{msg("ord.decision", "decision:"+it.GetIntentId(), dec, it.GetCorrelationId())}}
	if err := e.commit(ctx, tx); err != nil {
		delete(e.intents, it.GetIntentId())
		e.mu.Unlock()
		if dec.GetApproved() && !shadow {
			_ = e.ledger.Release(ctx, it.GetIntentId())
		}
		return nil, err
	}
	if state != IntentApproved {
		e.mu.Unlock()
		return &ordersv1.SubmitIntentResponse{Decision: dec}, nil
	}
	e.mu.Unlock()
	orders := e.execute(ctx, rec, plan)
	return &ordersv1.SubmitIntentResponse{Decision: dec, Orders: orders}, nil
}

func (e *Engine) remember(it *ordersv1.OrderIntent, qty decimal.Decimal) {
	sid := it.GetStrategyId()
	key := fmt.Sprintf("%s|%s|%s|%s|%s", it.GetBrokerAccountId(), it.GetInstrumentId(), it.GetSide(), qty, it.GetKind())
	now := e.now()
	keep := e.recent[sid][:0]
	for _, r := range e.recent[sid] {
		if now-r.at < int64(5*time.Minute) {
			keep = append(keep, r)
		}
	}
	e.recent[sid] = append(keep, recentIntent{key: key, at: now})
	e.sizes[sid] = append(e.sizes[sid], qty)
	if len(e.sizes[sid]) > 20 {
		e.sizes[sid] = e.sizes[sid][len(e.sizes[sid])-20:]
	}
}

// plan is the order the OMS will build for an approved intent.
type plan struct {
	owner, short string
	inst         *commonv1.Instrument
	caps         *brokerv1.Capabilities
	qty, price   decimal.Decimal
	orderType    ordersv1.OrderType
	tif          ordersv1.TimeInForce
	close        bool
	bracket      bool
}

func (p plan) preview() []*ordersv1.Order {
	if p.inst == nil {
		return nil
	}
	return []*ordersv1.Order{{Quantity: d(p.qty), LimitPrice: d(p.price), OrderType: p.orderType, Tif: p.tif}}
}

func roundTick(p decimal.Decimal, up bool) decimal.Decimal {
	if up {
		return p.RoundCeil(2)
	}
	return p.RoundFloor(2)
}

// buildInput gathers every gate input under the engine lock (ledger calls are in-process or local
// gRPC and fast).
func (e *Engine) buildInput(ctx context.Context, it *ordersv1.OrderIntent) (*risk.Input, plan) {
	acct, instr := it.GetBrokerAccountId(), it.GetInstrumentId()
	manual := it.GetStrategyId() == Manual || it.GetSource() == ordersv1.IntentSource_INTENT_SOURCE_UI
	st := e.strategies[it.GetStrategyId()]
	if manual {
		st = nil
	}
	p := plan{owner: it.GetStrategyId(), close: it.GetKind() == ordersv1.IntentKind_INTENT_KIND_CLOSE}
	if manual {
		p.owner, p.short = Manual, attribution.ManualShortID
	} else if st != nil {
		p.short = st.GetShortId()
	}
	in := &risk.Input{Intent: it, Strategy: st, Manual: manual, Close: p.close, Now: e.now(), ClockOK: e.cfg.ClockOK(),
		Buy: it.GetSide() == commonv1.Side_SIDE_BUY, Short: it.GetSide() == commonv1.Side_SIDE_SELL_SHORT}
	if st != nil {
		in.Routing = e.cfg.Routing[st.GetHorizon()]
		in.SegregationAllowed = slices.Contains(st.GetSegregationAllowlist(), instr)
	}
	in.Limits = e.effective(acct, it.GetStrategyId(), manual)
	qty := pd(it.GetQuantity())
	a := e.acct(acct)
	if a.info != nil {
		p.caps = a.info.GetCapabilities()
		p.bracket = p.caps.GetSupportsBracket()
	}
	if !p.caps.GetSupportsFractional() {
		qty = qty.Floor()
	}
	in.Qty, p.qty = qty, qty
	if m := e.market[instr]; m != nil {
		in.LastPrice, in.LastBarClose = m.last, m.close
		in.MaxBarAge = maxBarAge(e.cfg.Calendar, in.Now, m.interval, int64(e.cfg.FinalityDelay))
	}
	// Order type and price (OE-013): marketable limit with a collar instead of a market order.
	p.orderType, p.tif = ordersv1.OrderType_ORDER_TYPE_LIMIT, ordersv1.TimeInForce_TIME_IN_FORCE_DAY
	if it.GetTif() == ordersv1.TimeInForce_TIME_IN_FORCE_GTC {
		p.tif = it.GetTif()
	}
	if lp := pd(it.GetLimitPrice()); it.GetOrderType() == ordersv1.OrderType_ORDER_TYPE_LIMIT && lp.IsPositive() {
		p.price = lp
	} else if in.LastPrice.IsPositive() {
		bps := e.cfg.EntryCollarBps
		if p.close {
			bps = e.cfg.ExitCollarBps
		}
		off := in.LastPrice.Mul(decimal.NewFromInt(bps)).Div(decimal.NewFromInt(10000))
		if in.Buy {
			p.price = roundTick(in.LastPrice.Add(off), true)
		} else {
			p.price = roundTick(in.LastPrice.Sub(off), false)
		}
	}
	in.Price, in.Notional = p.price, p.price.Mul(qty)
	// Account state.
	pol := e.cfg.Policy(acct)
	ra := risk.Account{ID: acct, Known: a.info != nil, PDTGuard: pol.PDTGuard, PDTMax: pol.PDTMax, MarginMultiple: pol.MarginMultiple,
		MarginUtilCap: pol.MarginUtilCap, Segregation: pol.Segregation}
	if a.info != nil {
		ra.Live = a.info.GetEnvironment() == brokerv1.Environment_ENVIRONMENT_LIVE
	}
	if a.session != nil {
		ra.Session, ra.Ready = a.session.GetState().String(), a.session.GetState() == brokerv1.SessionState_SESSION_STATE_READY
	}
	if a.recon != nil {
		ra.ReconStatus, ra.Halted = a.recon.GetStatus().String(), a.recon.GetHalted()
		ra.ReconFresh = a.readyAt > 0 && a.recon.GetAsOf() >= a.readyAt
	}
	if s := a.snap; s != nil {
		ra.SnapshotKnown, ra.IMD, ra.MarginCall = true, s.GetIntradayMarginDeficit(), s.GetMarginCall()
		ra.TradingBlocked = s.GetTradingBlocked() || s.GetAccountBlocked()
		ra.Equity, ra.BuyingPower, ra.LongMV, ra.ShortMV = pd(s.GetEquity()), pd(s.GetBuyingPower()), pd(s.GetLongMarketValue()), pd(s.GetShortMarketValue())
		if ra.LongMV.IsZero() && ra.ShortMV.IsZero() {
			for _, pos := range s.GetPositions() {
				mv := pd(pos.GetMarketValue())
				if mv.IsNegative() {
					ra.ShortMV = ra.ShortMV.Add(mv)
				} else {
					ra.LongMV = ra.LongMV.Add(mv)
				}
			}
		}
		ra.DayTradeCount = int(s.GetDayTradeCount())
	}
	if c := e.dayTrades(acct); c > ra.DayTradeCount {
		ra.DayTradeCount = c
	}
	// Strategies' exposure and open orders on the account (margin guard, D30).
	for id, s := range e.strategies {
		if s.GetBrokerAccountId() != acct {
			continue
		}
		if pv, err := e.ledger.Portfolio(ctx, id); err == nil {
			ra.StrategiesGross = ra.StrategiesGross.Add(pd(pv.GetGrossExposure()))
			ra.StrategyAllocSum = ra.StrategyAllocSum.Add(pd(pv.GetAllocation()))
		}
	}
	for _, o := range e.orders {
		if IsFinal(o.GetState()) || o.GetBrokerAccountId() != acct || isProtective(o) || o.GetSide() != commonv1.Side_SIDE_BUY {
			continue
		}
		n := pd(o.GetLimitPrice()).Mul(pd(o.GetQuantity()).Sub(pd(o.GetFilledQty())))
		ra.AccountOpenOrder = ra.AccountOpenOrder.Add(n)
		if o.GetStrategyId() != Manual {
			ra.StrategiesOpenOrder = ra.StrategiesOpenOrder.Add(n)
		}
	}
	in.Account = ra
	// Kill switches and halts.
	for _, k := range e.kills {
		if k.GetLatched() {
			in.Kills = append(in.Kills, risk.KillSwitch{ID: k.GetEventId(), Scope: scopeName(k.GetScope()), Target: k.GetTarget(), Action: k.GetAction().String()})
		}
	}
	in.Halts = e.activeHalts(acct, p.owner, instr)
	if e.recovering {
		in.Halts = append(in.Halts, "startup recovery: open orders not yet reconciled with the broker (OE-020)")
	}
	// Ledger: ownership and the owner's portfolio.
	if oc, err := e.ledger.Constraints(ctx, acct, instr, p.owner); err == nil {
		in.Owner = risk.Ownership{Known: true, StrategyQty: pd(oc.GetStrategyQty()), OtherQty: pd(oc.GetOtherOwnersQty()), ManualQty: pd(oc.GetManualQty()),
			MaxSellable: pd(oc.GetMaxSellableByStrategy()), ShortAllowed: oc.GetShortAllowed(), Reason: oc.GetReason(), Halted: oc.GetHalted()}
	}
	if !manual && st != nil {
		if pv, err := e.ledger.Portfolio(ctx, st.GetStrategyId()); err == nil {
			pf := risk.Portfolio{Known: true, Allocation: pd(pv.GetAllocation()), Equity: pd(pv.GetEquity()), HighWaterMark: pd(pv.GetHighWaterMark()),
				DayPnL: pd(pv.GetDayPnl()), Gross: pd(pv.GetGrossExposure()), Net: pd(pv.GetNetExposure()), ConsecutiveLosses: e.losses[st.GetStrategyId()]}
			for _, pos := range pv.GetPositions() {
				if !pd(pos.GetQty()).IsZero() {
					pf.Positions++
					if pos.GetInstrumentId() == instr {
						pf.HoldsInstrument, pf.InstrumentValue = true, pd(pos.GetMarketValue())
					}
				}
			}
			in.Portfolio = pf
		}
	}
	// Market session.
	now := e.cfg.Now()
	in.MarketOpen = e.cfg.Calendar.IsOpen(now)
	if _, part, ok := e.cfg.Calendar.SessionAt(now); ok && part != 0 {
		in.ExtendedOpen = true
	}
	// Instrument identity and tradability (fail closed on lookup errors).
	if inst, err := e.instr.Lookup(ctx, instr); err == nil {
		p.inst = inst
		in.InstrumentKnown = true
		info := e.instrCache[acct+"|"+instr]
		if info == nil || e.now()-info.GetAsOf() > int64(10*time.Minute) {
			e.mu.Unlock()
			fresh, ierr := e.brokers.InstrumentInfo(ctx, acct, inst)
			e.mu.Lock()
			if ierr == nil {
				fresh.AsOf = e.now()
				e.instrCache[acct+"|"+instr] = fresh
				info = fresh
			}
		}
		if info != nil {
			in.Tradable, in.Shortable = info.GetTradable(), info.GetShortable()
		}
	}
	// Open orders: own legs, other owners' opposite-side resting orders (OE-018), rates.
	opposite := commonv1.Side_SIDE_SELL
	if !in.Buy {
		opposite = commonv1.Side_SIDE_BUY
	}
	cutoff := e.now() - int64(time.Minute)
	for _, o := range e.orders {
		if IsFinal(o.GetState()) || o.GetBrokerAccountId() != acct {
			continue
		}
		if o.GetStrategyId() == p.owner && !isProtective(o) {
			in.OpenOrders++
		}
		// Bracket legs held until their parent fills are not resting at the exchange.
		if o.GetInstrumentId() != instr || !sameDirection(o.GetSide(), opposite) || o.GetBrokerStatus() == "held" {
			continue
		}
		if o.GetStrategyId() == p.owner {
			if isProtective(o) {
				in.OwnOppositeLegs = true
			}
			continue
		}
		in.OtherOppositeOpen = append(in.OtherOppositeOpen, o.GetStrategyId())
	}
	n := 0
	for _, t := range e.orderTimes[p.owner] {
		if t >= cutoff {
			n++
		}
	}
	in.OrdersLastMinute = n
	key := fmt.Sprintf("%s|%s|%s|%s|%s", acct, instr, it.GetSide(), qty, it.GetKind())
	window := int64(in.Limits.DuplicateWindowSeconds) * int64(time.Second)
	for _, r := range e.recent[it.GetStrategyId()] {
		if r.key == key && e.now()-r.at <= window {
			in.Duplicate = true
		}
	}
	sizes := append([]decimal.Decimal(nil), e.sizes[it.GetStrategyId()]...)
	in.SizeHistory = len(sizes)
	if len(sizes) > 0 {
		sort.Slice(sizes, func(i, j int) bool { return sizes[i].LessThan(sizes[j]) })
		in.MedianOrderQty = sizes[len(sizes)/2]
	}
	in.SupportsBracket = p.bracket
	return in, p
}

func sameDirection(side, want commonv1.Side) bool {
	if want == commonv1.Side_SIDE_SELL {
		return side == commonv1.Side_SIDE_SELL || side == commonv1.Side_SIDE_SELL_SHORT
	}
	return side == want
}

func scopeName(s ordersv1.KillSwitchScope) string {
	switch s {
	case ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL:
		return "global"
	case ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT:
		return "broker_account"
	case ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY:
		return "strategy"
	case ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_INSTRUMENT:
		return "instrument"
	}
	return "unknown"
}

// dayTrades counts same-day round trips per instrument in the last five sessions, all owners
// (manual fills included, RM-008).
func (e *Engine) dayTrades(acct string) int {
	type k struct{ date, instr string }
	buys, sells := map[k]bool{}, map[k]bool{}
	since := e.cfg.Now().Add(-7 * 24 * time.Hour)
	sessions := e.cfg.Calendar.Sessions(since, e.cfg.Now())
	if len(sessions) > 5 {
		sessions = sessions[len(sessions)-5:]
	}
	valid := map[string]bool{}
	for _, s := range sessions {
		valid[s.Date] = true
	}
	// The session lookup is the expensive step: skip fills older than the window first.
	oldest := since.UnixNano()
	for _, f := range e.fills {
		if f.GetEventTime() < oldest || f.GetBrokerAccountId() != acct {
			continue
		}
		s, _, ok := e.cfg.Calendar.SessionAt(time.Unix(0, f.GetEventTime()))
		if !ok || !valid[s.Date] {
			continue
		}
		key := k{s.Date, f.GetInstrumentId()}
		if f.GetSide() == commonv1.Side_SIDE_BUY {
			buys[key] = true
		} else {
			sells[key] = true
		}
	}
	n := 0
	for key := range buys {
		if sells[key] {
			n++
		}
	}
	return n
}

// ---- order execution

func (e *Engine) newOrder(rec *ordersv1.IntentRecord, p plan, attempt int32, role ordersv1.OrderRole, side commonv1.Side, qty, price decimal.Decimal, typ ordersv1.OrderType, tif ordersv1.TimeInForce) *ordersv1.Order {
	it := rec.GetIntent()
	coid := attribution.ID{StrategyShortID: p.short, IntentID: it.GetIntentId(), Attempt: int(attempt)}.String()
	o := &ordersv1.Order{OrderId: newID(), IntentId: it.GetIntentId(), ClientOrderId: coid, BrokerAccountId: it.GetBrokerAccountId(), StrategyId: p.owner,
		InstrumentId: it.GetInstrumentId(), Side: side, Quantity: d(qty), State: ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT, FilledQty: d(decimal.Zero),
		Mode: it.GetMode(), CreatedAt: e.now(), UpdatedAt: e.now(), CorrelationId: it.GetCorrelationId(), Attempt: attempt, Role: role, OrderType: typ, Tif: tif}
	if typ == ordersv1.OrderType_ORDER_TYPE_STOP {
		o.StopPrice = d(price)
	} else {
		o.LimitPrice = d(price)
	}
	return o
}

// execute builds and submits the orders of an approved intent (OE-012..OE-018).
func (e *Engine) execute(ctx context.Context, rec *ordersv1.IntentRecord, p plan) []*ordersv1.Order {
	it := rec.GetIntent()
	qty := p.qty
	if p.close {
		// OE-018: cancel the owner's own resting orders in the instrument (protective legs included)
		// before the exit, then size the exit to what the owner still owns.
		if !e.cancelOwn(ctx, it.GetBrokerAccountId(), p.owner, it.GetInstrumentId()) {
			e.finishIntent(ctx, rec, IntentFailed, "own resting orders did not cancel in time; exit not sent (protective orders remain)")
			return nil
		}
		oc, err := e.ledger.Constraints(ctx, it.GetBrokerAccountId(), it.GetInstrumentId(), p.owner)
		if err != nil {
			e.finishIntent(ctx, rec, IntentFailed, "ledger unavailable after cancel")
			return nil
		}
		// The exit's own reservation is part of the reserved-sell quantity; add it back.
		avail := pd(oc.GetMaxSellableByStrategy()).Add(qty)
		if p.owner == Manual {
			avail = pd(oc.GetManualQty())
		}
		if avail.LessThan(qty) {
			qty = decimal.Max(decimal.Zero, avail)
		}
		if !qty.IsPositive() {
			e.finishIntent(ctx, rec, IntentDone, "nothing left to exit (protective order already closed the position)")
			return nil
		}
	}
	role := ordersv1.OrderRole_ORDER_ROLE_ENTRY
	if p.close {
		role = ordersv1.OrderRole_ORDER_ROLE_EXIT
	}
	o := e.newOrder(rec, p, 1, role, it.GetSide(), qty, p.price, p.orderType, p.tif)
	req := &brokerv1.BrokerOrderRequest{ClientOrderId: o.GetClientOrderId(), BrokerAccountId: o.GetBrokerAccountId(), Instrument: p.inst, Side: o.GetSide(),
		Qty: o.GetQuantity(), Type: o.GetOrderType(), LimitPrice: o.GetLimitPrice(), Tif: o.GetTif(), IntentId: it.GetIntentId(), Mode: it.GetMode(),
		CorrelationId: it.GetCorrelationId(), ExtendedHours: it.GetExtendedHours()}
	prot := it.GetProtective()
	if !p.close && prot != nil && pd(prot.GetStopLossPrice()).IsPositive() {
		if p.bracket {
			// Alpaca-style bracket / OTO: protection is attached at the broker with the entry (N5).
			cls := brokerv1.OrderClass_ORDER_CLASS_OTO
			if pd(prot.GetTakeProfitPrice()).IsPositive() {
				cls = brokerv1.OrderClass_ORDER_CLASS_BRACKET
			}
			req.Bracket = &brokerv1.BracketSpec{OrderClass: cls, StopLossStopPrice: prot.GetStopLossPrice()}
			if cls == brokerv1.OrderClass_ORDER_CLASS_BRACKET {
				req.Bracket.TakeProfitLimitPrice = prot.GetTakeProfitPrice()
			}
			if prot.GetTif() == ordersv1.TimeInForce_TIME_IN_FORCE_GTC {
				req.Tif, o.Tif = ordersv1.TimeInForce_TIME_IN_FORCE_GTC, ordersv1.TimeInForce_TIME_IN_FORCE_GTC
			}
		} else {
			// IBKR adapter: no bracket support; a GTC stop is placed as soon as the entry fills (OE-014).
			e.mu.Lock()
			e.protect[o.GetOrderId()] = prot
			e.mu.Unlock()
		}
	}
	return []*ordersv1.Order{e.submit(ctx, rec, o, req)}
}

// submit persists the order with its attribution ID before sending it (VP-002) and applies the
// broker's answer; an ambiguous answer is UNKNOWN and halts the strategy for the instrument until
// the reconciler resolves it by client_order_id (OE-003, OE-005).
func (e *Engine) submit(ctx context.Context, rec *ordersv1.IntentRecord, o *ordersv1.Order, req *brokerv1.BrokerOrderRequest) *ordersv1.Order {
	e.mu.Lock()
	e.index(o)
	if rec.GetState() == IntentApproved {
		rec.State = IntentSubmitted
	}
	e.orderTimes[o.GetStrategyId()] = append(e.orderTimes[o.GetStrategyId()], e.now())
	if err := e.commit(ctx, &Tx{Orders: []*ordersv1.Order{o}, Intents: []*ordersv1.IntentRecord{rec}, Outbox: []outbox.Message{e.orderMsg(o)}}); err != nil {
		o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_REJECTED, "not sent: OMS store unavailable"
		e.mu.Unlock()
		e.finishIntent(ctx, rec, IntentFailed, o.GetRejectReason())
		return o
	}
	e.mu.Unlock()
	resp, err := e.brokers.Submit(ctx, req)
	e.mu.Lock()
	tx := &Tx{}
	o.UpdatedAt = e.now()
	switch {
	case err != nil && (status.Code(err) == codes.FailedPrecondition || status.Code(err) == codes.InvalidArgument || status.Code(err) == codes.NotFound || status.Code(err) == codes.PermissionDenied):
		o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_REJECTED, "not sent: "+status.Convert(err).Message()
	case err != nil:
		o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_UNKNOWN, "ambiguous submit: "+status.Convert(err).Message()
	case resp.GetOutcome() == brokerv1.SubmitOutcome_SUBMIT_OUTCOME_ACCEPTED:
		e.applyBrokerOrder(o, resp.GetOrder(), tx)
	case resp.GetOutcome() == brokerv1.SubmitOutcome_SUBMIT_OUTCOME_REJECTED:
		o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_REJECTED, resp.GetReject().GetCode().String()+": "+resp.GetReject().GetMessage()
	default:
		o.State, o.RejectReason = ordersv1.OrderState_ORDER_STATE_UNKNOWN, "broker could not confirm the order (SUBMIT_OUTCOME_UNKNOWN)"
	}
	e.index(o)
	switch o.GetState() {
	case ordersv1.OrderState_ORDER_STATE_UNKNOWN:
		e.addHalt(o.GetBrokerAccountId(), o.GetStrategyId(), o.GetInstrumentId(), "order "+o.GetClientOrderId()+" state unknown (OE-005)", "unknown_order:"+o.GetOrderId(), tx)
	case ordersv1.OrderState_ORDER_STATE_REJECTED:
		e.noteReject(ctx, o, tx)
	}
	tx.Orders = append(tx.Orders, o)
	tx.Outbox = append(tx.Outbox, e.orderMsg(o))
	tx.Audit = append(tx.Audit, audit.Event{Actor: "service:execution", Action: "execution.order.submit", Target: o.GetClientOrderId(), CorrelationID: o.GetCorrelationId(),
		Details: map[string]any{"state": o.GetState().String(), "reject": o.GetRejectReason(), "role": o.GetRole().String()}})
	_ = e.commit(ctx, tx)
	covered := e.killCovers(o)
	// Read the state under the lock: a broker update may change it as soon as the lock is released.
	state := o.GetState()
	e.mu.Unlock()
	if state == ordersv1.OrderState_ORDER_STATE_REJECTED {
		e.afterFinal(ctx, o)
	}
	if covered && !IsFinal(state) && !isProtective(o) {
		// A kill switch engaged while this order was in flight: cancel it now (RM-006).
		e.cancelOrder(ctx, o)
	}
	e.maybeProtect(ctx, o)
	return o
}

// applyBrokerOrder merges the broker's view into o and registers bracket legs (OE-009).
func (e *Engine) applyBrokerOrder(o *ordersv1.Order, b *brokerv1.BrokerOrder, tx *Tx) {
	if b == nil {
		return
	}
	if b.GetBrokerOrderId() != "" {
		o.BrokerOrderId = b.GetBrokerOrderId()
	}
	if b.GetPermId() != "" {
		o.PermId = b.GetPermId()
	}
	if !IsFinal(o.GetState()) || b.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
		o.State = b.GetState()
	}
	if f := pd(b.GetFilledQty()); f.GreaterThanOrEqual(pd(o.GetFilledQty())) {
		o.FilledQty = d(f)
		if b.GetAvgFillPrice().GetValue() != "" {
			o.AvgPrice = b.GetAvgFillPrice()
		}
	}
	o.BrokerStatus = b.GetBrokerStatus()
	if o.GetSubmittedAt() == 0 {
		o.SubmittedAt = e.now()
	}
	o.UpdatedAt = e.now()
	for _, leg := range b.GetLegs() {
		e.upsertLeg(o, leg, tx)
	}
}

func (e *Engine) upsertLeg(parent *ordersv1.Order, leg *brokerv1.BrokerOrder, tx *Tx) *ordersv1.Order {
	key := parent.GetBrokerAccountId() + "|" + leg.GetBrokerOrderId()
	if id, ok := e.byBroker[key]; ok {
		x := e.orders[id]
		if !IsFinal(x.GetState()) || leg.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
			x.State = leg.GetState()
		}
		x.FilledQty, x.AvgPrice, x.BrokerStatus, x.UpdatedAt = leg.GetFilledQty(), leg.GetAvgFillPrice(), leg.GetBrokerStatus(), e.now()
		tx.Orders = append(tx.Orders, x)
		tx.Outbox = append(tx.Outbox, e.orderMsg(x))
		return x
	}
	role := ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP
	if leg.GetType() == ordersv1.OrderType_ORDER_TYPE_LIMIT {
		role = ordersv1.OrderRole_ORDER_ROLE_TAKE_PROFIT
	}
	x := &ordersv1.Order{OrderId: newID(), IntentId: parent.GetIntentId(), ClientOrderId: parent.GetClientOrderId(), BrokerOrderId: leg.GetBrokerOrderId(),
		BrokerAccountId: parent.GetBrokerAccountId(), StrategyId: parent.GetStrategyId(), InstrumentId: parent.GetInstrumentId(), Side: leg.GetSide(),
		Quantity: leg.GetQty(), State: leg.GetState(), FilledQty: leg.GetFilledQty(), AvgPrice: leg.GetAvgFillPrice(), Mode: parent.GetMode(),
		CreatedAt: e.now(), SubmittedAt: e.now(), UpdatedAt: e.now(), CorrelationId: parent.GetCorrelationId(), Attempt: parent.GetAttempt(), Role: role,
		ParentOrderId: parent.GetOrderId(), OrderType: leg.GetType(), LimitPrice: leg.GetLimitPrice(), StopPrice: leg.GetStopPrice(), Tif: leg.GetTif(), BrokerStatus: leg.GetBrokerStatus()}
	e.index(x)
	tx.Orders = append(tx.Orders, x)
	tx.Outbox = append(tx.Outbox, e.orderMsg(x))
	return x
}

func (e *Engine) noteReject(ctx context.Context, o *ordersv1.Order, tx *Tx) {
	sid := o.GetStrategyId()
	now := e.now()
	keep := e.rejects[sid][:0]
	for _, t := range e.rejects[sid] {
		if now-t < int64(time.Minute) {
			keep = append(keep, t)
		}
	}
	e.rejects[sid] = append(keep, now)
	if sid != Manual && len(e.rejects[sid]) >= e.cfg.RejectStorm {
		e.rejects[sid] = nil
		go func() {
			_, _ = e.Trigger(context.WithoutCancel(ctx), ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, sid, ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FREEZE,
				fmt.Sprintf("%d broker rejects within a minute (RM-016)", e.cfg.RejectStorm), &ordersv1.Actor{Name: "risk:reject_storm"})
		}()
	}
	tx.Outbox = append(tx.Outbox, e.alert(alertsv1.Severity_SEVERITY_WARNING, "order rejected", fmt.Sprintf("%s %s: %s", sid, o.GetClientOrderId(), o.GetRejectReason()),
		"reject-"+o.GetOrderId(), "docs/runbooks/kill-switch.md", o.GetCorrelationId()))
}

// finishIntent sets a terminal intent state and releases its reservation.
func (e *Engine) finishIntent(ctx context.Context, rec *ordersv1.IntentRecord, state, note string) {
	e.mu.Lock()
	if rec.GetState() == IntentDone || rec.GetState() == IntentFailed {
		e.mu.Unlock()
		return
	}
	rec.State = state
	tx := &Tx{Intents: []*ordersv1.IntentRecord{rec}}
	if note != "" {
		tx.Audit = append(tx.Audit, audit.Event{Actor: "service:execution", Action: "execution.intent." + state, Target: rec.GetIntent().GetIntentId(),
			CorrelationID: rec.GetIntent().GetCorrelationId(), Details: map[string]any{"note": note}})
		if state == IntentFailed {
			tx.Outbox = append(tx.Outbox, e.alert(alertsv1.Severity_SEVERITY_WARNING, "intent failed", rec.GetIntent().GetStrategyId()+": "+note,
				"intent-"+rec.GetIntent().GetIntentId(), "docs/runbooks/kill-switch.md", rec.GetIntent().GetCorrelationId()))
		}
	}
	_ = e.commit(ctx, tx)
	e.mu.Unlock()
	if err := e.ledger.Release(ctx, rec.GetIntent().GetIntentId()); err != nil {
		e.log.Warn("release reservation failed", "intent", rec.GetIntent().GetIntentId(), "err", err)
	}
}

// afterFinal releases the intent once all of its non-protective orders are final.
func (e *Engine) afterFinal(ctx context.Context, o *ordersv1.Order) {
	e.mu.Lock()
	rec := e.intents[o.GetIntentId()]
	if rec == nil {
		e.mu.Unlock()
		return
	}
	done := true
	for _, x := range e.orders {
		if x.GetIntentId() == o.GetIntentId() && !isProtective(x) && !IsFinal(x.GetState()) {
			done = false
		}
	}
	e.mu.Unlock()
	if done {
		state := IntentDone
		if o.GetState() == ordersv1.OrderState_ORDER_STATE_REJECTED && pd(o.GetFilledQty()).IsZero() {
			state = IntentFailed
		}
		e.finishIntent(ctx, rec, state, o.GetRejectReason())
	}
}

// maybeProtect places the GTC protective stop for an entry at a broker without bracket support once
// the entry is final with a filled quantity (OE-014). Child orders use attempts 50–99 (VP-002).
func (e *Engine) maybeProtect(ctx context.Context, entry *ordersv1.Order) {
	e.mu.Lock()
	prot := e.protect[entry.GetOrderId()]
	filled := pd(entry.GetFilledQty())
	if prot == nil || !IsFinal(entry.GetState()) || !filled.IsPositive() || e.hasProtective(entry) {
		e.mu.Unlock()
		return
	}
	delete(e.protect, entry.GetOrderId())
	rec := e.intents[entry.GetIntentId()]
	inst, err := e.instr.Lookup(ctx, entry.GetInstrumentId())
	e.mu.Unlock()
	if rec == nil || err != nil {
		return
	}
	side := commonv1.Side_SIDE_SELL
	if entry.GetSide() != commonv1.Side_SIDE_BUY {
		side = commonv1.Side_SIDE_BUY
	}
	tif := prot.GetTif()
	if tif == ordersv1.TimeInForce_TIME_IN_FORCE_UNSPECIFIED {
		tif = ordersv1.TimeInForce_TIME_IN_FORCE_GTC
	}
	short := attribution.ManualShortID
	if s := e.strategies[entry.GetStrategyId()]; s != nil {
		short = s.GetShortId()
	}
	stop := e.newOrder(rec, plan{owner: entry.GetStrategyId(), short: short}, 50, ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP, side, filled,
		pd(prot.GetStopLossPrice()), ordersv1.OrderType_ORDER_TYPE_STOP, tif)
	stop.ParentOrderId = entry.GetOrderId()
	req := &brokerv1.BrokerOrderRequest{ClientOrderId: stop.GetClientOrderId(), BrokerAccountId: stop.GetBrokerAccountId(), Instrument: inst, Side: side,
		Qty: stop.GetQuantity(), Type: ordersv1.OrderType_ORDER_TYPE_STOP, StopPrice: stop.GetStopPrice(), Tif: tif, IntentId: rec.GetIntent().GetIntentId(),
		Mode: rec.GetIntent().GetMode(), CorrelationId: rec.GetIntent().GetCorrelationId()}
	placed := e.submit(ctx, rec, stop, req)
	if IsFinal(placed.GetState()) && placed.GetState() != ordersv1.OrderState_ORDER_STATE_FILLED {
		e.mu.Lock()
		tx := &Tx{}
		e.addHalt(entry.GetBrokerAccountId(), entry.GetStrategyId(), entry.GetInstrumentId(), "protective stop could not be placed: "+placed.GetRejectReason(), "naked:"+entry.GetOrderId(), tx)
		_ = e.commit(ctx, tx)
		e.mu.Unlock()
	}
}

// cancelOwn cancels the owner's open orders in an instrument and waits until they are final.
func (e *Engine) cancelOwn(ctx context.Context, acct, owner, instr string) bool {
	e.mu.Lock()
	var open []*ordersv1.Order
	for _, o := range e.orders {
		if o.GetBrokerAccountId() == acct && o.GetStrategyId() == owner && o.GetInstrumentId() == instr && !IsFinal(o.GetState()) &&
			o.GetState() != ordersv1.OrderState_ORDER_STATE_PENDING_SUBMIT {
			open = append(open, o)
		}
	}
	e.mu.Unlock()
	if len(open) == 0 {
		return true
	}
	for _, o := range open {
		e.cancelOrder(ctx, o)
	}
	deadline := e.cfg.Wall().Add(e.cfg.CancelWait)
	for e.cfg.Wall().Before(deadline) {
		if e.allFinal(open) {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	// Ask the broker directly before giving up (updates may lag on the bus).
	for _, o := range open {
		if r, err := e.brokers.GetOrder(ctx, o.GetBrokerAccountId(), "", o.GetBrokerOrderId()); err == nil && r.GetFound() {
			e.mu.Lock()
			tx := &Tx{}
			e.applyBrokerOrder(o, r.GetOrder(), tx)
			tx.Orders = append(tx.Orders, o)
			_ = e.commit(ctx, tx)
			e.mu.Unlock()
		}
	}
	return e.allFinal(open)
}

func (e *Engine) allFinal(os []*ordersv1.Order) bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	for _, o := range os {
		if !IsFinal(o.GetState()) {
			return false
		}
	}
	return true
}

// cancelOrder asks the broker to cancel one platform order.
func (e *Engine) cancelOrder(ctx context.Context, o *ordersv1.Order) bool {
	e.mu.Lock()
	acct, bid, coid := o.GetBrokerAccountId(), o.GetBrokerOrderId(), o.GetClientOrderId()
	if bid != "" {
		coid = ""
	}
	e.mu.Unlock()
	resp, err := e.brokers.Cancel(ctx, acct, coid, bid)
	if err != nil {
		e.log.Warn("cancel failed", "order", o.GetOrderId(), "err", err)
		return false
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	tx := &Tx{}
	switch resp.GetOutcome() {
	case brokerv1.CancelOutcome_CANCEL_OUTCOME_REQUESTED, brokerv1.CancelOutcome_CANCEL_OUTCOME_ALREADY_FINAL:
		if resp.GetOrder() != nil {
			e.applyBrokerOrder(o, resp.GetOrder(), tx)
		} else if !IsFinal(o.GetState()) {
			o.State = ordersv1.OrderState_ORDER_STATE_PENDING_CANCEL
		}
	case brokerv1.CancelOutcome_CANCEL_OUTCOME_NOT_FOUND:
		if !IsFinal(o.GetState()) && pd(o.GetFilledQty()).IsZero() {
			o.State = ordersv1.OrderState_ORDER_STATE_CANCELED
		}
	}
	o.UpdatedAt = e.now()
	tx.Orders = append(tx.Orders, o)
	tx.Outbox = append(tx.Outbox, e.orderMsg(o))
	tx.Audit = append(tx.Audit, audit.Event{Actor: "service:execution", Action: "execution.order.cancel", Target: o.GetClientOrderId(), CorrelationID: o.GetCorrelationId(),
		Details: map[string]any{"outcome": resp.GetOutcome().String()}})
	_ = e.commit(ctx, tx)
	return true
}

// CancelIntent cancels every open order of one intent (platform orders only).
func (e *Engine) CancelIntent(ctx context.Context, intentID string) int {
	e.mu.Lock()
	var open []*ordersv1.Order
	for _, o := range e.orders {
		if o.GetIntentId() == intentID && !IsFinal(o.GetState()) {
			open = append(open, o)
		}
	}
	e.mu.Unlock()
	n := 0
	for _, o := range open {
		if e.cancelOrder(ctx, o) {
			n++
		}
	}
	return n
}
