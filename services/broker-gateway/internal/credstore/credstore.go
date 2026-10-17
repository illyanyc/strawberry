// Package credstore is the broker-credential store (rules SE-030..SE-033): per-record AES-256-GCM
// data keys, wrapped by a key-encryption key (KEK) held only by broker-gateway, with AAD binding the
// ciphertext to the broker account and environment so a paper credential can never decrypt as live.
package credstore

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/secrets"
)

// Environment is "paper" or "live".
type Environment string

// Environments.
const (
	Paper Environment = "paper"
	Live  Environment = "live"
)

// Errors.
var (
	ErrNotFound = errors.New("credential not found")
	ErrDecrypt  = errors.New("credential decryption failed")
)

// KeyRing holds KEKs by ID; Active encrypts new records, all IDs can decrypt (rotation, SE-033).
type KeyRing struct {
	Active string
	keys   map[string][]byte
}

// ParseKeyRing parses lines "<id>:<base64 32-byte key>"; the first line is the active KEK.
func ParseKeyRing(raw []byte) (*KeyRing, error) {
	kr := &KeyRing{keys: map[string][]byte{}}
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		id, b64, ok := strings.Cut(line, ":")
		if !ok || id == "" {
			return nil, errors.New("credstore: KEK lines must be <id>:<base64>")
		}
		k, err := base64.StdEncoding.DecodeString(b64)
		if err != nil || len(k) != 32 {
			return nil, fmt.Errorf("credstore: KEK %q must be 32 bytes base64", id)
		}
		if _, dup := kr.keys[id]; dup {
			return nil, fmt.Errorf("credstore: duplicate KEK id %q", id)
		}
		kr.keys[id] = k
		if kr.Active == "" {
			kr.Active = id
		}
	}
	if kr.Active == "" {
		return nil, errors.New("credstore: no KEK configured")
	}
	return kr, nil
}

// Status is non-secret metadata about a stored credential.
type Status struct {
	BrokerAccountID string
	Environment     Environment
	Broker          string
	Fields          []string
	KEKID           string
	CreatedAt       time.Time
	RotatedAt       time.Time
}

// Store persists credentials in schema secrets (role svc_broker_gateway only, SE-032).
type Store struct {
	pool *pgxpool.Pool
	ring *KeyRing
}

// New returns a store.
func New(pool *pgxpool.Pool, ring *KeyRing) *Store { return &Store{pool: pool, ring: ring} }

func dataAAD(account string, env Environment) []byte {
	return []byte("tb1|" + account + "|" + string(env))
}
func dekAAD(account string, env Environment) []byte {
	return []byte("tb1-dek|" + account + "|" + string(env))
}

type sealed struct {
	ciphertext, nonce, wrappedDEK, dekNonce []byte
	kekID                                   string
}

// seal encrypts secret under a fresh data key wrapped by the active KEK (SE-030).
func (s *Store) seal(account string, env Environment, secret map[string]string) (sealed, error) {
	plain, err := json.Marshal(secret)
	if err != nil {
		return sealed{}, err
	}
	defer secrets.Zero(plain)
	dek := make([]byte, 32)
	if _, err := rand.Read(dek); err != nil {
		return sealed{}, err
	}
	defer secrets.Zero(dek)
	ct, nonce, err := gcmSeal(dek, plain, dataAAD(account, env))
	if err != nil {
		return sealed{}, err
	}
	wrapped, dn, err := gcmSeal(s.ring.keys[s.ring.Active], dek, dekAAD(account, env))
	if err != nil {
		return sealed{}, err
	}
	return sealed{ciphertext: ct, nonce: nonce, wrappedDEK: wrapped, dekNonce: dn, kekID: s.ring.Active}, nil
}

func (s *Store) open(account string, env Environment, x sealed) (map[string]string, error) {
	kek, ok := s.ring.keys[x.kekID]
	if !ok {
		return nil, ErrDecrypt
	}
	dek, err := gcmOpen(kek, x.wrappedDEK, x.dekNonce, dekAAD(account, env))
	if err != nil {
		return nil, ErrDecrypt
	}
	defer secrets.Zero(dek)
	plain, err := gcmOpen(dek, x.ciphertext, x.nonce, dataAAD(account, env))
	if err != nil {
		return nil, ErrDecrypt
	}
	defer secrets.Zero(plain)
	out := map[string]string{}
	if err := json.Unmarshal(plain, &out); err != nil {
		return nil, ErrDecrypt
	}
	for _, v := range out {
		logging.RegisterSecret(v)
	}
	return out, nil
}

