// Package refsync keeps the instrument master, universes, and corporate actions current from
// Alpaca (through broker-gateway) and FMP (section 05, rules DF-006, MD-011, MD-012).
package refsync

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/shopspring/decimal"

	brokerv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/broker/v1"
	commonv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/common/v1"
	mdv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/marketdata/v1"
	"github.com/illyanyc/strawberry/services/marketdata/internal/fmp"
	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
)

// Ref is the reference store (refdata.PG in production).
type Ref interface {
	Resolve(ctx context.Context, symbol string, at int64) (string, error)
	EnsureInstrument(ctx context.Context, symbol string, at int64, inst refdata.Instrument, source string) (string, bool, error)
	RenameSymbol(ctx context.Context, instrumentID, newSymbol string, at int64, source string) error
	MarkDelisted(ctx context.Context, instrumentID string, at int64) error
	UpsertBrokerID(ctx context.Context, b refdata.BrokerID) error
	UpsertAttributes(ctx context.Context, a refdata.Attributes) (bool, error)
	UpsertAction(ctx context.Context, a refdata.CorporateAction) (bool, error)
	SetAgreement(ctx context.Context, source, sourceID, status string) error
	ActionsFor(ctx context.Context, instrumentID string) []refdata.CorporateAction
	SetUniverse(ctx context.Context, universe string, members []string, at int64) error
	History(ctx context.Context, instrumentID string) []refdata.SymbolSpan
}

// FMP is the subset of the FMP client used here.
type FMP interface {
	DelistedCompanies(ctx context.Context, maxPages int) ([]fmp.Delisted, error)
	SymbolChanges(ctx context.Context, limit int) ([]fmp.SymbolChange, error)
	Splits(ctx context.Context, symbol string) ([]fmp.Split, error)
	Dividends(ctx context.Context, symbol string) ([]fmp.Dividend, error)
}

// Publisher emits corporate-action events (bus subject ref.corporate_action.<instrument_id>).
type Publisher func(ctx context.Context, a *mdv1.CorporateAction) error

// Syncer runs the daily reference-data sync.
type Syncer struct {
	Ref     Ref
	Broker  brokerv1.BrokerMarketDataServiceClient
	FMP     FMP
	Publish Publisher
	Alert   func(ctx context.Context, subject, msg string)
	Log     *slog.Logger
	Now     func() time.Time
}

var ny = func() *time.Location { l, _ := time.LoadLocation("America/New_York"); return l }()

func dateNS(s string) int64 {
	t, err := time.ParseInLocation("2006-01-02", s, ny)
	if err != nil {
		return 0
	}
	return t.UnixNano()
}

// Sync runs the full daily sequence: ticker changes and delistings first (so a renamed instrument
// keeps its instrument_id), then broker assets, then corporate actions for the given instruments.
func (s *Syncer) Sync(ctx context.Context, instruments []string, from, to time.Time) error {
	if err := s.Universe(ctx); err != nil {
		return fmt.Errorf("universe: %w", err)
	}
	if _, err := s.Assets(ctx); err != nil {
		return fmt.Errorf("assets: %w", err)
	}
	if _, _, err := s.CorporateActions(ctx, instruments, from, to); err != nil {
		return fmt.Errorf("corporate actions: %w", err)
	}
	return nil
}

