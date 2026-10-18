package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/go-webauthn/webauthn/webauthn"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/audit"
)

// Store is the gateway's persistence layer over schemas auth and config (role svc_api_gateway).
type Store struct {
	pool *pgxpool.Pool
}

var errNotFound = errors.New("not found")

// Owner is the platform owner as a WebAuthn user.
type Owner struct {
	ID          string
	DisplayName string
	UserHandle  []byte
	Credentials []StoredCredential
}

// StoredCredential is a passkey with its display metadata.
type StoredCredential struct {
	Credential webauthn.Credential
	Name       string
	CreatedAt  time.Time
	LastUsedAt *time.Time
}

// WebAuthnID implements webauthn.User.
func (o *Owner) WebAuthnID() []byte { return o.UserHandle }

// WebAuthnName implements webauthn.User.
func (o *Owner) WebAuthnName() string { return o.ID }

// WebAuthnDisplayName implements webauthn.User.
func (o *Owner) WebAuthnDisplayName() string { return o.DisplayName }

// WebAuthnCredentials implements webauthn.User.
func (o *Owner) WebAuthnCredentials() []webauthn.Credential {
	out := make([]webauthn.Credential, len(o.Credentials))
	for i, c := range o.Credentials {
		out[i] = c.Credential
	}
	return out
}

func (s *Store) ownerExists(ctx context.Context) (bool, error) {
	var ok bool
	err := s.pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM auth.owners)`).Scan(&ok)
	return ok, err
}

func (s *Store) owner(ctx context.Context, id string) (*Owner, error) {
	o := &Owner{}
	err := s.pool.QueryRow(ctx, `SELECT id, display_name, user_handle FROM auth.owners WHERE id=$1`, id).
		Scan(&o.ID, &o.DisplayName, &o.UserHandle)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errNotFound
	}
	if err != nil {
		return nil, err
	}
	rows, err := s.pool.Query(ctx, `SELECT credential, name, created_at, last_used_at
		FROM auth.webauthn_credentials WHERE owner_id=$1 ORDER BY created_at`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var raw []byte
		var sc StoredCredential
		if err := rows.Scan(&raw, &sc.Name, &sc.CreatedAt, &sc.LastUsedAt); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(raw, &sc.Credential); err != nil {
			return nil, fmt.Errorf("decode credential: %w", err)
		}
		o.Credentials = append(o.Credentials, sc)
	}
	return o, rows.Err()
}

func (s *Store) ownerByHandle(ctx context.Context, handle []byte) (*Owner, error) {
	var id string
	err := s.pool.QueryRow(ctx, `SELECT id FROM auth.owners WHERE user_handle=$1`, handle).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errNotFound
	}
	if err != nil {
		return nil, err
	}
	return s.owner(ctx, id)
}

// createOwner inserts the first owner, its first passkey, and its recovery codes atomically. It
// fails if an owner already exists (registration closes after setup, SE-007).
func (s *Store) createOwner(ctx context.Context, o *Owner, cred *webauthn.Credential, credName string, codeHashes [][]byte) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `LOCK TABLE auth.owners IN EXCLUSIVE MODE`); err != nil {
			return err
		}
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM auth.owners)`).Scan(&exists); err != nil {
			return err
		}
		if exists {
			return errors.New("owner already exists")
		}
		if _, err := tx.Exec(ctx, `INSERT INTO auth.owners (id, display_name, user_handle) VALUES ($1,$2,$3)`,
			o.ID, o.DisplayName, o.UserHandle); err != nil {
			return err
		}
		if err := insertCredential(ctx, tx, o.ID, cred, credName); err != nil {
			return err
		}
		return replaceRecoveryCodes(ctx, tx, o.ID, codeHashes)
	})
}

