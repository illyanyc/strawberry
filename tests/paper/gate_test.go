package paper

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/shopspring/decimal"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	ledgerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/ledger/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	signalsv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/signals/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	execapp "github.com/illyanyc/strawberry/services/execution/app"
)

// orbSymbols are traded by the ORB baseline; AAPL is also held manually (allowlisted against
// symbol segregation) and MSFT is held manually only.
var orbSymbols = []string{"AAPL", "NVDA", "AMD", "XLK", "SPY"}

type pattern int

const (
	patTarget pattern = iota // breakout, then the take-profit leg fills
	patStop                  // breakout, then the stop leg fills
	patHold                  // breakout, then flat: the strategy exits before the close
	patNone                  // no breakout
)

// path returns OHLC for bar i (0..25) of a session for one symbol: the opening range is
// [99, 101] × scale; breakouts happen on bar 2.
func path(p pattern, i int, scale decimal.Decimal) (o, h, l, c decimal.Decimal) {
	f := func(x float64) decimal.Decimal { return decimal.NewFromFloat(x).Mul(scale).Round(2) }
	var open, close float64
	switch {
	case i == 0:
		return f(100), f(101), f(99), f(100.2)
	case i == 1:
		open, close = 100.2, 100.6
	case p == patNone:
		open, close = 100.4, 100.4+0.3*float64(i%3)-0.3
	case i == 2:
		open, close = 100.6, 101.3
	case p == patTarget:
		open, close = 101.3+0.6*float64(i-3), 101.3+0.6*float64(i-2)
	case p == patStop:
		open, close = 101.3-0.4*float64(i-3), 101.3-0.4*float64(i-2)
	default:
		open, close = 101.4, 101.5
	}
	hi, lo := open, close
	if close > open {
		hi, lo = close, open
	}
	return f(open), f(hi + 0.1), f(lo - 0.1), f(close)
}

type runtimeWatch struct {
	mu   sync.Mutex
	hb   *signalsv1.RuntimeHeartbeat
	seen int
}

func (w *runtimeWatch) on(m *nats.Msg) {
	hb := &signalsv1.RuntimeHeartbeat{}
	if proto.Unmarshal(m.Data, hb) == nil {
		w.mu.Lock()
		w.hb = hb
		w.seen++
		w.mu.Unlock()
	}
}

func (w *runtimeWatch) bars() int64 {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.hb.GetBarsProcessed()
}

// SessionEvidence is one simulated session's gate evidence.
type SessionEvidence struct {
	Date             string            `json:"date"`
	Entries          int               `json:"entries"`
	ProtectedEntries int               `json:"protected_entries"`
	Exits            map[string]int    `json:"exits"`
	RejectedIntents  map[string]int    `json:"rejected_intents"`
	CloseRecon       string            `json:"close_recon"`
	OpenBreaks       int               `json:"open_breaks"`
	ManualAAPL       string            `json:"manual_aapl"`
	ManualMSFT       string            `json:"manual_msft"`
	StrategyFlat     bool              `json:"strategy_flat"`
	RealizedPnL      string            `json:"strategy_realized_pnl"`
	Traced           int               `json:"fills_traced"`
	Fills            int               `json:"fills"`
	BrokerVsLedger   map[string]string `json:"broker_vs_ledger"`
}

