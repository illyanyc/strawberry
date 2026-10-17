// Package audit writes and verifies the append-only, hash-chained audit log (rules SE-020..SE-024).
//
// The chain is computed inside Postgres by the SECURITY DEFINER function audit.append (see
// libs/go/db/migrations/audit), so writers only hold EXECUTE on that function and can never
// update or delete rows. This package mirrors the canonical encoding so Verify can recompute
// every hash independently:
//
//	hash_n = SHA-256(hash_{n-1} || field("tb.audit.v1") || field(event_id) || field(occurred_at) ||
//	                 field(actor) || field(action) || field(target) || field(before_hash) ||
//	                 field(after_hash) || field(ip) || field(device) || field(correlation_id) ||
//	                 field(details_json))
//	field(v) = "<len(v) in bytes>:" || v || ";"
//
// hash_0 is 32 zero bytes. occurred_at is the decimal string of UTC nanoseconds.
package audit

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/oklog/ulid/v2"

	"github.com/illyanyc/strawberry/libs/go/logging"
)

// Domain is the canonical-encoding version tag.
const Domain = "tb.audit.v1"

// Event is one audit record before it is chained.
type Event struct {
	ID            string
	OccurredAt    time.Time
	Actor         string
	Action        string
	Target        string
	BeforeHash    string
	AfterHash     string
	IP            string
	Device        string
	CorrelationID string
	// Details holds non-secret context. It is canonicalized (sorted keys) and redacted before storage.
	Details map[string]any
}

// Record is a stored, chained event.
type Record struct {
	Seq           int64
	ID            string
	OccurredAt    int64
	Actor         string
	Action        string
	Target        string
	BeforeHash    string
	AfterHash     string
	IP            string
	Device        string
	CorrelationID string
	DetailsJSON   string
	PrevHash      []byte
	Hash          []byte
}

// Writer appends events through audit.append.
type Writer struct {
	pool *pgxpool.Pool
}

// NewWriter returns a writer using a pool whose role is a member of audit_writer.
func NewWriter(pool *pgxpool.Pool) *Writer { return &Writer{pool: pool} }

// Append chains and stores e, returning its sequence number and hex hash. Callers performing an
// action that must be audited MUST fail the action when Append fails (SE-021).
func (w *Writer) Append(ctx context.Context, e Event) (int64, string, error) {
	if e.ID == "" {
		e.ID = ulid.MustNew(ulid.Now(), rand.Reader).String()
	}
	if e.OccurredAt.IsZero() {
		e.OccurredAt = time.Now()
	}
	details, err := CanonicalDetails(e.Details)
	if err != nil {
		return 0, "", err
	}
	if e.CorrelationID == "" {
		e.CorrelationID = logging.CorrelationID(ctx)
	}
	var seq int64
	var hash string
	err = w.pool.QueryRow(ctx,
		`SELECT seq, hash FROM audit.append($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
		e.ID, e.OccurredAt.UTC().UnixNano(), e.Actor, e.Action, logging.Redact(e.Target),
		e.BeforeHash, e.AfterHash, e.IP, logging.Redact(e.Device), e.CorrelationID, details,
	).Scan(&seq, &hash)
	if err != nil {
		return 0, "", fmt.Errorf("audit: append %s: %w", e.Action, err)
	}
	return seq, hash, nil
}

// CanonicalDetails encodes details as JSON with sorted keys, redacting secret-shaped content.
func CanonicalDetails(details map[string]any) (string, error) {
	if details == nil {
		return "{}", nil
	}
	clean := make(map[string]any, len(details))
	for k, v := range details {
		if logging.IsSensitiveKey(k) {
			clean[k] = logging.Redacted
			continue
		}
		clean[k] = v
	}
	b, err := json.Marshal(clean)
	if err != nil {
		return "", fmt.Errorf("audit: details: %w", err)
	}
	return logging.Redact(string(b)), nil
}

// StateHash returns the hex SHA-256 of the canonical JSON of v, for before/after hashes of config changes.
func StateHash(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func field(v string) []byte {
	return []byte(strconv.Itoa(len(v)) + ":" + v + ";")
}

// ComputeHash returns the chained hash of r given its PrevHash.
func ComputeHash(r Record) []byte {
	h := sha256.New()
	h.Write(r.PrevHash)
	for _, f := range []string{
		Domain, r.ID, strconv.FormatInt(r.OccurredAt, 10), r.Actor, r.Action, r.Target,
		r.BeforeHash, r.AfterHash, r.IP, r.Device, r.CorrelationID, r.DetailsJSON,
	} {
		h.Write(field(f))
	}
	return h.Sum(nil)
}

// GenesisHash is the prev_hash of the first event.
var GenesisHash = make([]byte, sha256.Size)

// VerifyResult summarizes a chain verification.
type VerifyResult struct {
	Count    int64
	HeadSeq  int64
	HeadHash string
	// BrokenAt is the first sequence number that failed verification (0 when the chain is intact).
	BrokenAt int64
	Reason   string
}

// ErrChainBroken is returned when verification fails.
var ErrChainBroken = errors.New("audit: hash chain broken")

// Verify recomputes the whole chain in sequence order (SE-022). It needs SELECT on audit.events.
func Verify(ctx context.Context, conn interface {
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}) (VerifyResult, error) {
	rows, err := conn.Query(ctx, `SELECT seq, event_id, occurred_at, actor, action, target, before_hash, after_hash,
		ip, device, correlation_id, details_json, prev_hash, hash FROM audit.events ORDER BY seq`)
	if err != nil {
		return VerifyResult{}, fmt.Errorf("audit: query: %w", err)
	}
	defer rows.Close()
	res := VerifyResult{}
	prev := GenesisHash
	for rows.Next() {
		var r Record
		if err := rows.Scan(&r.Seq, &r.ID, &r.OccurredAt, &r.Actor, &r.Action, &r.Target, &r.BeforeHash,
			&r.AfterHash, &r.IP, &r.Device, &r.CorrelationID, &r.DetailsJSON, &r.PrevHash, &r.Hash); err != nil {
			return res, fmt.Errorf("audit: scan: %w", err)
		}
		want := res.Count + 1
		switch {
		case r.Seq != want:
			return fail(res, r.Seq, fmt.Sprintf("sequence gap: expected %d got %d", want, r.Seq))
		case !equal(r.PrevHash, prev):
			return fail(res, r.Seq, "prev_hash does not match previous event hash")
		case !equal(ComputeHash(r), r.Hash):
			return fail(res, r.Seq, "hash does not match recomputed canonical event")
		}
		res.Count++
		res.HeadSeq = r.Seq
		res.HeadHash = hex.EncodeToString(r.Hash)
		prev = r.Hash
	}
	if err := rows.Err(); err != nil {
		return res, fmt.Errorf("audit: rows: %w", err)
	}
	return res, nil
}

func fail(res VerifyResult, seq int64, reason string) (VerifyResult, error) {
	res.BrokenAt = seq
	res.Reason = reason
	return res, fmt.Errorf("%w at seq %d: %s", ErrChainBroken, seq, reason)
}

func equal(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	var d byte
	for i := range a {
		d |= a[i] ^ b[i]
	}
	return d == 0
}