func insertCredential(ctx context.Context, tx pgx.Tx, ownerID string, cred *webauthn.Credential, name string) error {
	raw, err := json.Marshal(cred)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO auth.webauthn_credentials (id, owner_id, name, credential) VALUES ($1,$2,$3,$4)`,
		cred.ID, ownerID, name, raw)
	return err
}

func (s *Store) addCredential(ctx context.Context, ownerID string, cred *webauthn.Credential, name string) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error { return insertCredential(ctx, tx, ownerID, cred, name) })
}

// touchCredential stores the updated sign count/flags after a successful assertion.
func (s *Store) touchCredential(ctx context.Context, cred *webauthn.Credential) error {
	raw, err := json.Marshal(cred)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `UPDATE auth.webauthn_credentials SET credential=$2, last_used_at=now() WHERE id=$1`, cred.ID, raw)
	return err
}

// deleteCredential removes a passkey but never the last one.
func (s *Store) deleteCredential(ctx context.Context, ownerID string, id []byte) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var n int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM auth.webauthn_credentials WHERE owner_id=$1`, ownerID).Scan(&n); err != nil {
			return err
		}
		if n <= 1 {
			return errLastPasskey
		}
		tag, err := tx.Exec(ctx, `DELETE FROM auth.webauthn_credentials WHERE owner_id=$1 AND id=$2`, ownerID, id)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return errNotFound
		}
		return nil
	})
}

var errLastPasskey = errors.New("cannot delete the last passkey")

// ---- ceremonies (single-use WebAuthn challenges) ----

func (s *Store) saveCeremony(ctx context.Context, id, kind, ownerID string, sessionHash []byte, data *webauthn.SessionData, ttl time.Duration) error {
	raw, err := json.Marshal(data)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `INSERT INTO auth.ceremonies (id, kind, owner_id, session_id_hash, data, expires_at)
		VALUES ($1,$2,NULLIF($3,''),$4,$5, now() + $6::interval)`, id, kind, ownerID, sessionHash, raw, ttl.String())
	return err
}

// takeCeremony deletes and returns an unexpired ceremony of the given kind (single use).
func (s *Store) takeCeremony(ctx context.Context, id, kind string, sessionHash []byte) (*webauthn.SessionData, error) {
	var raw []byte
	err := s.pool.QueryRow(ctx, `DELETE FROM auth.ceremonies WHERE id=$1 AND kind=$2 AND expires_at > now()
		AND session_id_hash IS NOT DISTINCT FROM $3 RETURNING data`, id, kind, sessionHash).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errNotFound
	}
	if err != nil {
		return nil, err
	}
	var d webauthn.SessionData
	return &d, json.Unmarshal(raw, &d)
}

// ---- setup tokens (SE-007) ----

func (s *Store) insertSetupToken(ctx context.Context, hash []byte, ttl time.Duration) error {
	_, err := s.pool.Exec(ctx, `INSERT INTO auth.setup_tokens (token_hash, expires_at) VALUES ($1, now() + $2::interval)`, hash, ttl.String())
	return err
}

func (s *Store) setupTokenValid(ctx context.Context, hash []byte) (bool, error) {
	var ok bool
	err := s.pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM auth.setup_tokens WHERE token_hash=$1 AND used_at IS NULL AND expires_at > now())`, hash).Scan(&ok)
	return ok, err
}

func (s *Store) consumeSetupToken(ctx context.Context, hash []byte) (bool, error) {
	tag, err := s.pool.Exec(ctx, `UPDATE auth.setup_tokens SET used_at=now() WHERE token_hash=$1 AND used_at IS NULL AND expires_at > now()`, hash)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}

// ---- sessions (SE-005) ----

// Session is a server-side session row.
type Session struct {
	IDHash       []byte
	PublicID     string
	OwnerID      string
	AuthMethod   string
	CreatedAt    time.Time
	LastSeenAt   time.Time
	IdleExpires  time.Time
	AbsExpires   time.Time
	StepUpMethod string
	StepUpUntil  *time.Time
	IP           string
	UserAgent    string
}

func (s *Store) createSession(ctx context.Context, sess *Session) error {
	_, err := s.pool.Exec(ctx, `INSERT INTO auth.sessions (id_hash, public_id, owner_id, auth_method, idle_expires_at,
		absolute_expires_at, step_up_method, step_up_until, ip, user_agent)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		sess.IDHash, sess.PublicID, sess.OwnerID, sess.AuthMethod, sess.IdleExpires, sess.AbsExpires,
		sess.StepUpMethod, sess.StepUpUntil, sess.IP, sess.UserAgent)
	return err
}

