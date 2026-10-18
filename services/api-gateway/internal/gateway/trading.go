package gateway

import (
	"context"
	"crypto/rand"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/policy"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// TradingClients are the internal services behind the portfolio, strategy, order, kill-switch, and
// chart pages (section 14 Phase 2 slice). Any of them may be nil; their routes then answer 503.
type TradingClients struct {
	Exec   ordersv1.ExecutionServiceClient
	Ledger ledgerv1.LedgerServiceClient
	MD     mdv1.MarketDataServiceClient
}

// DialTrading connects to execution, ledger, and marketdata with a service token on every call
// (SE-040). Connections are lazy, so the gateway starts while those services are still down.
func DialTrading(cfg Config, signer *svcauth.Signer) (*TradingClients, func(), error) {
	var conns []*grpc.ClientConn
	closeAll := func() {
		for _, c := range conns {
			_ = c.Close()
		}
	}
	dial := func(addr, audience string) (*grpc.ClientConn, error) {
		c, err := grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()),
			grpc.WithUnaryInterceptor(func(ctx context.Context, method string, req, reply any, cc *grpc.ClientConn, inv grpc.UnaryInvoker, opts ...grpc.CallOption) error {
				return inv(ctx, method, req, reply, cc, append(opts, signer.PerRPC(audience))...)
			}))
		if err == nil {
			conns = append(conns, c)
		}
		return c, err
	}
	ex, err := dial(cfg.ExecutionAddr, "execution")
	if err != nil {
		return nil, closeAll, err
	}
	lg, err := dial(cfg.LedgerAddr, "ledger")
	if err != nil {
		closeAll()
		return nil, func() {}, err
	}
	md, err := dial(cfg.MarketDataAddr, "marketdata")
	if err != nil {
		closeAll()
		return nil, func() {}, err
	}
	return &TradingClients{Exec: ordersv1.NewExecutionServiceClient(ex), Ledger: ledgerv1.NewLedgerServiceClient(lg),
		MD: mdv1.NewMarketDataServiceClient(md)}, closeAll, nil
}

func (s *Server) registerTradingRoutes(priv func(string, string, func(http.ResponseWriter, *http.Request, *reqCtx))) {
	priv("GET", "/api/v1/trading/status", s.handleTradingStatus)
	priv("GET", "/api/v1/accounts", s.handleAccounts)
	priv("GET", "/api/v1/accounts/{account}", s.handleAccountView)
	priv("GET", "/api/v1/accounts/{account}/lots", s.handleLots)
	priv("GET", "/api/v1/accounts/{account}/breaks", s.handleBreaks)
	priv("POST", "/api/v1/accounts/{account}/breaks/{id}/resolve", s.handleResolveBreak)
	priv("GET", "/api/v1/accounts/{account}/reconciliations", s.handleReconReports)
	priv("GET", "/api/v1/strategies", s.handleStrategies)
	priv("GET", "/api/v1/strategies/{id}/portfolio", s.handleStrategyPortfolio)
	priv("GET", "/api/v1/strategies/{id}/equity", s.handleStrategyEquity)
	priv("PUT", "/api/v1/strategies/{id}/mode", s.handleStrategyMode)
	priv("PUT", "/api/v1/strategies/{id}/allocation", s.handleStrategyAllocation)
	priv("GET", "/api/v1/risk-limits/{scope}", s.handleRiskLimitsGet)
	priv("PUT", "/api/v1/risk-limits/{scope}", s.handleRiskLimitsPut)
	priv("GET", "/api/v1/orders", s.handleOrders)
	priv("POST", "/api/v1/orders/preview", s.handleOrderPreview)
	priv("POST", "/api/v1/orders", s.handleOrderSubmit)
	priv("GET", "/api/v1/intents", s.handleIntents)
	priv("DELETE", "/api/v1/intents/{id}", s.handleIntentCancel)
	priv("GET", "/api/v1/fills", s.handleFills)
	priv("GET", "/api/v1/trace/{id}", s.handleTrace)
	priv("GET", "/api/v1/kill-switches", s.handleKillSwitches)
	priv("POST", "/api/v1/kill-switches", s.handleKillEngage)
	priv("POST", "/api/v1/kill-switches/{id}/release", s.handleKillRelease)
	priv("GET", "/api/v1/instruments/resolve", s.handleResolveSymbols)
	priv("GET", "/api/v1/bars", s.handleBars)
	priv("GET", "/api/v1/instruments/{id}/freshness", s.handleFreshness)
	priv("GET", "/api/v1/stream/market", s.handleMarketStream)
}

