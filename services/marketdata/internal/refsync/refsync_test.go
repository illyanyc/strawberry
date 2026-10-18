package refsync

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"google.golang.org/grpc"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/services/marketdata/internal/fmp"
	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
)

type fakeBroker struct {
	brokerv1.BrokerMarketDataServiceClient
	actions []*mdv1.CorporateAction
}

func (f fakeBroker) ListAssets(context.Context, *brokerv1.ListAssetsRequest, ...grpc.CallOption) (*brokerv1.ListAssetsResponse, error) {
	return &brokerv1.ListAssetsResponse{Assets: []*brokerv1.Asset{
		{Symbol: "NVDA", BrokerAssetId: "a-nvda", Exchange: "NASDAQ", Tradable: true, Shortable: true, EasyToBorrow: true, Fractionable: true, Marginable: true},
		{Symbol: "AAPL", BrokerAssetId: "a-aapl", Exchange: "NASDAQ", Tradable: true},
		{Symbol: "META", BrokerAssetId: "a-meta", Exchange: "NASDAQ", Tradable: true},
	}}, nil
}

func (f fakeBroker) GetCorporateActions(context.Context, *brokerv1.GetCorporateActionsRequest, ...grpc.CallOption) (*brokerv1.GetCorporateActionsResponse, error) {
	return &brokerv1.GetCorporateActionsResponse{Actions: f.actions}, nil
}

type fakeFMP struct{}

func load[T any](name string) []T {
	raw, _ := os.ReadFile("../../../../tests/fixtures/fmp/" + name)
	var out []T
	_ = json.Unmarshal(raw, &out)
	return out
}

func (fakeFMP) DelistedCompanies(context.Context, int) ([]fmp.Delisted, error) {
	return load[fmp.Delisted]("delisted_companies.json"), nil
}
func (fakeFMP) SymbolChanges(context.Context, int) ([]fmp.SymbolChange, error) {
	return load[fmp.SymbolChange]("symbol_change.json"), nil
}
func (fakeFMP) Splits(_ context.Context, s string) ([]fmp.Split, error) {
	if s == "NVDA" {
		return load[fmp.Split]("splits_nvda.json"), nil
	}
	return nil, nil
}
func (fakeFMP) Dividends(_ context.Context, s string) ([]fmp.Dividend, error) {
	if s == "AAPL" {
		return load[fmp.Dividend]("dividends_aapl.json"), nil
	}
	return nil, nil
}

