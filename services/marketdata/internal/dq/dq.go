// Package dq implements the data-quality checks of rule ST-010 for bars (gaps vs the exchange
// calendar, duplicates, OHLC consistency, non-positive prices, negative volume, misaligned
// timestamps, volume spikes). The Python implementation (tbcore.dq) applies the same rules and both
// are tested against tests/fixtures/dq.
package dq

import (
	"fmt"
	"sort"
	"time"

	"github.com/illyanyc/strawberry/libs/go/calendar"
)

// Bar is the minimal bar view the checks need.
type Bar struct {
	InstrumentID string
	TsOpen       time.Time
	TsClose      time.Time
	Open, High   float64
	Low, Close   float64
	Volume       float64
}

// Finding is one defect.
type Finding struct {
	Check        string    `json:"check"`
	InstrumentID string    `json:"instrument_id"`
	TsOpen       time.Time `json:"ts_open"`
	Detail       string    `json:"detail"`
}

// Gap is a run of consecutive missing expected bars within one session.
type Gap struct {
	InstrumentID string    `json:"instrument_id"`
	From         time.Time `json:"from"` // first missing bar open
	To           time.Time `json:"to"`   // open of the first bar after the run
	Missing      int       `json:"missing"`
}

// Report is the result of Check.
type Report struct {
	Rows     int            `json:"rows"`
	Findings []Finding      `json:"findings"`
	Gaps     []Gap          `json:"gaps"`
	Counts   map[string]int `json:"counts"`
}

// Clean reports whether no defect (other than gaps below MinGapRun) was found.
func (r Report) Clean() bool { return len(r.Findings) == 0 && len(r.Gaps) == 0 }

// Options tune the checks.
type Options struct {
	Timeframe time.Duration
	// Extended includes pre/post-market bars in the expected grid.
	Extended bool
	// MinGapRun reports gaps of at least this many consecutive missing bars (default 1).
	MinGapRun int
	// SpikeFactor flags volume above factor × trailing median (default 20) over SpikeWindow bars
	// (default 20).
	SpikeFactor float64
	SpikeWindow int
	// Daily marks daily bars (one per session at the session open).
	Daily bool
}

func (o *Options) defaults() {
	if o.MinGapRun <= 0 {
		o.MinGapRun = 1
	}
	if o.SpikeFactor <= 0 {
		o.SpikeFactor = 20
	}
	if o.SpikeWindow <= 0 {
		o.SpikeWindow = 20
	}
}

// Check validates bars against the calendar over sessions whose open is in [from, to).
// instruments lists every instrument expected to have bars (so a fully missing one is a gap).
func Check(bars []Bar, instruments []string, cal *calendar.Calendar, from, to time.Time, o Options) Report {
	o.defaults()
	rep := Report{Rows: len(bars), Counts: map[string]int{}}
	add := func(check, inst string, ts time.Time, detail string) {
		rep.Findings = append(rep.Findings, Finding{Check: check, InstrumentID: inst, TsOpen: ts, Detail: detail})
		rep.Counts[check]++
	}
	byInst := map[string][]Bar{}
	for _, b := range bars {
		byInst[b.InstrumentID] = append(byInst[b.InstrumentID], b)
	}
	for _, id := range instruments {
		if _, ok := byInst[id]; !ok {
			byInst[id] = nil
		}
	}
	sessions := cal.Sessions(from, to)
	ids := make([]string, 0, len(byInst))
	for id := range byInst {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		bs := byInst[id]
		for i := 1; i < len(bs); i++ {
			if bs[i].TsOpen.Before(bs[i-1].TsOpen) {
				add("non_monotonic", id, bs[i].TsOpen, "ts_open decreases in input order")
				break
			}
		}
		sort.SliceStable(bs, func(i, j int) bool { return bs[i].TsOpen.Before(bs[j].TsOpen) })
		present := map[int64]bool{}
		var vols []float64
		for i, b := range bs {
			if i > 0 && b.TsOpen.Equal(bs[i-1].TsOpen) {
				add("duplicate", id, b.TsOpen, "same ts_open twice")
				continue
			}
			present[b.TsOpen.UnixNano()] = true
			if b.Open <= 0 || b.High <= 0 || b.Low <= 0 || b.Close <= 0 {
				add("nonpositive_price", id, b.TsOpen, fmt.Sprintf("o=%g h=%g l=%g c=%g", b.Open, b.High, b.Low, b.Close))
			} else if b.High < max(b.Open, b.Close, b.Low) || b.Low > min(b.Open, b.Close, b.High) {
				add("ohlc_inconsistent", id, b.TsOpen, fmt.Sprintf("o=%g h=%g l=%g c=%g", b.Open, b.High, b.Low, b.Close))
			}
			if b.Volume < 0 {
				add("negative_volume", id, b.TsOpen, fmt.Sprintf("v=%g", b.Volume))
			}
			if !b.TsClose.After(b.TsOpen) || !aligned(b.TsOpen, cal, o) {
				add("misaligned_ts", id, b.TsOpen, "ts_open off the session grid or ts_close <= ts_open")
			}
			if len(vols) >= o.SpikeWindow {
				med := median(vols[len(vols)-o.SpikeWindow:])
				if med > 0 && b.Volume > o.SpikeFactor*med {
					add("volume_spike", id, b.TsOpen, fmt.Sprintf("v=%g median=%g", b.Volume, med))
				}
			}
			if b.Volume >= 0 {
				vols = append(vols, b.Volume)
			}
		}
		for _, s := range sessions {
			var opens []time.Time
			if o.Daily {
				opens = []time.Time{s.Open}
			} else {
				opens = s.BarOpens(o.Timeframe, o.Extended)
			}
			run := 0
			var runStart time.Time
			flush := func(next time.Time) {
				if run >= o.MinGapRun {
					rep.Gaps = append(rep.Gaps, Gap{InstrumentID: id, From: runStart, To: next, Missing: run})
					rep.Counts["gap"]++
				}
				run = 0
			}
			for _, op := range opens {
				if present[op.UnixNano()] {
					if run > 0 {
						flush(op)
					}
					continue
				}
				if run == 0 {
					runStart = op
				}
				run++
			}
			if run > 0 {
				end := s.Close
				if o.Extended {
					end = s.PostClose
				}
				flush(end)
			}
		}
	}
	return rep
}

func aligned(t time.Time, cal *calendar.Calendar, o Options) bool {
	s, _, ok := cal.SessionAt(t)
	if !ok {
		return false
	}
	if o.Daily {
		return t.Equal(s.Open)
	}
	base := s.Open
	if t.Before(s.Open) {
		base = s.PreOpen
	}
	return t.Sub(base)%o.Timeframe == 0
}

func median(xs []float64) float64 {
	c := append([]float64(nil), xs...)
	sort.Float64s(c)
	n := len(c)
	if n%2 == 1 {
		return c[n/2]
	}
	return (c[n/2-1] + c[n/2]) / 2
}
