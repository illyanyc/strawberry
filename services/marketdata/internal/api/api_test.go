package api

import (
	"context"
	"testing"
	"time"

	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/libs/go/calendar"
	"github.com/illyanyc/strawberry/services/marketdata/internal/live"
)

type nopSink struct{}

func (nopSink) Final(context.Context, *mdv1.Bar) error      { return nil }
func (nopSink) Correction(context.Context, *mdv1.Bar) error { return nil }
func (nopSink) Partial(context.Context, *mdv1.Bar)          {}

func TestMD007IsFreshFailsClosedAndAllowlist(t *testing.T) {
	p := live.New(calendar.XNYS(), nil, nopSink{}, "alpaca.sip", nil, nil)
	s := &Server{Pipe: p, Feed: "alpaca.sip", Now: time.Now}
	r, err := s.IsFresh(context.Background(), &mdv1.IsFreshRequest{InstrumentId: "I-X"})
	if err != nil || r.GetFresh() || r.GetReason() == "" {
		t.Fatalf("unknown/disconnected instrument must not be fresh: %+v %v", r, err)
	}
	if _, err := s.IsFresh(context.Background(), &mdv1.IsFreshRequest{}); err == nil {
		t.Fatal("missing instrument_id accepted")
	}
	none := &Server{Feed: "alpaca.sip", Now: time.Now}
	if r, _ := none.IsFresh(context.Background(), &mdv1.IsFreshRequest{InstrumentId: "I-X"}); r.GetFresh() {
		t.Fatal("streaming disabled must not report fresh")
	}
	for m, callers := range AllowedCallers {
		for _, c := range callers {
			if c == "ledger" || c == "marketdata" {
				t.Fatalf("%s allows %s", m, c)
			}
		}
	}
	if len(AllowedCallers[svc+"IsFresh"]) == 0 || AllowedCallers[svc+"IsFresh"][0] != "execution" {
		t.Fatal("execution must be able to call IsFresh before orders (RM-004)")
	}
	fs, _ := s.GetFeedStatus(context.Background(), &mdv1.GetFeedStatusRequest{})
	if len(fs.GetFeeds()) != 1 || fs.GetFeeds()[0].GetConnected() {
		t.Fatalf("feed status %+v", fs)
	}
}