// Assets syncs tradable instruments and attributes from Alpaca (daily, with validity ranges).
func (s *Syncer) Assets(ctx context.Context) (created int, err error) {
	resp, err := s.Broker.ListAssets(ctx, &brokerv1.ListAssetsRequest{IncludeInactive: false})
	if err != nil {
		return 0, err
	}
	now := s.Now().UnixNano()
	for _, a := range resp.GetAssets() {
		if a.GetSymbol() == "" || strings.ContainsAny(a.GetSymbol(), " /") {
			continue
		}
		class := "us_equity"
		id, isNew, err := s.Ref.EnsureInstrument(ctx, a.GetSymbol(), now, refdata.Instrument{AssetClass: class, Name: a.GetName(), PrimaryExchange: a.GetExchange()}, "alpaca")
		if err != nil {
			return created, err
		}
		if isNew {
			created++
		}
		if err := s.Ref.UpsertBrokerID(ctx, refdata.BrokerID{InstrumentID: id, Broker: "alpaca", BrokerInstrumentID: a.GetBrokerAssetId(), ValidFrom: now}); err != nil {
			return created, err
		}
		if _, err := s.Ref.UpsertAttributes(ctx, refdata.Attributes{InstrumentID: id, Source: "alpaca", Tradable: a.GetTradable(), Shortable: a.GetShortable(),
			EasyToBorrow: a.GetEasyToBorrow(), Fractionable: a.GetFractionable(), Marginable: a.GetMarginable(), ValidFrom: now}); err != nil {
			return created, err
		}
	}
	return created, nil
}

// Universe applies FMP's delisted list and symbol changes (survivorship-free universe, DF-006, D20).
func (s *Syncer) Universe(ctx context.Context) error {
	if s.FMP == nil {
		return nil
	}
	changes, err := s.FMP.SymbolChanges(ctx, 1000)
	if err != nil && !errors.Is(err, fmp.ErrDisabled) {
		return err
	}
	sort.Slice(changes, func(i, j int) bool { return changes[i].Date < changes[j].Date })
	for _, c := range changes {
		at := dateNS(c.Date)
		if id, err := s.Ref.Resolve(ctx, c.OldSymbol, at-1); err == nil {
			if err := s.Ref.RenameSymbol(ctx, id, c.NewSymbol, at, "fmp"); err != nil && s.Log != nil {
				s.Log.Warn("symbol change not applied", "old", c.OldSymbol, "new", c.NewSymbol, "err", err)
			}
		}
	}
	del, err := s.FMP.DelistedCompanies(ctx, 200)
	if err != nil && !errors.Is(err, fmp.ErrDisabled) {
		return err
	}
	for _, d := range del {
		at := dateNS(d.DelistedDate)
		if at == 0 {
			continue
		}
		id, err := s.Ref.Resolve(ctx, d.Symbol, at-1)
		if err != nil {
			listed := dateNS(d.IPODate)
			if listed == 0 || listed >= at {
				listed = at - int64(24*time.Hour)
			}
			// Unknown to the broker (already delisted): create it so historical universes include it.
			id, _, err = s.Ref.EnsureInstrument(ctx, d.Symbol, listed, refdata.Instrument{AssetClass: "us_equity", Name: d.CompanyName,
				PrimaryExchange: d.Exchange, ListedAt: listed, DelistedAt: at}, "fmp")
			if err != nil {
				return err
			}
		}
		if err := s.Ref.MarkDelisted(ctx, id, at); err != nil {
			return err
		}
	}
	return nil
}

// Key identifies one action across sources.
type Key struct {
	InstrumentID string
	Type         string
	ExDate       int64
}

// CrossCheck sets the agreement status of every action (MD-012): both sources report the same
// type, ex-date, and ratio/amount → agreed; one source only → single_source; same instrument and
// type with a different value, or the same value within five days but on different ex-dates →
// disagreed (adjustment blocked).
func CrossCheck(actions []refdata.CorporateAction) map[string]string {
	status := map[string]string{} // source|source_id -> status
	byKey := map[Key][]refdata.CorporateAction{}
	for _, a := range actions {
		byKey[Key{a.InstrumentID, a.Type, a.ExDate}] = append(byKey[Key{a.InstrumentID, a.Type, a.ExDate}], a)
	}
	val := func(a refdata.CorporateAction) decimal.Decimal {
		v := a.Ratio
		if a.Type == "dividend" {
			v = a.Amount
		}
		d, _ := decimal.NewFromString(v)
		return d
	}
	for _, group := range byKey {
		sources := map[string]bool{}
		for _, a := range group {
			sources[a.Source] = true
		}
		st := "single_source"
		if len(sources) > 1 {
			st = "agreed"
			for _, a := range group[1:] {
				if val(a).Sub(val(group[0])).Abs().GreaterThan(decimal.RequireFromString("0.000001")) {
					st = "disagreed"
				}
			}
		}
		for _, a := range group {
			status[a.Source+"|"+a.SourceID] = st
		}
	}
	// Near-miss ex-dates across sources (e.g. one vendor a day late).
	for k, group := range byKey {
		if len(group) != 1 || status[group[0].Source+"|"+group[0].SourceID] != "single_source" {
			continue
		}
		for k2, g2 := range byKey {
			if k2 == k || k2.InstrumentID != k.InstrumentID || k2.Type != k.Type || g2[0].Source == group[0].Source {
				continue
			}
			if d := k2.ExDate - k.ExDate; d > -5*int64(24*time.Hour) && d < 5*int64(24*time.Hour) {
				status[group[0].Source+"|"+group[0].SourceID] = "disagreed"
			}
		}
	}
	return status
}