// activeSession returns the session if it is unrevoked and within idle and absolute timeouts, and
// slides the idle expiry.
func (s *Store) activeSession(ctx context.Context, idHash []byte, idle time.Duration) (*Session, error) {
	sess := &Session{IDHash: idHash}
	err := s.pool.QueryRow(ctx, `UPDATE auth.sessions
		SET last_seen_at = now(), idle_expires_at = LEAST(now() + $2::interval, absolute_expires_at)
		WHERE id_hash=$1 AND revoked_at IS NULL AND idle_expires_at > now() AND absolute_expires_at > now()
		RETURNING public_id, owner_id, auth_method, created_at, last_seen_at, idle_expires_at, absolute_expires_at,
		          step_up_method, step_up_until, ip, user_agent`, idHash, idle.String()).
		Scan(&sess.PublicID, &sess.OwnerID, &sess.AuthMethod, &sess.CreatedAt, &sess.LastSeenAt, &sess.IdleExpires,
			&sess.AbsExpires, &sess.StepUpMethod, &sess.StepUpUntil, &sess.IP, &sess.UserAgent)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errNotFound
	}
	return sess, err
}

// peekSession returns the session if still valid, without touching last_seen or idle expiry.
func (s *Store) peekSession(ctx context.Context, idHash []byte) (*Session, error) {
	sess := &Session{IDHash: idHash}
	err := s.pool.QueryRow(ctx, `SELECT public_id, owner_id, auth_method, idle_expires_at, absolute_expires_at
		FROM auth.sessions WHERE id_hash=$1 AND revoked_at IS NULL AND idle_expires_at > now() AND absolute_expires_at > now()`, idHash).
		Scan(&sess.PublicID, &sess.OwnerID, &sess.AuthMethod, &sess.IdleExpires, &sess.AbsExpires)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errNotFound
	}
	return sess, err
}

func (s *Store) setStepUp(ctx context.Context, idHash []byte, method string, until time.Time) error {
	_, err := s.pool.Exec(ctx, `UPDATE auth.sessions SET step_up_method=$2, step_up_until=$3 WHERE id_hash=$1`, idHash, method, until)
	return err
}

func (s *Store) revokeSession(ctx context.Context, ownerID, publicID string) (bool, error) {
	tag, err := s.pool.Exec(ctx, `UPDATE auth.sessions SET revoked_at=now() WHERE owner_id=$1 AND public_id=$2 AND revoked_at IS NULL`, ownerID, publicID)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}

func (s *Store) revokeSessionByHash(ctx context.Context, idHash []byte) error {
	_, err := s.pool.Exec(ctx, `UPDATE auth.sessions SET revoked_at=now() WHERE id_hash=$1 AND revoked_at IS NULL`, idHash)
	return err
}