// Put stores or replaces a credential and appends its audit event in the same transaction (SE-021).
func (s *Store) Put(ctx context.Context, ownerID, broker, account string, env Environment, secret map[string]string, ev audit.Event) (Status, error) {
	for _, v := range secret {
		logging.RegisterSecret(v)
	}
	x, err := s.seal(account, env, secret)
	if err != nil {
		return Status{}, err
	}
	fields := make([]string, 0, len(secret))
	for k := range secret {
		fields = append(fields, k)
	}
	sort.Strings(fields)
	var st Status
	err = pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx, `INSERT INTO secrets.broker_credentials
			(broker_account_id, environment, broker, owner_id, ciphertext, nonce, wrapped_dek, dek_nonce, kek_id, fields)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
			ON CONFLICT (broker_account_id, environment) DO UPDATE SET broker=EXCLUDED.broker, ciphertext=EXCLUDED.ciphertext,
			  nonce=EXCLUDED.nonce, wrapped_dek=EXCLUDED.wrapped_dek, dek_nonce=EXCLUDED.dek_nonce, kek_id=EXCLUDED.kek_id,
			  fields=EXCLUDED.fields, rotated_at=now()
			RETURNING broker_account_id, environment, broker, fields, kek_id, created_at, rotated_at`,
			account, string(env), broker, ownerID, x.ciphertext, x.nonce, x.wrappedDEK, x.dekNonce, x.kekID, fields).
			Scan(&st.BrokerAccountID, &st.Environment, &st.Broker, &st.Fields, &st.KEKID, &st.CreatedAt, &st.RotatedAt)
		if err != nil {
			return err
		}
		ev.Details = mergeDetails(ev.Details, map[string]any{"fields": fields, "environment": string(env), "broker": broker, "kek_id": x.kekID})
		_, _, err = audit.AppendWith(ctx, tx, ev)
		return err
	})
	return st, err
}

// Get decrypts a credential. It is for broker-gateway's own adapters only and has no RPC (SE-031).
func (s *Store) Get(ctx context.Context, account string, env Environment) (map[string]string, error) {
	var x sealed
	err := s.pool.QueryRow(ctx, `SELECT ciphertext, nonce, wrapped_dek, dek_nonce, kek_id FROM secrets.broker_credentials
		WHERE broker_account_id=$1 AND environment=$2`, account, string(env)).Scan(&x.ciphertext, &x.nonce, &x.wrappedDEK, &x.dekNonce, &x.kekID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return s.open(account, env, x)
}

// Delete removes a credential and audits it in one transaction.
func (s *Store) Delete(ctx context.Context, account string, env Environment, ev audit.Event) error {
	return pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `DELETE FROM secrets.broker_credentials WHERE broker_account_id=$1 AND environment=$2`, account, string(env))
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 0 {
			return ErrNotFound
		}
		ev.Details = mergeDetails(ev.Details, map[string]any{"environment": string(env)})
		_, _, err = audit.AppendWith(ctx, tx, ev)
		return err
	})
}

// List returns metadata for every stored credential.
func (s *Store) List(ctx context.Context) ([]Status, error) {
	rows, err := s.pool.Query(ctx, `SELECT broker_account_id, environment, broker, fields, kek_id, created_at, rotated_at
		FROM secrets.broker_credentials ORDER BY broker_account_id, environment`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Status
	for rows.Next() {
		var st Status
		if err := rows.Scan(&st.BrokerAccountID, &st.Environment, &st.Broker, &st.Fields, &st.KEKID, &st.CreatedAt, &st.RotatedAt); err != nil {
			return nil, err
		}
		out = append(out, st)
	}
	return out, rows.Err()
}

// RewrapAll re-wraps every data key under the active KEK (KEK rotation, SE-033). Ciphertexts are
// unchanged; only wrapped_dek, dek_nonce, kek_id move.
func (s *Store) RewrapAll(ctx context.Context) (int, error) {
	n := 0
	err := pgx.BeginFunc(ctx, s.pool, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `SELECT broker_account_id, environment, wrapped_dek, dek_nonce, kek_id
			FROM secrets.broker_credentials WHERE kek_id <> $1 FOR UPDATE`, s.ring.Active)
		if err != nil {
			return err
		}
		type rec struct {
			account, env, kekID string
			wrapped, nonce      []byte
		}
		var recs []rec
		for rows.Next() {
			var r rec
			if err := rows.Scan(&r.account, &r.env, &r.wrapped, &r.nonce, &r.kekID); err != nil {
				rows.Close()
				return err
			}
			recs = append(recs, r)
		}
		rows.Close()
		for _, r := range recs {
			old, ok := s.ring.keys[r.kekID]
			if !ok {
				return fmt.Errorf("credstore: KEK %q not in key ring", r.kekID)
			}
			env := Environment(r.env)
			dek, err := gcmOpen(old, r.wrapped, r.nonce, dekAAD(r.account, env))
			if err != nil {
				return ErrDecrypt
			}
			wrapped, dn, err := gcmSeal(s.ring.keys[s.ring.Active], dek, dekAAD(r.account, env))
			secrets.Zero(dek)
			if err != nil {
				return err
			}
			if _, err := tx.Exec(ctx, `UPDATE secrets.broker_credentials SET wrapped_dek=$3, dek_nonce=$4, kek_id=$5
				WHERE broker_account_id=$1 AND environment=$2`, r.account, r.env, wrapped, dn, s.ring.Active); err != nil {
				return err
			}
			n++
		}
		return nil
	})
	return n, err
}

func gcmSeal(key, plain, aad []byte) (ct, nonce []byte, err error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, nil, err
	}
	g, err := cipher.NewGCM(block)
	if err != nil {
		return nil, nil, err
	}
	nonce = make([]byte, g.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, nil, err
	}
	return g.Seal(nil, nonce, plain, aad), nonce, nil
}

func gcmOpen(key, ct, nonce, aad []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	g, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return g.Open(nil, nonce, ct, aad)
}

func mergeDetails(a, b map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range a {
		out[k] = v
	}
	for k, v := range b {
		out[k] = v
	}
	return out
}