func TestMD012CrossCheckAgreementAndDisagreement(t *testing.T) {
	ctx := context.Background()
	ref := refdata.NewMemory()
	// Instruments existed before the 2022 symbol change.
	meta, _, _ := ref.EnsureInstrument(ctx, "FB", dateNS("2012-05-18"), refdata.Instrument{AssetClass: "us_equity", ListedAt: dateNS("2012-05-18")}, "test")
	nvda, _, _ := ref.EnsureInstrument(ctx, "NVDA", dateNS("1999-01-22"), refdata.Instrument{AssetClass: "us_equity", ListedAt: dateNS("1999-01-22")}, "test")
	aapl, _, _ := ref.EnsureInstrument(ctx, "AAPL", dateNS("1980-12-12"), refdata.Instrument{AssetClass: "us_equity", ListedAt: dateNS("1980-12-12")}, "test")
	var alerts []string
	var published []*mdv1.CorporateAction
	s := &Syncer{Ref: ref, FMP: fakeFMP{}, Now: func() time.Time { return time.Date(2026, 10, 17, 12, 0, 0, 0, time.UTC) },
		Alert:   func(_ context.Context, _, m string) { alerts = append(alerts, m) },
		Publish: func(_ context.Context, a *mdv1.CorporateAction) error { published = append(published, a); return nil },
		Broker: fakeBroker{actions: []*mdv1.CorporateAction{
			// Alpaca agrees with FMP on NVDA 2024 (10:1) but reports AAPL's dividend with a different amount.
			{Symbol: "NVDA", Type: mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPLIT, ExDate: dateNS("2024-06-10"), Ratio: &commonv1.Decimal{Value: "10"}, SourceId: "fs-1"},
			{Symbol: "AAPL", Type: mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DIVIDEND, ExDate: dateNS("2025-02-10"), Amount: &commonv1.Decimal{Value: "0.26"}, SourceId: "cd-1"},
		}}}
	if err := s.Universe(ctx); err != nil {
		t.Fatal(err)
	}
	before := len(ref.Instruments)
	if _, err := s.Assets(ctx); err != nil {
		t.Fatal(err)
	}
	if len(ref.Instruments) != before {
		t.Fatalf("assets sync created %d duplicate instruments for known tickers", len(ref.Instruments)-before)
	}
	// FB → META rename applied to the same instrument; delisted TWTR/SIVB created and closed.
	if id, _ := ref.Resolve(ctx, "META", dateNS("2024-01-02")); id != meta {
		t.Fatal("symbol change not applied to the existing instrument")
	}
	twtr, err := ref.Resolve(ctx, "TWTR", dateNS("2020-01-02"))
	if err != nil || ref.Instruments[twtr].DelistedAt != dateNS("2022-11-08") {
		t.Fatalf("delisted TWTR: %v %+v", err, ref.Instruments[twtr])
	}
	if _, err := ref.Resolve(ctx, "TWTR", dateNS("2023-01-03")); err == nil {
		t.Fatal("TWTR must not resolve after delisting")
	}
	stored, disagreed, err := s.CorporateActions(ctx, []string{nvda, aapl}, time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	if stored != 5 || disagreed != 2 || len(alerts) != 2 {
		t.Fatalf("stored=%d disagreed=%d alerts=%v", stored, disagreed, alerts)
	}
	got := map[string]string{}
	for _, a := range append(ref.ActionsFor(ctx, nvda), ref.ActionsFor(ctx, aapl)...) {
		got[a.Source+"|"+a.Type+"|"+time.Unix(0, a.ExDate).In(ny).Format("2006-01-02")] = a.Agreement
	}
	want := map[string]string{
		"alpaca|split|2024-06-10": "agreed", "fmp|split|2024-06-10": "agreed", "fmp|split|2021-07-20": "single_source",
		"alpaca|dividend|2025-02-10": "disagreed", "fmp|dividend|2025-02-10": "disagreed",
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("%s: %q want %q", k, got[k], v)
		}
	}
	// FMP dividends carry the declaration date, so availability precedes the ex-date (MD-011).
	for _, a := range ref.ActionsFor(ctx, aapl) {
		if a.Source == "fmp" && a.AvailableAt != dateNS("2025-01-30") {
			t.Fatalf("dividend available_at %d", a.AvailableAt)
		}
	}
	if len(published) == 0 {
		t.Fatal("corporate actions must be published for the ledger")
	}
	// The owner resolves the AAPL dividend; the next sync must not undo it.
	for _, a := range ref.ActionsFor(ctx, aapl) {
		if a.Type == "dividend" {
			st := refdata.AgreementConfirmed
			if a.Source == "alpaca" {
				st = refdata.AgreementRejected
			}
			_ = ref.SetAgreement(ctx, a.Source, a.SourceID, st)
		}
	}
	if _, disagreed, err = s.CorporateActions(ctx, []string{nvda, aapl}, time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC), time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)); err != nil || disagreed != 0 {
		t.Fatalf("after owner resolution: disagreed=%d err=%v", disagreed, err)
	}
	for _, a := range ref.ActionsFor(ctx, aapl) {
		if a.Type == "dividend" && !refdata.OwnerResolved(a.Agreement) {
			t.Fatalf("owner resolution overwritten: %s %s", a.Source, a.Agreement)
		}
	}
}

func TestMD012NearMissExDatesDisagree(t *testing.T) {
	st := CrossCheck([]refdata.CorporateAction{
		{Source: "alpaca", SourceID: "a", InstrumentID: "X", Type: "split", Ratio: "2", ExDate: dateNS("2025-05-05")},
		{Source: "fmp", SourceID: "b", InstrumentID: "X", Type: "split", Ratio: "2", ExDate: dateNS("2025-05-06")},
	})
	if st["alpaca|a"] != "disagreed" || st["fmp|b"] != "disagreed" {
		t.Fatalf("near-miss ex-dates: %v", st)
	}
}