func (s *Store) listSessions(ctx context.Context, ownerID string) ([]Session, error) {
	rows, err := s.pool.Query(ctx, `SELECT public_id, auth_method, created_at, last_seen_at, idle_expires_at,
		absolute_expires_at, ip, user_agent FROM auth.sessions
		WHERE owner_id=$1 AND revoked_at IS NULL AND idle_expires_at > now() AND absolute_expires_at > now()
		ORDER BY last_seen_at DESC`, ownerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Session
	for rows.Next() {
		var x Session
		if err := rows.Scan(&x.PublicID, &x.AuthMethod, &x.CreatedAt, &x.LastSeenAt, &x.IdleExpires, &x.AbsExpires, &x.IP, &x.UserAgent); err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

// ---- TOTP (SE-008) ----

type totpRow struct {
	SecretEnc   []byte
	Confirmed   bool
	LastCounter int64
}

func (s *Store) totp(ctx context.Context, ownerID string) (*totpRow, error) {
	t := &totpRow{}
	err := s.pool.QueryRow(ctx, `SELECT secret_enc, confirmed, last_counter FROM auth.totp WHERE owner_id=$1`, ownerID).
		Scan(&t.SecretEnc, &t.Confirmed, &t.LastCounter)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, errNotFound
	}
	return t, err
}

// putPendingTOTP stores a new unconfirmed secret, replacing any previous one.
func (s *Store) putPendingTOTP(ctx context.Context, ownerID string, enc []byte) error {
	_, err := s.pool.Exec(ctx, `INSERT INTO auth.totp (owner_id, secret_enc) VALUES ($1,$2)
		ON CONFLICT (owner_id) DO UPDATE SET secret_enc=EXCLUDED.secret_enc, confirmed=false, last_counter=0,
		created_at=now(), confirmed_at=NULL`, ownerID, enc)
	return err
}

// advanceTOTP records an accepted counter; it fails (false) if the counter was already used (replay).
func (s *Store) advanceTOTP(ctx context.Context, ownerID string, counter int64, confirm bool) (bool, error) {
	tag, err := s.pool.Exec(ctx, `UPDATE auth.totp SET last_counter=$2,
		confirmed = confirmed OR $3, confirmed_at = CASE WHEN $3 AND NOT confirmed THEN now() ELSE confirmed_at END
		WHERE owner_id=$1 AND last_counter < $2`, ownerID, counter, confirm)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}

// ---- recovery codes (SE-009) ----

func replaceRecoveryCodes(ctx context.Context, tx pgx.Tx, ownerID string, hashes [][]byte) error {
	if _, err := tx.Exec(ctx, `DELETE FROM auth.recovery_codes WHERE owner_id=$1`, ownerID); err != nil {
		return err
	}
	for _, h := range hashes {
		if _, err := tx.Exec(ctx, `INSERT INTO auth.recovery_codes (owner_id, code_hash) VALUES ($1,$2)`, ownerID, h); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) replaceRecoveryCodes(ctx context.Context, ownerID string, hashes [][]byte) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error { return replaceRecoveryCodes(ctx, tx, ownerID, hashes) })
}

func (s *Store) consumeRecoveryCode(ctx context.Context, ownerID string, hash []byte) (bool, error) {
	tag, err := s.pool.Exec(ctx, `UPDATE auth.recovery_codes SET used_at=now() WHERE owner_id=$1 AND code_hash=$2 AND used_at IS NULL`, ownerID, hash)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}

func (s *Store) recoveryCodesLeft(ctx context.Context, ownerID string) (int, error) {
	var n int
	err := s.pool.QueryRow(ctx, `SELECT count(*) FROM auth.recovery_codes WHERE owner_id=$1 AND used_at IS NULL`, ownerID).Scan(&n)
	return n, err
}

// ---- throttling (SE-011) ----

// lockedUntil returns the latest lock expiry among keys, or zero.
func (s *Store) lockedUntil(ctx context.Context, keys ...string) (time.Time, error) {
	var t *time.Time
	err := s.pool.QueryRow(ctx, `SELECT max(locked_until) FROM auth.throttle WHERE key = ANY($1) AND locked_until > now()`, keys).Scan(&t)
	if err != nil || t == nil {
		return time.Time{}, err
	}
	return *t, nil
}

// recordFailure counts a failure for key within window and locks the key when it reaches max.
// It returns true when this failure triggered a lock.
func (s *Store) recordFailure(ctx context.Context, key string, max int, window time.Duration) (bool, error) {
	var locked bool
	err := s.pool.QueryRow(ctx, `INSERT INTO auth.throttle (key, failures, window_start) VALUES ($1, 1, now())
		ON CONFLICT (key) DO UPDATE SET
		  failures = CASE WHEN auth.throttle.window_start < now() - $3::interval THEN 1 ELSE auth.throttle.failures + 1 END,
		  window_start = CASE WHEN auth.throttle.window_start < now() - $3::interval THEN now() ELSE auth.throttle.window_start END
		RETURNING failures >= $2`, key, max, window.String()).Scan(&locked)
	if err != nil || !locked {
		return false, err
	}
	_, err = s.pool.Exec(ctx, `UPDATE auth.throttle SET locked_until = now() + $2::interval, failures = 0, window_start = now() WHERE key=$1`, key, window.String())
	return true, err
}

func (s *Store) clearFailures(ctx context.Context, key string) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM auth.throttle WHERE key=$1 AND (locked_until IS NULL OR locked_until <= now())`, key)
	return err
}

// ---- settings (SE-025) ----

type settingRow struct {
	Key       string          `json:"key"`
	Value     json.RawMessage `json:"value"`
	Version   int             `json:"version"`
	UpdatedAt time.Time       `json:"updated_at"`
	UpdatedBy string          `json:"updated_by"`
}

func (s *Store) settings(ctx context.Context, ownerID string) (map[string]settingRow, error) {
	rows, err := s.pool.Query(ctx, `SELECT key, value, version, updated_at, updated_by FROM config.settings WHERE owner_id=$1`, ownerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]settingRow{}
	for rows.Next() {
		var r settingRow
		if err := rows.Scan(&r.Key, &r.Value, &r.Version, &r.UpdatedAt, &r.UpdatedBy); err != nil {
			return nil, err
		}
		out[r.Key] = r
	}
	return out, rows.Err()
}

// putSetting writes a value with optimistic concurrency (expectedVersion 0 = must not exist) and
// appends ev to the audit log in the same transaction (SE-021, SE-025).
func (s *Store) putSetting(ctx context.Context, ownerID, key string, value json.RawMessage, expectedVersion int, actor string, ev audit.Event) (int, error) {
	var v int
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var err error
		v, err = putSettingTx(ctx, tx, ownerID, key, value, expectedVersion, actor)
		if err != nil {
			return err
		}
		_, _, err = audit.AppendWith(ctx, tx, ev)
		return err
	})
	return v, err
}

func putSettingTx(ctx context.Context, tx pgx.Tx, ownerID, key string, value json.RawMessage, expectedVersion int, actor string) (int, error) {
	var v int
	var err error
	if expectedVersion == 0 {
		err = tx.QueryRow(ctx, `INSERT INTO config.settings (owner_id, key, value, updated_by) VALUES ($1,$2,$3,$4)
			ON CONFLICT DO NOTHING RETURNING version`, ownerID, key, value, actor).Scan(&v)
	} else {
		err = tx.QueryRow(ctx, `UPDATE config.settings SET value=$3, version=version+1, updated_at=now(), updated_by=$5
			WHERE owner_id=$1 AND key=$2 AND version=$4 RETURNING version`, ownerID, key, value, expectedVersion, actor).Scan(&v)
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, errConflict
	}
	return v, err
}

var errConflict = errors.New("version conflict")

// ---- publications (UI-053) ----

func (s *Store) insertPublication(ctx context.Context, p publication) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		var n int
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM config.publications WHERE owner_id=$1 AND withdrawn_at IS NULL`, p.OwnerID).Scan(&n); err != nil {
			return err
		}
		if n >= publicationCap {
			return errPublicationLimit
		}
		_, err := tx.Exec(ctx, `INSERT INTO config.publications
			(id, owner_id, display_name, title, definition, window_start, window_end, bar_size, universe, published_at)
			VALUES ($1,$2,$3,$4,$5,$6::date,$7::date,$8,$9,$10)`,
			p.ID, p.OwnerID, p.DisplayName, p.Title, p.Definition, p.WindowStart, p.WindowEnd, p.BarSize, p.Universe, p.PublishedAt)
		return err
	})
}