func (s *Syncer) fmpActions(ctx context.Context, id, symbol string, now int64) ([]refdata.CorporateAction, error) {
	var out []refdata.CorporateAction
	splits, err := s.FMP.Splits(ctx, symbol)
	if err != nil {
		return nil, err
	}
	for _, sp := range splits {
		num, e1 := decimal.NewFromString(sp.Numerator.String())
		den, e2 := decimal.NewFromString(sp.Denominator.String())
		if e1 != nil || e2 != nil || den.IsZero() {
			continue
		}
		ex := dateNS(sp.Date)
		out = append(out, refdata.CorporateAction{Source: "fmp", SourceID: "split:" + symbol + ":" + sp.Date, InstrumentID: id, Symbol: symbol, Type: "split",
			ExDate: ex, Ratio: num.DivRound(den, 10).String(), AvailableAt: refdata.AvailableAt(0, ex, now), IngestedAt: now})
	}
	divs, err := s.FMP.Dividends(ctx, symbol)
	if err != nil {
		return nil, err
	}
	for _, d := range divs {
		ex := dateNS(d.Date)
		amt := d.Dividend.String()
		if amt == "" {
			amt = d.AdjDividend.String()
		}
		out = append(out, refdata.CorporateAction{Source: "fmp", SourceID: "div:" + symbol + ":" + d.Date, InstrumentID: id, Symbol: symbol, Type: "dividend",
			ExDate: ex, Amount: amt, RecordDate: dateNS(d.RecordDate), PayableDate: dateNS(d.PaymentDate), DeclaredAt: dateNS(d.DeclarationDate),
			AvailableAt: refdata.AvailableAt(dateNS(d.DeclarationDate), ex, now), IngestedAt: now})
	}
	return out, nil
}

var caType = map[mdv1.CorporateActionType]string{
	mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPLIT: "split", mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DIVIDEND: "dividend",
	mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SYMBOL_CHANGE: "symbol_change", mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_DELISTING: "delisting",
	mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_MERGER: "merger", mdv1.CorporateActionType_CORPORATE_ACTION_TYPE_SPIN_OFF: "spin_off",
}

