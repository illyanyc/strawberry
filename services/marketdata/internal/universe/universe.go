// Package universe loads the configured universes and maps them onto the instrument master
// (U3: configuration, not code; DF-006 point-in-time membership).
package universe

import (
	"bufio"
	"context"
	_ "embed"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/illyanyc/strawberry/services/marketdata/internal/refdata"
)

//go:embed dev.txt
var devList string

// Load returns the tickers of a universe file (or the embedded development list when path is "").
func Load(path string) ([]string, error) {
	text := devList
	if path != "" {
		b, err := os.ReadFile(path) //nolint:gosec // operator-provided config path
		if err != nil {
			return nil, err
		}
		text = string(b)
	}
	var out []string
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		l := strings.TrimSpace(sc.Text())
		if l == "" || strings.HasPrefix(l, "#") {
			continue
		}
		out = append(out, strings.ToUpper(l))
	}
	return out, sc.Err()
}

// Store is the reference store subset used here.
type Store interface {
	Resolve(ctx context.Context, symbol string, at int64) (string, error)
	SetUniverse(ctx context.Context, universe string, members []string, at int64) error
	UniverseAt(ctx context.Context, universe string, at int64) []string
	History(ctx context.Context, instrumentID string) []refdata.SymbolSpan
}

// Universes implements api.Universe over the reference store.
type Universes struct {
	Ref Store
}

// symbolAt returns the instrument's ticker at time at.
func (u Universes) symbolAt(ctx context.Context, id string, at int64) string {
	for _, s := range u.Ref.History(ctx, id) {
		if s.ValidFrom <= at && (s.ValidTo == 0 || at < s.ValidTo) {
			return s.Symbol
		}
	}
	return ""
}

// Members returns symbol → instrument_id for members at time at.
func (u Universes) Members(ctx context.Context, name string, at time.Time) (map[string]string, error) {
	ids := u.Ref.UniverseAt(ctx, name, at.UnixNano())
	if len(ids) == 0 {
		return nil, fmt.Errorf("universe %q has no members at %s", name, at.UTC().Format(time.RFC3339))
	}
	out := map[string]string{}
	for _, id := range ids {
		if s := u.symbolAt(ctx, id, at.UnixNano()); s != "" {
			out[s] = id
		}
	}
	return out, nil
}

// Set makes ids the universe's membership from now on and returns symbol → instrument_id.
func (u Universes) Set(ctx context.Context, name string, ids []string, at time.Time) (map[string]string, error) {
	if err := u.Ref.SetUniverse(ctx, name, ids, at.UnixNano()); err != nil {
		return nil, err
	}
	return u.Members(ctx, name, at)
}

// FromTickers resolves tickers (current) to instrument IDs, skipping unknown ones.
func (u Universes) FromTickers(ctx context.Context, tickers []string, at time.Time) (ids []string, unknown []string) {
	for _, t := range tickers {
		id, err := u.Ref.Resolve(ctx, t, at.UnixNano())
		if err != nil {
			unknown = append(unknown, t)
			continue
		}
		ids = append(ids, id)
	}
	return ids, unknown
}