// TestGate2FiveORBSessionsReconcileAndManualUntouched is the Phase 2 gate: the ORB baseline
// (Python, tbcore.strategy, through the real strategy runtime) trades five simulated sessions on
// the Alpaca-shaped paper account through the full path (bars → runtime → ord.intent → gate →
// OMS → adapter engine → simulator → executions → ledger → reconciliation). Every entry carries
// broker-side protection; a manually held ticker that the strategy also trades keeps its manual
// quantity; every session closes flat with a clean stored reconciliation report and no break
// other than auto-resolved timing differences; every fill traces back to its intent.
func TestGate2FiveORBSessionsReconcileAndManualUntouched(t *testing.T) {
	uv, err := exec.LookPath("uv")
	if err != nil {
		if os.Getenv("TB_PAPER_GATE_REQUIRED") == "1" {
			t.Fatal("uv not found; the paper gate needs the Python strategy runtime")
		}
		t.Skip("uv not found: run `make paper-gate`")
	}
	cal := calendar.XNYS()
	sessions := cal.Sessions(time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC), time.Date(2026, 10, 31, 0, 0, 0, 0, time.UTC))[:5]
	h := New(t, Options{Start: sessions[0].Open.Add(-10 * time.Minute), AlpacaCash: decimal.NewFromInt(60000), Symbols: DefaultSymbols, ExecDeadman: 20 * time.Second})
	ctx := h.Ctx
	ids := make([]string, 0, len(orbSymbols))
	for _, s := range orbSymbols {
		ids = append(ids, h.Instr[s])
	}
	dir := t.TempDir()
	reg := map[string]any{"strategies": []map[string]any{{
		"strategy_id": "orb-15m", "short_id": "orb15aa", "name": "ORB 15m", "horizon": "intraday", "broker_account_id": Alpaca, "mode": "paper",
		"allocation": "30000", "segregation_allowlist": []string{h.Instr["AAPL"]}, "version": "1",
		"runtime": map[string]any{"class": "orb", "timeframe": "15m", "instruments": ids,
			"params": map[string]any{"opening_bars": 1, "risk_per_trade": "0.005", "target_r": "2", "stop_buffer_bps": "5", "exit_minutes_before_close": 15, "max_trades_per_day": 1}},
	}}}
	b, _ := json.Marshal(reg)
	regPath := filepath.Join(dir, "strategies.json")
	if err := os.WriteFile(regPath, b, 0o600); err != nil {
		t.Fatal(err)
	}
	seedReg, err := execapp.LoadRegistry(regPath)
	if err != nil {
		t.Fatal(err)
	}
	if err := execapp.Seed(ctx, h.Exec.Engine, seedReg); err != nil {
		t.Fatal(err)
	}
	// Gate 3 parity (BT-070): optionally record the bus for the event-driven replay.
	if path := os.Getenv("TB_PAPER_RECORDING"); path != "" {
		rec, err := StartRecorder(h.NC, path, func() int64 { return h.Clock.Now().UnixNano() })
		if err != nil {
			t.Fatal(err)
		}
		defer func() {
			h.Quiesce()
			n, err := rec.Close()
			if err != nil {
				t.Errorf("recording: %v", err)
			}
			t.Logf("recorded %d bus messages to %s", n, path)
		}()
	}
	// Manual book: 100 AAPL (also traded by ORB) and 50 MSFT (not traded by ORB).
	h.Fresh(map[string]string{"AAPL": "100", "MSFT": "200", "NVDA": "100", "AMD": "100", "SPY": "100", "XLK": "100"})
	h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 100)
	h.ManualFill(Alpaca, "MSFT", commonv1.Side_SIDE_BUY, 50)
	h.Quiesce()

	watch := &runtimeWatch{}
	sub, err := h.NC.Subscribe("sys.health.strategy-runtime", watch.on)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = sub.Unsubscribe() }()
	root, _ := filepath.Abs("../..")
	rctx, stopRuntime := context.WithCancel(ctx)
	cmd := exec.CommandContext(rctx, uv, "run", "--frozen", "--package", "strategy-runtime", "python", "-m", "tb_strategy_runtime")
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "TB_NATS_URL="+h.URL, "TB_NATS_USER=", "TB_OPS_DISABLED=1", "TB_STRATEGY_REGISTRY="+regPath,
		"TB_HEARTBEAT_INTERVAL=1", "TB_RUNTIME_ID=paper-gate", "TB_LOG_LEVEL=warning")
	stderr, _ := cmd.StderrPipe()
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			if os.Getenv("TB_PAPER_DEBUG") != "" {
				t.Log("runtime:", sc.Text())
			}
		}
	}()
	t.Cleanup(func() { stopRuntime(); _ = cmd.Wait() })
	h.WaitFor("strategy runtime heartbeat", 60*time.Second, func() bool { watch.mu.Lock(); defer watch.mu.Unlock(); return watch.seen > 0 })
	// The runtime was down during setup, so the dead-man may have latched: release it (owner).
	for _, k := range h.Exec.Engine.KillSwitches() {
		if _, err := h.Exec.Engine.Release(ctx, k.GetEventId(), "runtime up", &ordersv1.Actor{Name: "owner", StepUpMethod: "passkey"}); err != nil {
			t.Fatal(err)
		}
	}

	scales := map[string]decimal.Decimal{"AAPL": decimal.NewFromInt(1), "NVDA": decimal.RequireFromString("1.5"), "AMD": decimal.RequireFromString("0.8"),
		"XLK": decimal.RequireFromString("1.2"), "SPY": decimal.RequireFromString("4")}
	manualAAPL, manualMSFT := 100, 50
	var evidence []SessionEvidence
	expectBars := int64(0)
	for n, s := range sessions {
		ev := SessionEvidence{Date: s.Date, Exits: map[string]int{}, RejectedIntents: map[string]int{}, BrokerVsLedger: map[string]string{}}
		sessionStart := s.Open.UnixNano()
		for i := 0; i < 26; i++ {
			closeT := s.Open.Add(time.Duration(i+1) * 15 * time.Minute)
			h.Clock.Set(closeT.Add(61 * time.Second))
			for k, sym := range orbSymbols {
				p := pattern((n + k) % 4)
				o, hi, lo, c := path(p, i, scales[sym])
				// Intrabar path at the simulated broker (protective legs match on it), then the bar.
				for _, px := range []decimal.Decimal{o, hi, lo, c} {
					h.Alpaca.SetPrice(sym, px)
				}
				bar := &mdv1.Bar{InstrumentId: h.Instr[sym], Timeframe: mdv1.Timeframe_TIMEFRAME_15M, TsOpen: closeT.Add(-15 * time.Minute).UnixNano(),
					TsClose: closeT.UnixNano(), Open: d(o), High: d(hi), Low: d(lo), Close: d(c), Volume: d(decimal.NewFromInt(100000)), Feed: "sim",
					IsFinal: true, AvailableAt: closeT.Add(time.Minute).UnixNano(), Source: "sim"}
				payload, _ := proto.Marshal(bar)
				h.publish(fmt.Sprintf("md.bar.15m.%s", h.Instr[sym]), fmt.Sprintf("bar:%s:%d", h.Instr[sym], bar.GetTsOpen()), payload)
				expectBars++
			}
			// MSFT is not traded but keeps the account's marks fresh.
			h.Alpaca.SetPrice("MSFT", decimal.NewFromInt(200))
			want := expectBars
			h.WaitFor(fmt.Sprintf("runtime processed bar %d of %s", i, s.Date), 30*time.Second, func() bool { return watch.bars() >= want })
			h.Quiesce()
			// Manual activity outside the platform during the session.
			if n == 2 && i == 5 {
				h.ManualFill(Alpaca, "MSFT", commonv1.Side_SIDE_SELL, 20)
				manualMSFT -= 20
				h.Quiesce()
			}
			if n == 3 && i == 7 {
				h.ManualFill(Alpaca, "AAPL", commonv1.Side_SIDE_BUY, 10)
				manualAAPL += 10
				h.Quiesce()
			}
		}
		// After the close: the ledger's session-close reconciliation is the stored daily report.
		// Step into the intraday-flatten window (OE-016, nothing left to flatten) with fresh
		// snapshots, then past the close.
		h.Clock.Set(s.Close.Add(-30 * time.Second))
		h.Quiesce()
		h.Clock.Set(s.Close.Add(5 * time.Second))
		h.Quiesce()
		var closeRep *ledgerv1.ReconciliationResult
		h.WaitFor("session-close reconciliation report "+s.Date, 10*time.Second, func() bool {
			reps, _ := h.LedgerCl.ListReconciliationReports(ctx, &ledgerv1.ListReconciliationReportsRequest{BrokerAccountId: Alpaca, Since: s.Close.UnixNano()})
			for _, r := range reps.GetReports() {
				if r.GetTrigger() == "session_close" {
					closeRep = r
					return true
				}
			}
			return false
		})
		ev.CloseRecon = closeRep.GetStatus().String()
		if closeRep.GetStatus() != ledgerv1.ReconciliationStatus_RECONCILIATION_STATUS_OK {
			t.Fatalf("%s: session-close reconciliation %s diffs %v breaks %v (as_of %s snapshot %s)", s.Date, closeRep.GetStatus(), closeRep.GetDiffs(), closeRep.GetBreaks(), time.Unix(0, closeRep.GetAsOf()).UTC(), time.Unix(0, closeRep.GetSnapshotAsOf()).UTC())
		}
		ev.OpenBreaks = len(closeRep.GetBreaks())
		aapl, msft := h.Owners(Alpaca, "AAPL"), h.Owners(Alpaca, "MSFT")
		ev.ManualAAPL, ev.ManualMSFT = aapl["manual"], msft["manual"]
		if aapl["manual"] != fmt.Sprint(manualAAPL) || msft["manual"] != fmt.Sprint(manualMSFT) {
			t.Fatalf("%s: manual holdings changed: AAPL %v MSFT %v (want %d / %d)", s.Date, aapl, msft, manualAAPL, manualMSFT)
		}
		if got := h.Alpaca.Position(ctx, Alpaca, "AAPL"); !got.Equal(decimal.NewFromInt(int64(manualAAPL))) {
			t.Fatalf("%s: broker AAPL %s, want manual %d after the strategy is flat", s.Date, got, manualAAPL)
		}
		pv, err := h.LedgerCl.GetStrategyPortfolio(ctx, &ledgerv1.GetStrategyPortfolioRequest{StrategyId: "orb-15m"})
		if err != nil {
			t.Fatal(err)
		}
		ev.StrategyFlat = true
		for _, p := range pv.GetPortfolio().GetPositions() {
			if p.GetQty().GetValue() != "0" {
				ev.StrategyFlat = false
			}
		}
		if !ev.StrategyFlat {
			t.Fatalf("%s: ORB not flat at the close: %v", s.Date, pv.GetPortfolio().GetPositions())
		}
		ev.RealizedPnL = pv.GetPortfolio().GetRealizedPnl().GetValue()
		for _, sym := range append([]string{"MSFT"}, orbSymbols...) {
			o := h.Owners(Alpaca, sym)
			ev.BrokerVsLedger[sym] = fmt.Sprintf("broker %s / manual %s / orb %s", o["broker"], o["manual"], o["orb-15m"])
		}
		// Entries, protection, exits, rejections, and traceability for this session.
		for _, o := range h.Exec.Engine.Orders(Alpaca, "orb-15m", false, sessionStart, 0) {
			switch o.GetRole() {
			case ordersv1.OrderRole_ORDER_ROLE_ENTRY:
				if o.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
					ev.Entries++
					for _, x := range h.Exec.Engine.Orders(Alpaca, "orb-15m", false, sessionStart, 0) {
						if x.GetParentOrderId() == o.GetOrderId() && x.GetRole() == ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP {
							ev.ProtectedEntries++
							break
						}
					}
				}
			case ordersv1.OrderRole_ORDER_ROLE_EXIT, ordersv1.OrderRole_ORDER_ROLE_PROTECTIVE_STOP, ordersv1.OrderRole_ORDER_ROLE_TAKE_PROFIT:
				if o.GetState() == ordersv1.OrderState_ORDER_STATE_FILLED {
					ev.Exits[o.GetRole().String()]++
				}
			}
		}
		if ev.ProtectedEntries != ev.Entries {
			t.Fatalf("%s: %d of %d entries had broker-side protection", s.Date, ev.ProtectedEntries, ev.Entries)
		}
		for _, it := range h.Exec.Engine.Intents("orb-15m", true, sessionStart, 0) {
			ev.RejectedIntents[it.GetDecision().GetFirstFailure()]++
		}
		for _, f := range h.Exec.Engine.Fills(Alpaca, "orb-15m", sessionStart, 0) {
			ev.Fills++
			tr := h.Exec.Engine.Trace(f.GetCorrelationId())
			if len(tr.GetIntents()) == 1 && tr.GetIntents()[0].GetDecision().GetApproved() && len(tr.GetOrders()) > 0 && len(tr.GetFills()) > 0 && ledgerSaw(h, f.GetBrokerExecId()) {
				ev.Traced++
			}
		}
		if ev.Traced != ev.Fills {
			t.Fatalf("%s: %d of %d fills traced signal → intent → decision → order → fill → ledger", s.Date, ev.Traced, ev.Fills)
		}
		evidence = append(evidence, ev)
		t.Logf("%s: entries %d (protected %d) exits %v rejected %v recon %s manual AAPL %s MSFT %s realized %s", ev.Date, ev.Entries, ev.ProtectedEntries,
			ev.Exits, ev.RejectedIntents, ev.CloseRecon, ev.ManualAAPL, ev.ManualMSFT, ev.RealizedPnL)
	}
	// No break other than auto-resolved timing differences over the five sessions (VP-008).
	for _, bk := range h.Ledger.Engine.Breaks(Alpaca, false) {
		if bk.GetType() != ledgerv1.BreakType_BREAK_TYPE_TIMING || bk.GetStatus() == ledgerv1.BreakStatus_BREAK_STATUS_OPEN {
			t.Fatalf("unexplained break: %v", bk)
		}
	}
	if diffs, err := h.Ledger.Engine.VerifyMaterialized(ctx); err != nil || len(diffs) > 0 {
		t.Fatalf("ledger rebuild differs: %v %v", diffs, err)
	}
	total := 0
	for _, ev := range evidence {
		total += ev.Entries
	}
	if total < 5 {
		t.Fatalf("only %d ORB entries over five sessions", total)
	}
	if out := os.Getenv("TB_PAPER_EVIDENCE"); out != "" {
		b, _ := json.MarshalIndent(map[string]any{"sessions": evidence, "timing_breaks_auto_resolved": len(h.Ledger.Engine.Breaks(Alpaca, false))}, "", "  ")
		_ = os.WriteFile(out, b, 0o600)
	}
}

func d(v decimal.Decimal) *commonv1.Decimal { return &commonv1.Decimal{Value: v.String()} }

func ledgerSaw(h *Harness, execID string) bool {
	for _, m := range h.LedgerSt.Messages("ledger.update." + Alpaca) {
		u := &ledgerv1.LedgerUpdate{}
		if proto.Unmarshal(m.Payload, u) == nil && u.GetSourceId() == execID {
			return true
		}
	}
	return false
}