// CorporateActions pulls actions for the instruments from Alpaca and FMP over [from, to],
// cross-checks them, stores them, publishes new/changed ones, and alerts on disagreements.
func (s *Syncer) CorporateActions(ctx context.Context, instruments []string, from, to time.Time) (stored, disagreed int, err error) {
	now := s.Now().UnixNano()
	symOf := map[string]string{}
	var syms []string
	for _, id := range instruments {
		h := s.Ref.History(ctx, id)
		if len(h) == 0 {
			continue
		}
		sym := h[len(h)-1].Symbol
		symOf[sym] = id
		syms = append(syms, sym)
	}
	var all []refdata.CorporateAction
	for i := 0; i < len(syms); i += 100 {
		resp, err := s.Broker.GetCorporateActions(ctx, &brokerv1.GetCorporateActionsRequest{Symbols: syms[i:min(i+100, len(syms))], Start: from.UnixNano(), End: to.UnixNano()})
		if err != nil {
			return 0, 0, fmt.Errorf("alpaca corporate actions: %w", err)
		}
		for _, a := range resp.GetActions() {
			id := symOf[a.GetSymbol()]
			if id == "" {
				continue
			}
			all = append(all, refdata.CorporateAction{Source: "alpaca", SourceID: a.GetSourceId(), InstrumentID: id, Symbol: a.GetSymbol(),
				Type: caType[a.GetType()], ExDate: a.GetExDate(), Ratio: a.GetRatio().GetValue(), Amount: a.GetAmount().GetValue(), NewSymbol: a.GetNewSymbol(),
				RecordDate: a.GetRecordDate(), PayableDate: a.GetPayableDate(), DeclaredAt: a.GetDeclaredAt(),
				AvailableAt: refdata.AvailableAt(a.GetDeclaredAt(), a.GetExDate(), now), IngestedAt: now})
		}
	}
	if s.FMP != nil {
		for sym, id := range symOf {
			acts, err := s.fmpActions(ctx, id, sym, now)
			if errors.Is(err, fmp.ErrDisabled) {
				break
			}
			if err != nil {
				return 0, 0, fmt.Errorf("fmp corporate actions %s: %w", sym, err)
			}
			for _, a := range acts {
				if a.ExDate >= from.UnixNano() && a.ExDate <= to.UnixNano() {
					all = append(all, a)
				}
			}
		}
	}
	for _, a := range all {
		isNew, err := s.Ref.UpsertAction(ctx, a)
		if err != nil {
			return stored, disagreed, err
		}
		if isNew {
			stored++
		}
	}
	// Cross-check against everything stored for these instruments (earlier fetches included).
	var known []refdata.CorporateAction
	for _, id := range instruments {
		for _, a := range s.Ref.ActionsFor(ctx, id) {
			if a.Agreement != refdata.AgreementRejected {
				known = append(known, a)
			}
		}
	}
	for k, st := range CrossCheck(known) {
		src, sid, _ := strings.Cut(k, "|")
		if err := s.Ref.SetAgreement(ctx, src, sid, st); err != nil {
			return stored, disagreed, err
		}
		if st == "disagreed" {
			disagreed++
		}
	}
	for _, id := range instruments {
		for _, a := range s.Ref.ActionsFor(ctx, id) {
			if a.IngestedAt != now {
				continue
			}
			if a.Agreement == "disagreed" && s.Alert != nil {
				s.Alert(ctx, "md.corporate_action.disagreement", fmt.Sprintf("%s %s on %s: sources disagree; adjustment blocked (MD-012)", a.Symbol, a.Type, time.Unix(0, a.ExDate).In(ny).Format("2006-01-02")))
			}
			if s.Publish != nil {
				_ = s.Publish(ctx, toProto(a))
			}
		}
	}
	return stored, disagreed, nil
}

func toProto(a refdata.CorporateAction) *mdv1.CorporateAction {
	var t mdv1.CorporateActionType
	for k, v := range caType {
		if v == a.Type {
			t = k
		}
	}
	ag := map[string]mdv1.AgreementStatus{"single_source": mdv1.AgreementStatus_AGREEMENT_STATUS_SINGLE_SOURCE, "agreed": mdv1.AgreementStatus_AGREEMENT_STATUS_AGREED,
		"disagreed": mdv1.AgreementStatus_AGREEMENT_STATUS_DISAGREED}[a.Agreement]
	d := func(s string) *commonv1.Decimal {
		if s == "" {
			return nil
		}
		return &commonv1.Decimal{Value: s}
	}
	return &mdv1.CorporateAction{InstrumentId: a.InstrumentID, Type: t, ExDate: a.ExDate, Ratio: d(a.Ratio), Amount: d(a.Amount), Source: a.Source,
		AvailableAt: a.AvailableAt, IngestedAt: a.IngestedAt, Symbol: a.Symbol, NewSymbol: a.NewSymbol, RecordDate: a.RecordDate, PayableDate: a.PayableDate,
		DeclaredAt: a.DeclaredAt, SourceId: a.SourceID, Agreement: ag, Sources: []string{a.Source}}
}