func (s *Store) listPublications(ctx context.Context, sortBy string) ([]publication, error) {
	// UI-053: only these two orders exist. The query text is fixed; sortBy never enters SQL.
	q := `SELECT id, owner_id, display_name, title, definition, window_start, window_end, bar_size, universe, published_at
		FROM config.publications WHERE withdrawn_at IS NULL ORDER BY published_at DESC, id DESC`
	if sortBy == "name" {
		q = `SELECT id, owner_id, display_name, title, definition, window_start, window_end, bar_size, universe, published_at
		FROM config.publications WHERE withdrawn_at IS NULL ORDER BY lower(title) ASC, id ASC`
	}
	rows, err := s.pool.Query(ctx, q)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []publication
	for rows.Next() {
		p, err := scanPublication(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

func (s *Store) publicationByID(ctx context.Context, id string) (publication, error) {
	rows, err := s.pool.Query(ctx, `SELECT id, owner_id, display_name, title, definition, window_start, window_end, bar_size, universe, published_at
		FROM config.publications WHERE id=$1 AND withdrawn_at IS NULL`, id)
	if err != nil {
		return publication{}, err
	}
	defer rows.Close()
	if !rows.Next() {
		if err := rows.Err(); err != nil {
			return publication{}, err
		}
		return publication{}, errNotFound
	}
	return scanPublication(rows)
}

func (s *Store) withdrawPublication(ctx context.Context, id, ownerID string) error {
	tag, err := s.pool.Exec(ctx, `UPDATE config.publications SET withdrawn_at=now() WHERE id=$1 AND owner_id=$2 AND withdrawn_at IS NULL`, id, ownerID)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return errNotFound
	}
	return nil
}

type publicationScanner interface {
	Scan(dest ...any) error
}

func (s *Store) putAPIKey(ctx context.Context, rec apiKeyRecord) error {
	_, err := s.pool.Exec(ctx, `INSERT INTO config.api_keys
		(owner_id, kind, environment, key_hint, ciphertext, created_at, rotated_at)
		VALUES ($1,$2,$3,$4,$5,$6,$7)
		ON CONFLICT (owner_id, kind, environment)
		DO UPDATE SET key_hint=$4, ciphertext=$5, rotated_at=$7`,
		rec.OwnerID, rec.Kind, rec.Environment, rec.KeyHint, rec.Ciphertext, rec.CreatedAt, rec.RotatedAt)
	return err
}

func (s *Store) listAPIKeys(ctx context.Context, ownerID string) ([]apiKeyRecord, error) {
	rows, err := s.pool.Query(ctx, `SELECT owner_id, kind, environment, key_hint, ciphertext, created_at, rotated_at
		FROM config.api_keys WHERE owner_id=$1 ORDER BY kind`, ownerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]apiKeyRecord, 0)
	for rows.Next() {
		var rec apiKeyRecord
		if err := rows.Scan(&rec.OwnerID, &rec.Kind, &rec.Environment, &rec.KeyHint, &rec.Ciphertext, &rec.CreatedAt, &rec.RotatedAt); err != nil {
			return nil, err
		}
		out = append(out, rec)
	}
	return out, rows.Err()
}

func (s *Store) deleteAPIKey(ctx context.Context, ownerID, kind, environment string) error {
	tag, err := s.pool.Exec(ctx, `DELETE FROM config.api_keys WHERE owner_id=$1 AND kind=$2 AND environment=$3`, ownerID, kind, environment)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return errKeyGone
	}
	return nil
}

