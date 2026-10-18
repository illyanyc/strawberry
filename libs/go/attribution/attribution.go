// Package attribution parses and validates order attribution IDs (rules BR-003, OE-002, VP-001,
// VP-002): tb1-<strategyShortId>-<intentULID>-<attempt>.
package attribution

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// Prefix marks platform orders; anything else belongs to the manual owner (VP-001).
const Prefix = "tb1-"

// ManualShortID is the reserved strategy short ID for owner orders entered in the platform UI.
const ManualShortID = "manual"

// Strategy short IDs are 6–8 lowercase alphanumerics; the intent ID is a Crockford-base32 ULID;
// the attempt counter has 1–4 digits.
var re = regexp.MustCompile(`^tb1-([a-z0-9]{6,8})-([0-9A-HJKMNP-TV-Z]{26})-([0-9]{1,4})$`)

// ID is a parsed attribution ID.
type ID struct {
	StrategyShortID string
	IntentID        string
	Attempt         int
}

// String formats the ID.
func (id ID) String() string {
	return fmt.Sprintf("%s%s-%s-%d", Prefix, id.StrategyShortID, id.IntentID, id.Attempt)
}

// Manual reports whether the ID is a platform-entered manual order.
func (id ID) Manual() bool { return id.StrategyShortID == ManualShortID }

// Next returns the ID for the replacement order (attempt + 1).
func (id ID) Next() ID { id.Attempt++; return id }

// Parse validates s (BR-003). maxLen is the broker's field limit (0 = no extra limit).
func Parse(s string, maxLen int) (ID, error) {
	if s == "" {
		return ID{}, fmt.Errorf("attribution: client_order_id is required")
	}
	if maxLen > 0 && len(s) > maxLen {
		return ID{}, fmt.Errorf("attribution: %d characters exceeds the broker limit of %d", len(s), maxLen)
	}
	m := re.FindStringSubmatch(s)
	if m == nil {
		return ID{}, fmt.Errorf("attribution: %q is not tb1-<strategyShortId>-<intentULID>-<attempt>", s)
	}
	n, _ := strconv.Atoi(m[3])
	return ID{StrategyShortID: m[1], IntentID: m[2], Attempt: n}, nil
}

// IsPlatform reports whether a broker-side client order ID carries the platform prefix. Orders
// without it (placed in the broker's app, TradingView panel) are manual (VP-001, BR-019).
func IsPlatform(clientOrderID string) bool { return strings.HasPrefix(clientOrderID, Prefix) }

// ValidReplacement checks that next replaces cur: same strategy and intent, higher attempt.
func ValidReplacement(cur, next ID) error {
	if cur.StrategyShortID != next.StrategyShortID || cur.IntentID != next.IntentID {
		return fmt.Errorf("attribution: replacement must keep strategy and intent")
	}
	if next.Attempt <= cur.Attempt {
		return fmt.Errorf("attribution: replacement attempt %d must exceed %d", next.Attempt, cur.Attempt)
	}
	return nil
}