// ---- helpers ----

// Identifiers that reach gRPC requests or bus subjects: no dots or wildcards (subject injection).
var (
	idRe     = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	scopeRe  = regexp.MustCompile(`^(global|manual|account:[a-z0-9_-]{1,64}|strategy:[A-Za-z0-9_-]{1,64})$`)
	symbolRe = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9.\-]{0,11}$`)
	mobileRe = regexp.MustCompile(`Mobi|Android|iPhone|iPad`)
)

// pj is the canonical proto3 JSON mapping (lowerCamelCase names) with every field emitted, which is
// what the generated TypeScript `*Json` types describe (ADR-0028).
var pj = protojson.MarshalOptions{EmitUnpopulated: true}

// writeProto renders a contract message in the canonical protojson form (contracts/openapi
// gateway.yaml: trading resources are tb.* messages).
func writeProto(w http.ResponseWriter, code int, m proto.Message) {
	b, err := pj.Marshal(m)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "internal", "encode error")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(b)
}

func decodeProto(r *http.Request, m proto.Message) error {
	b, err := io.ReadAll(r.Body)
	if err != nil {
		return errors.New("invalid body")
	}
	if err := protojson.Unmarshal(b, m); err != nil {
		return errors.New("invalid JSON body")
	}
	return nil
}

func qInt(r *http.Request, key string, def, maxV int) int32 {
	n, err := strconv.Atoi(r.URL.Query().Get(key))
	if err != nil || n <= 0 {
		return int32(def)
	}
	return int32(min(n, maxV))
}

func qBool(r *http.Request, key string) bool { return r.URL.Query().Get(key) == "true" }

// qNs reads an optional UTC-nanosecond timestamp; ok is false when it is present but malformed.
func qNs(r *http.Request, key string) (int64, bool) {
	v := r.URL.Query().Get(key)
	if v == "" {
		return 0, true
	}
	n, err := strconv.ParseInt(v, 10, 64)
	return n, err == nil && n >= 0
}

// qID returns an optional identifier query parameter; ok is false when it is present but malformed.
func qID(r *http.Request, key string) (string, bool) {
	v := r.URL.Query().Get(key)
	return v, v == "" || idRe.MatchString(v)
}

func callCtx(r *http.Request) (context.Context, context.CancelFunc) {
	return context.WithTimeout(r.Context(), 5*time.Second)
}

// isMobile reports a phone or tablet client (UA client hint or user agent). Owner-controlled and
// spoofable by the owner only; it gates UX-level restrictions (RM-007 desktop release, U6).
func isMobile(r *http.Request) bool {
	if h := r.Header.Get("Sec-CH-UA-Mobile"); h != "" {
		return h == "?1"
	}
	return mobileRe.MatchString(r.UserAgent())
}

// execActor is the authenticated owner as forwarded to execution. step_up_method is set only while
// the session's step-up is fresh, so execution's own checks (RM-007) see the same state as policy.
func (s *Server) execActor(r *http.Request, rc *reqCtx) *ordersv1.Actor {
	a := &ordersv1.Actor{Name: "owner", Mobile: isMobile(r), Ip: rc.ip, Device: rc.ua}
	if rc.session.StepUpUntil != nil && s.now().Before(*rc.session.StepUpUntil) {
		a.StepUpMethod = rc.session.StepUpMethod
	}
	return a
}

func (s *Server) needExec(w http.ResponseWriter) bool {
	if s.trading == nil || s.trading.Exec == nil {
		writeError(w, http.StatusServiceUnavailable, "execution_unavailable", "execution unavailable")
		return false
	}
	return true
}

func (s *Server) needLedger(w http.ResponseWriter) bool {
	if s.trading == nil || s.trading.Ledger == nil {
		writeError(w, http.StatusServiceUnavailable, "ledger_unavailable", "ledger unavailable")
		return false
	}
	return true
}

func (s *Server) needMD(w http.ResponseWriter) bool {
	if s.trading == nil || s.trading.MD == nil {
		writeError(w, http.StatusServiceUnavailable, "marketdata_unavailable", "marketdata unavailable")
		return false
	}
	return true
}

// upstreamError maps internal gRPC errors to safe HTTP errors (SE-012: messages are redacted).
func (s *Server) upstreamError(w http.ResponseWriter, r *http.Request, svc string, err error) {
	st := status.Convert(err)
	switch st.Code() {
	case codes.InvalidArgument, codes.FailedPrecondition:
		writeError(w, http.StatusBadRequest, "rejected", st.Message())
	case codes.NotFound:
		writeError(w, http.StatusNotFound, "not_found", st.Message())
	case codes.PermissionDenied:
		writeError(w, http.StatusForbidden, "forbidden", st.Message())
	case codes.Unavailable, codes.DeadlineExceeded, codes.Canceled:
		writeError(w, http.StatusServiceUnavailable, svc+"_unavailable", svc+" unavailable")
	default:
		s.internalError(w, r, svc, err)
	}
}

func (s *Server) readGuard(w http.ResponseWriter, rc *reqCtx) bool {
	return s.authorize(w, rc, policy.ActTradingRead, policy.Resource{})
}

// ---- status and accounts ----

func (s *Server) handleTradingStatus(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.GetTradingStatus(ctx, &ordersv1.GetTradingStatusRequest{})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleAccounts(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.ListAccounts(ctx, &ledgerv1.ListAccountsRequest{})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) accountParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	a := r.PathValue("account")
	if !accountIDRe.MatchString(a) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid account")
		return "", false
	}
	return a, true
}

func (s *Server) handleAccountView(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.GetAccountView(ctx, &ledgerv1.GetAccountViewRequest{BrokerAccountId: acct})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleLots(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	owner, ok2 := qID(r, "owner")
	instr, ok3 := qID(r, "instrument_id")
	if !ok {
		return
	}
	if !ok2 || !ok3 {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid owner or instrument_id")
		return
	}
	if !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.ListLots(ctx, &ledgerv1.ListLotsRequest{BrokerAccountId: acct, Owner: owner, InstrumentId: instr})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleBreaks(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.ListBreaks(ctx, &ledgerv1.ListBreaksRequest{BrokerAccountId: acct, OpenOnly: qBool(r, "open_only")})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleReconReports(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok := s.accountParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.ListReconciliationReports(ctx, &ledgerv1.ListReconciliationReportsRequest{BrokerAccountId: acct, Limit: qInt(r, "limit", 20, 200)})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type resolveBreakReq struct {
	Resolution string `json:"resolution"`
	FromOwner  string `json:"from_owner"`
	Qty        string `json:"qty"`
	Note       string `json:"note"`
}

// handleResolveBreak applies an owner break resolution (VP-019); it needs a passkey step-up (RM-007).
func (s *Server) handleResolveBreak(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if _, ok := s.accountParam(w, r); !ok {
		return
	}
	id := r.PathValue("id")
	var req resolveBreakReq
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid break id")
		return
	}
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	switch req.Resolution {
	case "accept", "transfer_to_manual", "book_to_manual", "rebaseline_cash":
	default:
		writeError(w, http.StatusBadRequest, "bad_request", "resolution must be accept|transfer_to_manual|book_to_manual|rebaseline_cash")
		return
	}
	if req.FromOwner != "" && !idRe.MatchString(req.FromOwner) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid from_owner")
		return
	}
	if !s.authorize(w, rc, policy.ActLedgerBreakResolve, policy.Resource{ID: id}) || !s.needLedger(w) {
		return
	}
	in := &ledgerv1.ResolveBreakRequest{BreakId: id, Resolution: req.Resolution, FromOwner: req.FromOwner, Actor: "owner",
		Note: truncate(req.Note, 500), CorrelationId: logging.CorrelationID(r.Context())}
	if req.Qty != "" {
		in.Qty = &commonv1.Decimal{Value: req.Qty}
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.ResolveBreak(ctx, in)
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// ---- strategies ----

func (s *Server) strategyParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid strategy id")
		return "", false
	}
	return id, true
}

func (s *Server) handleStrategies(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.ListStrategies(ctx, &ordersv1.ListStrategiesRequest{})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleStrategyPortfolio(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.GetStrategyPortfolio(ctx, &ledgerv1.GetStrategyPortfolioRequest{StrategyId: id})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleStrategyEquity(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	acct := r.URL.Query().Get("account")
	if !ok {
		return
	}
	if !accountIDRe.MatchString(acct) {
		writeError(w, http.StatusBadRequest, "bad_request", "account is required")
		return
	}
	if !s.readGuard(w, rc) || !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Ledger.GetEquityCurve(ctx, &ledgerv1.GetEquityCurveRequest{Owner: id, BrokerAccountId: acct})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type strategyModeReq struct {
	Mode   string `json:"mode"`
	Reason string `json:"reason"`
}

var uiModes = map[string]ordersv1.StrategyMode{
	"disabled": ordersv1.StrategyMode_STRATEGY_MODE_DISABLED, "shadow": ordersv1.StrategyMode_STRATEGY_MODE_SHADOW,
	"paper": ordersv1.StrategyMode_STRATEGY_MODE_PAPER,
}

// handleStrategyMode sets disabled|shadow|paper (UI-014). Live is only reachable through the
// section 15 go-live flow (SM-004), so it is rejected here before reaching execution.
func (s *Server) handleStrategyMode(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok {
		return
	}
	var req strategyModeReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	mode, ok := uiModes[req.Mode]
	if !ok {
		writeError(w, http.StatusBadRequest, "bad_request", "mode must be disabled|shadow|paper; live needs the go-live flow")
		return
	}
	if !s.authorize(w, rc, policy.ActStrategyModeSet, policy.Resource{ID: id, Mode: req.Mode}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.SetStrategyMode(ctx, &ordersv1.SetStrategyModeRequest{StrategyId: id, Mode: mode,
		Reason: truncate(req.Reason, 300), Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type allocationReq struct {
	Amount string `json:"amount"`
}

var decimalRe = regexp.MustCompile(`^\d{1,15}(\.\d{1,8})?$`)

// handleStrategyAllocation changes a strategy's allocation. Increases need a passkey step-up
// (RM-007, VP-009); the comparison uses the ledger's current value, as decimal strings.
func (s *Server) handleStrategyAllocation(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, ok := s.strategyParam(w, r)
	if !ok {
		return
	}
	var req allocationReq
	if err := decode(r, &req); err != nil || !decimalRe.MatchString(req.Amount) {
		writeError(w, http.StatusBadRequest, "bad_request", "amount must be a non-negative decimal string")
		return
	}
	if !s.needLedger(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	cur, err := s.trading.Ledger.GetStrategyPortfolio(ctx, &ledgerv1.GetStrategyPortfolioRequest{StrategyId: id})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	action := policy.ActAllocationDecrease
	if decimalGreater(req.Amount, cur.GetPortfolio().GetAllocation().GetValue()) {
		action = policy.ActAllocationIncrease
	}
	if !s.authorize(w, rc, action, policy.Resource{ID: id}) {
		return
	}
	resp, err := s.trading.Ledger.SetAllocation(ctx, &ledgerv1.SetAllocationRequest{Owner: id, BrokerAccountId: cur.GetBrokerAccountId(),
		Amount: &commonv1.Decimal{Value: req.Amount}, Actor: "owner", CorrelationId: logging.CorrelationID(r.Context())})
	if err != nil {
		s.upstreamError(w, r, "ledger", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// decimalGreater compares non-negative decimal strings without floats. An unparsable current value
// counts as zero, so any positive amount is treated as an increase (fail closed to step-up).
func decimalGreater(a, b string) bool {
	norm := func(s string) (string, string) {
		s = strings.TrimSpace(s)
		if s == "" || strings.HasPrefix(s, "-") || !decimalRe.MatchString(s) {
			s = "0"
		}
		i, f, _ := strings.Cut(s, ".")
		i = strings.TrimLeft(i, "0")
		return i, strings.TrimRight(f, "0")
	}
	ai, af := norm(a)
	bi, bf := norm(b)
	if len(ai) != len(bi) {
		return len(ai) > len(bi)
	}
	if ai != bi {
		return ai > bi
	}
	for len(af) < len(bf) {
		af += "0"
	}
	for len(bf) < len(af) {
		bf += "0"
	}
	return af > bf
}

// ---- risk limits ----

func (s *Server) scopeParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	sc := r.PathValue("scope")
	if !scopeRe.MatchString(sc) {
		writeError(w, http.StatusBadRequest, "bad_request", "scope must be global|manual|account:<id>|strategy:<id>")
		return "", false
	}
	return sc, true
}

func (s *Server) handleRiskLimitsGet(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	sc, ok := s.scopeParam(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.GetRiskLimits(ctx, &ordersv1.GetRiskLimitsRequest{Scope: sc})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// handleRiskLimitsPut saves limits for a scope. Execution decides whether the change loosens any
// limit (RM-007); when it does and the session has no fresh passkey step-up, the policy answer for
// risk.limits.raise (require_step_up) is returned and the UI retries after the step-up.
func (s *Server) handleRiskLimitsPut(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	sc, ok := s.scopeParam(w, r)
	if !ok {
		return
	}
	var lim ordersv1.RiskLimits
	if err := decodeProto(r, &lim); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.authorize(w, rc, policy.ActRiskLimitsLower, policy.Resource{ID: sc}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.SetRiskLimits(ctx, &ordersv1.SetRiskLimitsRequest{Scope: sc, Limits: &lim, Actor: s.execActor(r, rc)})
	if status.Code(err) == codes.PermissionDenied {
		if s.authorize(w, rc, policy.ActRiskLimitsRaise, policy.Resource{ID: sc}) {
			writeError(w, http.StatusForbidden, "forbidden", "execution refused the limit change")
		}
		return
	}
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// ---- orders, intents, fills, trace ----

func (s *Server) handleOrders(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok1 := qID(r, "account")
	sid, ok2 := qID(r, "strategy_id")
	if !ok1 || !ok2 {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid account or strategy_id")
		return
	}
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.ListOrders(ctx, &ordersv1.ListOrdersRequest{BrokerAccountId: acct, StrategyId: sid, OpenOnly: qBool(r, "open_only"),
		Limit: qInt(r, "limit", 200, 2000)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleIntents(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	sid, ok := qID(r, "strategy_id")
	since, ok2 := qNs(r, "since")
	if !ok || !ok2 {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid strategy_id or since")
		return
	}
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.ListIntents(ctx, &ordersv1.ListIntentsRequest{StrategyId: sid, RejectedOnly: qBool(r, "rejected_only"), Since: since, Limit: qInt(r, "limit", 200, 2000)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleFills(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	acct, ok1 := qID(r, "account")
	sid, ok2 := qID(r, "strategy_id")
	instr, ok3 := qID(r, "instrument_id")
	since, ok4 := qNs(r, "since")
	if !ok1 || !ok2 || !ok3 || !ok4 {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid filter")
		return
	}
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.ListFills(ctx, &ordersv1.ListFillsRequest{BrokerAccountId: acct, StrategyId: sid, Since: since, Limit: qInt(r, "limit", 200, 2000)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	if instr != "" {
		kept := resp.GetFills()[:0]
		for _, f := range resp.GetFills() {
			if f.GetInstrumentId() == instr {
				kept = append(kept, f)
			}
		}
		resp.Fills = kept
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleTrace(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid correlation id")
		return
	}
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.GetTrace(ctx, &ordersv1.GetTraceRequest{CorrelationId: id})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) handleIntentCancel(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid intent id")
		return
	}
	if !s.authorize(w, rc, policy.ActOrdersCancel, policy.Resource{ID: id}) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.CancelIntent(ctx, &ordersv1.CancelIntentRequest{IntentId: id, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// manualOrderReq is the UI order ticket (UI-015). Quantities and prices are decimal strings.
type manualOrderReq struct {
	BrokerAccountID string `json:"broker_account_id"`
	InstrumentID    string `json:"instrument_id"`
	Side            string `json:"side"`
	Quantity        string `json:"quantity"`
	OrderType       string `json:"order_type"`
	LimitPrice      string `json:"limit_price"`
	TIF             string `json:"tif"`
	ExtendedHours   bool   `json:"extended_hours"`
	Reason          string `json:"reason"`
	// IntentID makes a retried submit idempotent; the gateway generates one when empty.
	IntentID string `json:"intent_id"`
}

var (
	uiSides = map[string]commonv1.Side{"buy": commonv1.Side_SIDE_BUY, "sell": commonv1.Side_SIDE_SELL, "sell_short": commonv1.Side_SIDE_SELL_SHORT}
	uiTypes = map[string]ordersv1.OrderType{"market": ordersv1.OrderType_ORDER_TYPE_MARKET, "limit": ordersv1.OrderType_ORDER_TYPE_LIMIT}
	uiTIFs  = map[string]ordersv1.TimeInForce{"day": ordersv1.TimeInForce_TIME_IN_FORCE_DAY, "gtc": ordersv1.TimeInForce_TIME_IN_FORCE_GTC}
)

// manualIntent validates the ticket and builds an owner ("manual") intent. Ownership, sizing, and
// every risk check stay in execution (VP-001, RM-001); the gateway only shapes the request.
func (s *Server) manualIntent(r *http.Request, req manualOrderReq) (*ordersv1.OrderIntent, error) {
	side, ok1 := uiSides[req.Side]
	typ, ok2 := uiTypes[req.OrderType]
	tif, ok3 := uiTIFs[req.TIF]
	switch {
	case !accountIDRe.MatchString(req.BrokerAccountID) || !idRe.MatchString(req.InstrumentID):
		return nil, errors.New("broker_account_id and instrument_id are required")
	case !ok1 || !ok2 || !ok3:
		return nil, errors.New("side must be buy|sell|sell_short, order_type market|limit, tif day|gtc")
	case !decimalRe.MatchString(req.Quantity) || !decimalGreater(req.Quantity, "0"):
		return nil, errors.New("quantity must be a positive decimal string")
	case typ == ordersv1.OrderType_ORDER_TYPE_LIMIT && (!decimalRe.MatchString(req.LimitPrice) || !decimalGreater(req.LimitPrice, "0")):
		return nil, errors.New("limit orders need a positive limit_price")
	case req.IntentID != "" && !idRe.MatchString(req.IntentID):
		return nil, errors.New("invalid intent_id")
	}
	id := req.IntentID
	if id == "" {
		id = ulid.MustNew(ulid.Timestamp(s.now()), rand.Reader).String()
	}
	now := s.now().UnixNano()
	kind := ordersv1.IntentKind_INTENT_KIND_OPEN
	if side == commonv1.Side_SIDE_SELL {
		kind = ordersv1.IntentKind_INTENT_KIND_CLOSE
	}
	it := &ordersv1.OrderIntent{IntentId: id, StrategyId: "manual", BrokerAccountId: req.BrokerAccountID, InstrumentId: req.InstrumentID,
		Side: side, Sizing: &ordersv1.OrderIntent_Quantity{Quantity: &commonv1.Decimal{Value: req.Quantity}}, OrderType: typ, Tif: tif,
		Reason: truncate(req.Reason, 300), CorrelationId: logging.CorrelationID(r.Context()), CreatedAt: now, DecisionTime: now,
		Kind: kind, ExtendedHours: req.ExtendedHours, Source: ordersv1.IntentSource_INTENT_SOURCE_UI}
	if typ == ordersv1.OrderType_ORDER_TYPE_LIMIT {
		it.LimitPrice = &commonv1.Decimal{Value: req.LimitPrice}
	}
	return it, nil
}

// accountMode returns the account's environment as execution reports it. Unknown counts as live,
// so a manual order for an unknown account still needs the stronger live step-up (fail closed).
func (s *Server) accountMode(ctx context.Context, acct string) string {
	st, err := s.trading.Exec.GetTradingStatus(ctx, &ordersv1.GetTradingStatusRequest{})
	if err != nil {
		return "live"
	}
	for _, a := range st.GetStatus().GetAccounts() {
		if a.GetBrokerAccountId() == acct && a.GetEnvironment() == "paper" {
			return "paper"
		}
	}
	return "live"
}

func modeEnum(m string) commonv1.Mode {
	if m == "paper" {
		return commonv1.Mode_MODE_PAPER
	}
	return commonv1.Mode_MODE_LIVE
}

func (s *Server) handleOrderPreview(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req manualOrderReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	it, err := s.manualIntent(r, req)
	if err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	it.Mode = modeEnum(s.accountMode(ctx, it.GetBrokerAccountId()))
	resp, err := s.trading.Exec.PreviewIntent(ctx, &ordersv1.PreviewIntentRequest{Intent: it})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// handleOrderSubmit submits a manual order through the same pre-trade gate as strategies (RM-001).
// Live accounts need a passkey step-up (SE-003); phones need mobile.order_entry_enabled (U6, UI-015).
func (s *Server) handleOrderSubmit(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req manualOrderReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	it, err := s.manualIntent(r, req)
	if err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if !s.needExec(w) {
		return
	}
	if isMobile(r) && !s.mobileOrdersEnabled(r, rc) {
		writeError(w, http.StatusForbidden, "mobile_order_entry_disabled", "order entry from a phone is disabled (settings: mobile.order_entry_enabled)")
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	mode := s.accountMode(ctx, it.GetBrokerAccountId())
	if !s.authorize(w, rc, policy.ActOrdersSubmitManual, policy.Resource{ID: it.GetBrokerAccountId(), Mode: mode}) {
		return
	}
	it.Mode = modeEnum(mode)
	resp, err := s.trading.Exec.SubmitIntent(ctx, &ordersv1.SubmitIntentRequest{Intent: it, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

func (s *Server) mobileOrdersEnabled(r *http.Request, rc *reqCtx) bool {
	if s.pool == nil {
		return false
	}
	stored, err := s.store.settings(r.Context(), rc.session.OwnerID)
	if err != nil {
		return false
	}
	row, ok := stored["mobile.order_entry_enabled"]
	return ok && strings.TrimSpace(string(row.Value)) == "true"
}

// ---- kill switches ----

func (s *Server) handleKillSwitches(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.readGuard(w, rc) || !s.needExec(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.ListKillSwitches(ctx, &ordersv1.ListKillSwitchesRequest{ActiveOnly: !qBool(r, "all")})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type killEngageReq struct {
	Scope  string `json:"scope"`
	Target string `json:"target"`
	Action string `json:"action"`
	Reason string `json:"reason"`
}

var (
	uiKillScopes = map[string]ordersv1.KillSwitchScope{"global": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL,
		"account": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT, "strategy": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY,
		"instrument": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_INSTRUMENT}
	uiKillActions = map[string]ordersv1.KillSwitchAction{"freeze": ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FREEZE,
		"cancel_open": ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN,
		"flatten":     ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED}
)

// handleKillEngage latches a kill switch (RM-006). From a desktop, engaging needs no step-up so it
// is always one confirmation away (UI-006); from a phone it needs a fresh passkey assertion (RM-020,
// UI-050), after which the UI retries automatically.
func (s *Server) handleKillEngage(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req killEngageReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	scope, ok1 := uiKillScopes[req.Scope]
	action, ok2 := uiKillActions[req.Action]
	if req.Action == "" {
		action, ok2 = ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN, true
	}
	if !ok1 || !ok2 || (scope != ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL && !idRe.MatchString(req.Target)) {
		writeError(w, http.StatusBadRequest, "bad_request", "scope global|account|strategy|instrument (target required unless global); action freeze|cancel_open|flatten")
		return
	}
	res := policy.Resource{ID: req.Scope + ":" + req.Target}
	if isMobile(r) {
		res.Mode = "mobile"
	}
	if !s.authorize(w, rc, policy.ActKillSwitchEngage, res) || !s.needExec(w) {
		return
	}
	reason := truncate(strings.TrimSpace(req.Reason), 300)
	if reason == "" {
		reason = "owner engaged from the UI"
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.TriggerKillSwitch(ctx, &ordersv1.TriggerKillSwitchRequest{Scope: scope, Target: req.Target, Action: action,
		Reason: reason, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

type killReleaseReq struct {
	Reason string `json:"reason"`
}

// handleKillRelease releases a latched kill switch: fresh passkey step-up from a desktop client
// only (RM-007). Execution re-checks both from the forwarded actor.
func (s *Server) handleKillRelease(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id := r.PathValue("id")
	var req killReleaseReq
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid kill switch id")
		return
	}
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if isMobile(r) {
		writeError(w, http.StatusForbidden, "desktop_required", "kill switches can only be released from a desktop client")
		return
	}
	if !s.authorize(w, rc, policy.ActKillSwitchDisable, policy.Resource{ID: id}) || !s.needExec(w) {
		return
	}
	reason := truncate(strings.TrimSpace(req.Reason), 300)
	if reason == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "a release reason is required")
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.Exec.ReleaseKillSwitch(ctx, &ordersv1.ReleaseKillSwitchRequest{EventId: id, Reason: reason, Actor: s.execActor(r, rc)})
	if err != nil {
		s.upstreamError(w, r, "execution", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// ---- instruments and bars ----

func (s *Server) handleResolveSymbols(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var syms []string
	for _, v := range strings.Split(r.URL.Query().Get("symbols"), ",") {
		if v = strings.TrimSpace(v); v != "" {
			if !symbolRe.MatchString(v) {
				writeError(w, http.StatusBadRequest, "bad_request", "invalid symbol")
				return
			}
			syms = append(syms, v)
		}
	}
	if len(syms) == 0 || len(syms) > 50 {
		writeError(w, http.StatusBadRequest, "bad_request", "1-50 comma-separated symbols are required")
		return
	}
	if !s.readGuard(w, rc) || !s.needMD(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.MD.ResolveSymbols(ctx, &mdv1.ResolveSymbolsRequest{Symbols: syms})
	if err != nil {
		s.upstreamError(w, r, "marketdata", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

var uiTimeframes = map[string]mdv1.Timeframe{"1m": mdv1.Timeframe_TIMEFRAME_1M, "5m": mdv1.Timeframe_TIMEFRAME_5M,
	"15m": mdv1.Timeframe_TIMEFRAME_15M, "30m": mdv1.Timeframe_TIMEFRAME_30M, "1h": mdv1.Timeframe_TIMEFRAME_1H, "1d": mdv1.Timeframe_TIMEFRAME_1D}

func (s *Server) chartParams(w http.ResponseWriter, r *http.Request) (string, string, bool) {
	id, tf := r.URL.Query().Get("instrument_id"), r.URL.Query().Get("timeframe")
	if _, ok := uiTimeframes[tf]; !ok || !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "instrument_id and timeframe (1m|5m|15m|30m|1h|1d) are required")
		return "", "", false
	}
	return id, tf, true
}

func (s *Server) handleBars(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id, tf, ok := s.chartParams(w, r)
	if !ok || !s.readGuard(w, rc) || !s.needMD(w) {
		return
	}
	ctx, cancel := callCtx(r)
	defer cancel()
	resp, err := s.trading.MD.GetBars(ctx, &mdv1.GetBarsRequest{InstrumentId: id, Timeframe: uiTimeframes[tf], Limit: qInt(r, "limit", 500, 500)})
	if err != nil {
		s.upstreamError(w, r, "marketdata", err)
		return
	}
	writeProto(w, http.StatusOK, resp)
}

// handleFreshness reports, per timeframe, whether marketdata considers an instrument's newest final
// bar fresh (RM-004's two-interval rule) and when that bar closed: the data explorer's freshness
// table (UI-051). Each timeframe is asked concurrently; a failed one reports its error, never "fresh".
func (s *Server) handleFreshness(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	id := r.PathValue("id")
	if !idRe.MatchString(id) {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid instrument id")
		return
	}
	if !s.readGuard(w, rc) || !s.needMD(w) {
		return
	}
	type row struct {
		Timeframe    string `json:"timeframe"`
		Fresh        bool   `json:"fresh"`
		LastBarClose string `json:"last_bar_close"`
		Reason       string `json:"reason"`
		Error        string `json:"error,omitempty"`
	}
	order := []string{"1m", "5m", "15m", "30m", "1h", "1d"}
	rows := make([]row, len(order))
	var wg sync.WaitGroup
	for i, tf := range order {
		wg.Go(func() {
			ctx, cancel := callCtx(r)
			defer cancel()
			resp, err := s.trading.MD.IsFresh(ctx, &mdv1.IsFreshRequest{InstrumentId: id, Timeframe: uiTimeframes[tf]})
			if err != nil {
				rows[i] = row{Timeframe: tf, Error: "marketdata_unavailable"}
				return
			}
			rows[i] = row{Timeframe: tf, Fresh: resp.GetFresh(), LastBarClose: strconv.FormatInt(resp.GetLastBarClose(), 10), Reason: logging.Redact(resp.GetReason())}
		})
	}
	wg.Wait()
	writeJSON(w, http.StatusOK, map[string]any{"instrument_id": id, "timeframes": rows})
}