func (s *Store) putDisclosure(ctx context.Context, rec disclosureRecord) error {
	_, err := s.pool.Exec(ctx, `INSERT INTO config.risk_disclosures (owner_id, accepted_at) VALUES ($1,$2)
		ON CONFLICT (owner_id) DO UPDATE SET accepted_at=$2`, rec.OwnerID, rec.AcceptedAt)
	return err
}

func (s *Store) getDisclosure(ctx context.Context, ownerID string) (disclosureRecord, error) {
	var rec disclosureRecord
	err := s.pool.QueryRow(ctx, `SELECT owner_id, accepted_at FROM config.risk_disclosures WHERE owner_id=$1`, ownerID).Scan(&rec.OwnerID, &rec.AcceptedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return disclosureRecord{}, errNotFound
	}
	return rec, err
}

func scanPublication(row publicationScanner) (publication, error) {
	var p publication
	var start, end time.Time
	if err := row.Scan(&p.ID, &p.OwnerID, &p.DisplayName, &p.Title, &p.Definition, &start, &end, &p.BarSize, &p.Universe, &p.PublishedAt); err != nil {
		return publication{}, err
	}
	p.WindowStart = start.UTC().Format(time.DateOnly)
	p.WindowEnd = end.UTC().Format(time.DateOnly)
	return p, nil
}
