package dq

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/illyanyc/strawberry/libs/go/calendar"
)

const fixtures = "../../../../tests/fixtures/dq/"

func loadFixture(t *testing.T) []Bar {
	t.Helper()
	f, err := os.Open(fixtures + "bars_1m_injected.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	recs, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	var out []Bar
	for _, r := range recs[1:] {
		to, _ := time.Parse(time.RFC3339, r[1])
		tc, _ := time.Parse(time.RFC3339, r[2])
		p := func(s string) float64 { v, _ := strconv.ParseFloat(s, 64); return v }
		out = append(out, Bar{InstrumentID: r[0], TsOpen: to, TsClose: tc, Open: p(r[3]), High: p(r[4]), Low: p(r[5]), Close: p(r[6]), Volume: p(r[7])})
	}
	return out
}

func TestST010DQFlagsInjectedDefects(t *testing.T) {
	var exp struct {
		Counts map[string]int `json:"counts"`
		Gaps   []struct {
			InstrumentID string `json:"instrument_id"`
			From, To     string
			Missing      int `json:"missing"`
		} `json:"gaps"`
		Clean []string `json:"clean_instruments"`
	}
	raw, _ := os.ReadFile(fixtures + "expected.json")
	if err := json.Unmarshal(raw, &exp); err != nil {
		t.Fatal(err)
	}
	from := time.Date(2026, 3, 6, 0, 0, 0, 0, time.UTC)
	rep := Check(loadFixture(t), []string{"INSTA", "INSTB"}, calendar.XNYS(), from, from.Add(24*time.Hour), Options{Timeframe: time.Minute})
	for check, n := range exp.Counts {
		if rep.Counts[check] != n {
			t.Errorf("%s: got %d want %d", check, rep.Counts[check], n)
		}
	}
	if len(rep.Counts) != len(exp.Counts) {
		t.Errorf("unexpected checks fired: %v", rep.Counts)
	}
	if len(rep.Gaps) != 1 || rep.Gaps[0].Missing != 10 || rep.Gaps[0].From.Format(time.RFC3339) != exp.Gaps[0].From || rep.Gaps[0].To.Format(time.RFC3339) != exp.Gaps[0].To {
		t.Errorf("gaps %+v", rep.Gaps)
	}
	for _, f := range rep.Findings {
		if f.InstrumentID == "INSTB" {
			t.Errorf("clean instrument flagged: %+v", f)
		}
	}
}

func TestST010HalfDayAndMissingInstrument(t *testing.T) {
	cal := calendar.XNYS()
	s, _ := cal.Session("2025-11-28")
	var bars []Bar
	for _, op := range s.BarOpens(15*time.Minute, false) {
		bars = append(bars, Bar{InstrumentID: "A", TsOpen: op, TsClose: s.BarClose(op, 15*time.Minute), Open: 1, High: 1, Low: 1, Close: 1, Volume: 1})
	}
	from := s.Open.Add(-time.Hour)
	rep := Check(bars, []string{"A", "GONE"}, cal, from, from.Add(24*time.Hour), Options{Timeframe: 15 * time.Minute})
	if rep.Counts["gap"] != 1 || rep.Gaps[0].InstrumentID != "GONE" || rep.Gaps[0].Missing != 14 {
		t.Fatalf("half day: %+v", rep)
	}
}
