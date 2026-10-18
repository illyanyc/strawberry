package paper

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	alertsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/alerts/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
	execapp "github.com/illyanyc/strawberry/services/execution/app"
)

// monitorGate runs the Python monitor driver (monitor_gate.py) against the harness's execution
// service, served here behind the production service-token interceptor (SE-040) so the monitor's
// calls are authenticated and allowlisted exactly as in the stack.
type monitorGate struct {
	h       *Harness
	uv      string
	root    string
	secrets string
	pub     ed25519.PublicKey
	seed    []byte
	addr    string
	stop    func()

	mu     sync.Mutex
	alerts []*alertsv1.Alert
	events []string
}

func newMonitorGate(t *testing.T, h *Harness, uv string) *monitorGate {
	t.Helper()
	seed, pub, err := svcauth.GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	g := &monitorGate{h: h, uv: uv, secrets: t.TempDir(), seed: seed, pub: pub}
	g.root, _ = filepath.Abs("../..")
	// Same mounted-secret layout as /run/secrets (base64 seed, tbcore.svcauth.Signer.from_secrets).
	if err := os.WriteFile(filepath.Join(g.secrets, svcauth.KeySecretName("monitor")), []byte(base64.StdEncoding.EncodeToString(seed)), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := h.NC.Subscribe("alert.>", g.onAlert); err != nil {
		t.Fatal(err)
	}
	if _, err := h.NC.Subscribe("monitor.>", func(m *nats.Msg) { g.mu.Lock(); g.events = append(g.events, m.Subject); g.mu.Unlock() }); err != nil {
		t.Fatal(err)
	}
	g.serveSigned()
	t.Cleanup(func() { g.stop() })
	return g
}

func (g *monitorGate) onAlert(m *nats.Msg) {
	a := &alertsv1.Alert{}
	if proto.Unmarshal(m.Data, a) == nil {
		g.mu.Lock()
		g.alerts = append(g.alerts, a)
		g.mu.Unlock()
	}
}

// serveSigned (re)serves the current execution app with the monitor's key trusted; call it again
// after RestartExecution.
func (g *monitorGate) serveSigned() {
	t := g.h.T
	if g.stop != nil {
		g.stop()
	}
	lis, err := (&net.ListenConfig{}).Listen(context.Background(), "tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	v := svcauth.NewVerifier("execution", map[string]ed25519.PublicKey{"monitor": g.pub})
	gs := grpc.NewServer(grpc.UnaryInterceptor(v.UnaryServerInterceptor(execapp.AllowedCallers)))
	g.h.Exec.Register(gs)
	go func() { _ = gs.Serve(lis) }()
	g.addr, g.stop = lis.Addr().String(), gs.Stop
}

func (g *monitorGate) client(t *testing.T) ordersv1.ExecutionServiceClient {
	c, err := grpc.NewClient(g.addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return ordersv1.NewExecutionServiceClient(c)
}

// run executes one driver phase and returns its JSON result.
func (g *monitorGate) run(t *testing.T, phase, strategy, short, mode string) map[string]any {
	t.Helper()
	args := []string{"run", "--frozen", "--package", "monitor", "python", "tests/paper/monitor_gate.py", "--phase", phase,
		"--strategy", strategy, "--short-id", short, "--mode", mode, "--exec-addr", g.addr, "--secrets-dir", g.secrets,
		"--nats-url", g.h.URL, "--workdir", t.TempDir()}
	ctx, cancel := context.WithTimeout(g.h.Ctx, 5*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, g.uv, args...) //nolint:gosec // fixed arguments from the test
	cmd.Dir = g.root
	cmd.Env = append(os.Environ(), "TB_LOG_LEVEL=warning")
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("monitor driver %s: %v\n%s", phase, err, stderr.String())
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	res := map[string]any{}
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &res); err != nil {
		t.Fatalf("monitor driver %s output: %v\n%s", phase, err, out)
	}
	return res
}

func (g *monitorGate) waitAlert(t *testing.T, sev alertsv1.Severity, subject string) *alertsv1.Alert {
	t.Helper()
	var found *alertsv1.Alert
	g.h.WaitFor("alert "+subject, 10*time.Second, func() bool {
		g.mu.Lock()
		defer g.mu.Unlock()
		for _, a := range g.alerts {
			if a.GetSeverity() == sev && strings.Contains(a.GetSubject(), subject) {
				found = a
				return true
			}
		}
		return false
	})
	return found
}

func (h *Harness) demoteAudit(id string) map[string]any {
	for _, a := range h.ExecSt.AuditSnapshot() {
		if a.Action == "execution.strategy.demote" && a.Target == id {
			out := map[string]any{"actor": a.Actor}
			for k, v := range a.Details {
				out[k] = v
			}
			return out
		}
	}
	return nil
}

func (h *Harness) activeKill(target string) *ordersv1.KillSwitch {
	for _, k := range h.Exec.Engine.KillSwitches() {
		if k.GetTarget() == target && k.GetReleasedAt() == 0 {
			return k
		}
	}
	return nil
}

// makeLive persists strategies in live mode (as the section 15 flow would) and restarts execution.
func (g *monitorGate) makeLive(t *testing.T, ids ...string) {
	t.Helper()
	h := g.h
	var live []*ordersv1.Strategy
	for _, s := range h.Exec.Engine.Strategies() {
		if slices.Contains(ids, s.GetStrategyId()) {
			c := proto.Clone(s).(*ordersv1.Strategy)
			c.Mode, c.ModeReason, c.Limits = ordersv1.StrategyMode_STRATEGY_MODE_LIVE, "simulated live account (section 15 promotion)", nil
			live = append(live, c)
		}
	}
	if err := h.ExecSt.Commit(h.Ctx, &execapp.Tx{Strategies: live}); err != nil {
		t.Fatal(err)
	}
	h.RestartExecution()
	h.WaitFor("execution recovery", 15*time.Second, func() bool { return !h.Exec.Engine.Recovering() })
	h.Fresh(gatePrices)
	h.Quiesce()
	g.serveSigned()
	for _, id := range ids {
		if h.mode(id) != ordersv1.StrategyMode_STRATEGY_MODE_LIVE {
			t.Fatalf("setup: %s not live: %s", id, h.mode(id))
		}
	}
}

var gatePrices = map[string]string{"AAPL": "100", "MSFT": "200", "NVDA": "50", "AMD": "80", "SPY": "400", "XLK": "150"}

func steps(res map[string]any) []map[string]any {
	var out []map[string]any
	raw, _ := res["steps"].([]any)
	for _, s := range raw {
		if m, ok := s.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

func compValue(step map[string]any, name string) float64 {
	c, _ := step[name].(map[string]any)
	v, _ := c["value"].(float64)
	return v
}

func compStatus(step map[string]any, name string) string {
	c, _ := step[name].(map[string]any)
	s, _ := c["status"].(string)
	return s
}

// TestGate5MonitoringContainment is the section 13 Gate 5 check (MN-032, MN-035, MN-037, MN-038,
// RM-018, SE-040), end to end over the real service boundary: the monitor core (Python, tb_monitor)
// evaluates injected feature drift, injected live-vs-backtest divergence on recorded paper sessions,
// and a drawdown beyond 1.5× the backtest's p95, and contains through execution's DemoteStrategy
// with its Ed25519 service token. Paper strategies go to halted, live ones to paper through a
// latched strategy kill switch; each demotion is audited with the monitor's reason and evidence,
// alerts reach the bus with their runbook, working entries are canceled, protective stops and
// positions stay, and the manual holding in the same symbol is never touched.
func TestGate5MonitoringContainment(t *testing.T) {
	uv, err := exec.LookPath("uv")
	if err != nil {
		if os.Getenv("TB_MONITORING_GATE") == "1" {
			t.Fatal("uv not found; the monitoring gate needs the Python monitor")
		}
		t.Skip("uv not found: run `make monitoring-gate`")
	}
	h := New(t, Options{AlpacaCash: dec("200000")})
	h.Strategy("orb-15m", "orb15aa", commonv1.Horizon_HORIZON_INTRADAY, Alpaca, "30000", []string{InstrumentID("AAPL")}, true)
	h.Strategy("mlpaper", "mlpaper", commonv1.Horizon_HORIZON_INTRADAY, Alpaca, "10000", nil, true)
	h.Strategy("mllive", "mllive1", commonv1.Horizon_HORIZON_INTRADAY, Alpaca, "10000", nil, true)
	h.Fresh(gatePrices)
	h.Quiesce()
	h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 100)
	h.Quiesce()
	mustApprove(t, h.submit(h.intent("orb-15m", Alpaca, "AAPL", commonv1.Side_SIDE_BUY, "30", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "97", "")))
	h.Quiesce()
	h.workingLimit("orb-15m", "NVDA", "49.50", "45")
	h.workingLimit("mllive", "AMD", "79.50", "75")
	h.Quiesce()

	g := newMonitorGate(t, h, uv)
	g.makeLive(t, "mllive")
	evidence := map[string]any{"generated_at": time.Now().UTC().Format(time.RFC3339)}
	manualOK := func(stage string) {
		t.Helper()
		got := h.Owners(Alpaca, "AAPL")
		if got["manual"] != "100" || got["orb-15m"] != "30" {
			t.Fatalf("%s: positions changed (manual must stay 100, orb-15m 30): %v", stage, got)
		}
		evidence["owners_after_"+stage] = got
	}

	// 1. Injected feature drift on a paper strategy (MN-038): moderate drift raises a warning alert and
	//    does nothing else; severe drift on the top-importance feature breaches and demotes to halted.
	res := g.run(t, "drift", "mlpaper", "mlpaper", "paper")
	evidence["drift"] = res
	st := steps(res)
	if len(st) != 3 || compStatus(st[0], "feature_drift") != "HEALTH_STATUS_OK" || compStatus(st[1], "feature_drift") != "HEALTH_STATUS_WARNING" ||
		st[1]["mode"] != "paper" || compStatus(st[2], "feature_drift") != "HEALTH_STATUS_BREACH" || st[2]["mode"] != "halted" {
		t.Fatalf("drift steps: %v", st)
	}
	if w := g.waitAlert(t, alertsv1.Severity_SEVERITY_WARNING, "Feature drift warning"); w.GetRunbookUrl() != "docs/runbooks/model-monitoring.md" {
		t.Fatalf("drift warning runbook: %v", w)
	}
	if h.mode("mlpaper") != ordersv1.StrategyMode_STRATEGY_MODE_HALTED {
		t.Fatalf("drift breach: mlpaper is %s, want halted", h.mode("mlpaper"))
	}
	if a := h.demoteAudit("mlpaper"); a == nil || a["actor"] != "service:monitor" || !strings.Contains(fmt.Sprint(a["reason"]), "feature_drift") || a["to"] != "STRATEGY_MODE_HALTED" {
		t.Fatalf("drift demotion audit: %v", a)
	}
	manualOK("drift")

	// 2. Injected divergence on recorded paper sessions (MN-037): the replay never sees the AAPL
	//    or NVDA bars, the trade match rate falls well below 0.8, and the paper strategy is demoted to halted.
	res = g.run(t, "divergence", "orb-15m", "orb15aa", "paper")
	evidence["divergence_paper"] = res
	st = steps(res)
	if len(st) != 1 || compStatus(st[0], "divergence") != "HEALTH_STATUS_BREACH" || compValue(st[0], "divergence") >= 0.8 || st[0]["mode"] != "halted" {
		t.Fatalf("divergence (paper) must be beyond the 0.8 match-rate breach: %v", st)
	}
	h.Quiesce()
	if h.mode("orb-15m") != ordersv1.StrategyMode_STRATEGY_MODE_HALTED {
		t.Fatalf("divergence: orb-15m is %s, want halted", h.mode("orb-15m"))
	}
	a := h.demoteAudit("orb-15m")
	if a == nil || a["actor"] != "service:monitor" || !strings.Contains(fmt.Sprint(a["reason"]), "divergence") || a["evidence_id"] == "" || a["from"] != "STRATEGY_MODE_PAPER" {
		t.Fatalf("divergence demotion audit: %v", a)
	}
	oo := h.OpenOrders(Alpaca)
	if !slices.Contains(oo, "orb15aa:AAPL:SIDE_SELL:ORDER_TYPE_STOP") || slices.ContainsFunc(oo, func(o string) bool { return strings.HasPrefix(o, "orb15aa:NVDA") }) {
		t.Fatalf("paper demotion must cancel the working entry and keep the protective stop: %v", oo)
	}
	mustReject(t, h.submit(h.intent("orb-15m", Alpaca, "SPY", commonv1.Side_SIDE_BUY, "1", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "390", "")), "mode")
	manualOK("divergence_paper")

	// 3. The same injected divergence on a strategy in a simulated live account: live → paper through
	//    a latched DEMOTE_TO_PAPER kill switch that blocks new intents until the owner releases it.
	g.makeLive(t, "orb-15m")
	res = g.run(t, "divergence", "orb-15m", "orb15aa", "live")
	evidence["divergence_live"] = res
	st = steps(res)
	if len(st) != 1 || compStatus(st[0], "divergence") != "HEALTH_STATUS_BREACH" || compValue(st[0], "divergence") >= 0.8 || st[0]["mode"] != "paper" {
		t.Fatalf("divergence (live) must be beyond the 0.8 match-rate breach: %v", st)
	}
	h.Quiesce()
	if h.mode("orb-15m") != ordersv1.StrategyMode_STRATEGY_MODE_PAPER {
		t.Fatalf("live divergence: orb-15m is %s, want paper", h.mode("orb-15m"))
	}
	k := h.activeKill("orb-15m")
	if k == nil || k.GetAction() != ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_DEMOTE_TO_PAPER || !strings.Contains(k.GetReason(), "divergence") {
		t.Fatalf("live demotion must latch a DEMOTE_TO_PAPER kill switch: %v", k)
	}
	mustReject(t, h.submit(h.intent("orb-15m", Alpaca, "SPY", commonv1.Side_SIDE_BUY, "1", ordersv1.IntentKind_INTENT_KIND_OPEN, commonv1.Horizon_HORIZON_INTRADAY, "390", "")), "kill_switch")
	manualOK("divergence_live")

	// 4. Drawdown beyond 1.5× the backtest's horizon-matched p95 on a live strategy (MN-032): demoted
	//    to paper; its working entry is canceled.
	res = g.run(t, "drawdown", "mllive", "mllive1", "live")
	evidence["drawdown_live"] = res
	st = steps(res)
	if len(st) != 1 || compStatus(st[0], "drawdown") != "HEALTH_STATUS_BREACH" || compValue(st[0], "drawdown") <= 1.5 || st[0]["mode"] != "paper" {
		t.Fatalf("drawdown (live): %v", st)
	}
	h.Quiesce()
	if h.mode("mllive") != ordersv1.StrategyMode_STRATEGY_MODE_PAPER || h.activeKill("mllive") == nil {
		t.Fatalf("drawdown: mllive is %s (kill %v), want paper with a latched kill switch", h.mode("mllive"), h.activeKill("mllive"))
	}
	if a := h.demoteAudit("mllive"); a == nil || !strings.Contains(fmt.Sprint(a["reason"]), "drawdown") || a["from"] != "STRATEGY_MODE_LIVE" {
		t.Fatalf("drawdown demotion audit: %v", a)
	}
	if slices.ContainsFunc(h.OpenOrders(Alpaca), func(o string) bool { return strings.HasPrefix(o, "mllive1:AMD") }) {
		t.Fatalf("live demotion must cancel the working entry: %v", h.OpenOrders(Alpaca))
	}
	manualOK("drawdown_live")

	// Alerts and events reached the bus: the monitor's containment alerts and events, and
	// execution's own demotion alerts, each with the containment runbook.
	crit := g.waitAlert(t, alertsv1.Severity_SEVERITY_CRITICAL, "demoted")
	if crit.GetRunbookUrl() != "docs/runbooks/containment.md" {
		t.Fatalf("containment alert runbook: %v", crit)
	}
	g.mu.Lock()
	for _, want := range []string{"monitor.containment.mlpaper", "monitor.containment.orb-15m", "monitor.containment.mllive"} {
		if !slices.Contains(g.events, want) {
			g.mu.Unlock()
			t.Fatalf("missing bus event %s in %v", want, g.events)
		}
	}
	var alerts []map[string]string
	for _, a := range g.alerts {
		alerts = append(alerts, map[string]string{"severity": a.GetSeverity().String(), "subject": a.GetSubject(), "runbook": a.GetRunbookUrl()})
	}
	evidence["bus_alerts"], evidence["bus_events"] = alerts, slices.Clone(g.events)
	g.mu.Unlock()

	// Service boundary (SE-040): no token, an untrusted service, a method monitor may not call, and
	// demoting manual are all refused.
	c := g.client(t)
	ctx := context.Background()
	monitorSigner, err := svcauth.NewSigner("monitor", g.seed)
	if err != nil {
		t.Fatal(err)
	}
	strangerSeed, _, _ := svcauth.GenerateKey()
	stranger, _ := svcauth.NewSigner("ledger", strangerSeed)
	boundary := map[string]string{}
	check := func(name string, err error, want codes.Code) {
		t.Helper()
		if status.Code(err) != want {
			t.Fatalf("%s: got %v, want %s", name, err, want)
		}
		boundary[name] = want.String()
	}
	_, err = c.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "orb-15m", Reason: "x"})
	check("no_token", err, codes.Unauthenticated)
	_, err = c.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "orb-15m", Reason: "x"}, stranger.PerRPC("execution"))
	check("untrusted_service", err, codes.Unauthenticated)
	_, err = c.SetStrategyMode(ctx, &ordersv1.SetStrategyModeRequest{StrategyId: "orb-15m", Mode: ordersv1.StrategyMode_STRATEGY_MODE_LIVE}, monitorSigner.PerRPC("execution"))
	check("monitor_set_mode", err, codes.PermissionDenied)
	_, err = c.ReleaseKillSwitch(ctx, &ordersv1.ReleaseKillSwitchRequest{EventId: k.GetEventId(), Reason: "x"}, monitorSigner.PerRPC("execution"))
	check("monitor_release_kill", err, codes.PermissionDenied)
	_, err = c.DemoteStrategy(ctx, &ordersv1.DemoteStrategyRequest{StrategyId: "manual", Reason: "x"}, monitorSigner.PerRPC("execution"))
	check("demote_manual", err, codes.InvalidArgument)
	evidence["service_boundary"] = boundary
	evidence["final_modes"] = map[string]string{"mlpaper": h.mode("mlpaper").String(), "orb-15m": h.mode("orb-15m").String(), "mllive": h.mode("mllive").String()}

	if path := os.Getenv("TB_MONITORING_EVIDENCE"); path != "" {
		b, err := json.MarshalIndent(evidence, "", "  ")
		if err == nil {
			err = os.WriteFile(path, b, 0o600)
		}
		if err != nil {
			t.Error(errors.Join(errors.New("writing monitoring-gate evidence"), err))
		}
	}
}
