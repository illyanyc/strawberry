// Command tbctl is the operator CLI, run inside the `ops` container (or locally with Go):
//
//	tbctl secrets init <dir>   generate every missing deploy-time secret file into <dir> (idempotent)
//	tbctl db init              create roles, databases, schemas and apply migrations (superuser)
//	tbctl db checksum          print a per-table checksum of the platform DB (backup drills)
//	tbctl bus init             create or update the JetStream streams
//	tbctl audit verify         recompute the audit hash chain; exit 1 if broken (SE-022)
//	tbctl audit head           print the audit head as JSON (backup manifests, SE-024)
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/secrets"
)

func main() {
	if len(os.Args) < 3 {
		usage()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	store := secrets.NewStore(env("TB_SECRETS_DIR", "/run/secrets"))
	var err error
	switch os.Args[1] + " " + os.Args[2] {
	case "secrets init":
		if len(os.Args) < 4 {
			usage()
		}
		err = initSecrets(os.Args[3])
	case "db init":
		err = dbInit(ctx, store)
	case "db checksum":
		err = dbChecksum(ctx, store)
	case "bus init":
		err = busInit(ctx, store)
	case "audit verify":
		err = auditVerify(ctx, store, true)
	case "audit head":
		err = auditVerify(ctx, store, false)
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "tbctl:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: tbctl secrets init <dir> | db init | db checksum | bus init | audit verify | audit head")
	os.Exit(2)
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func dbConfig(user string) db.Config {
	return db.Config{Host: env("TB_DB_HOST", "postgres"), Port: 5432, Name: env("TB_DB_NAME", "platform"), User: user, SSLMode: env("TB_DB_SSLMODE", "disable")}
}

func superDSN(store *secrets.Store, database string) (string, error) {
	pw, err := store.GetString("pg_superuser_password")
	if err != nil {
		return "", err
	}
	c := dbConfig("postgres")
	c.Name = database
	return c.DSN(pw), nil
}

func dbInit(ctx context.Context, store *secrets.Store) error {
	dsn, err := superDSN(store, "postgres")
	if err != nil {
		return err
	}
	pw := map[string]string{}
	for _, r := range append(append([]string{}, db.LoginRoles...), db.AppDatabases...) {
		v, err := store.GetString(db.PasswordSecretName(r))
		if err != nil {
			return err
		}
		pw[r] = v
	}
	if err := retry(ctx, func() error { return db.Bootstrap(ctx, dsn, env("TB_DB_NAME", "platform"), pw) }); err != nil {
		return err
	}
	fmt.Println("db init: roles, databases, schemas, and migrations are up to date")
	return nil
}

// dbChecksum prints md5 over every row of every table in the platform schemas, as superuser.
func dbChecksum(ctx context.Context, store *secrets.Store) error {
	dsn, err := superDSN(store, env("TB_DB_NAME", "platform"))
	if err != nil {
		return err
	}
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		return err
	}
	defer func() { _ = conn.Close(ctx) }()
	rows, err := conn.Query(ctx, `SELECT table_schema, table_name FROM information_schema.tables
		WHERE table_schema IN ('audit','auth','config','secrets') AND table_type='BASE TABLE' ORDER BY 1, 2`)
	if err != nil {
		return err
	}
	var tables [][2]string
	for rows.Next() {
		var s, t string
		if err := rows.Scan(&s, &t); err != nil {
			return err
		}
		tables = append(tables, [2]string{s, t})
	}
	rows.Close()
	out := map[string]string{}
	for _, t := range tables {
		id := pgx.Identifier{t[0], t[1]}.Sanitize()
		var sum *string
		var n int64
		q := fmt.Sprintf(`SELECT count(*), md5(string_agg(x::text, '|' ORDER BY x::text)) FROM %s x`, id)
		if err := conn.QueryRow(ctx, q).Scan(&n, &sum); err != nil {
			return err
		}
		v := "empty"
		if sum != nil {
			v = *sum
		}
		out[t[0]+"."+t[1]] = fmt.Sprintf("%d:%s", n, v)
	}
	return json.NewEncoder(os.Stdout).Encode(out)
}

func busInit(ctx context.Context, store *secrets.Store) error {
	nc, err := bus.Connect(bus.Config{URL: env("TB_NATS_URL", "nats://nats:4222"), User: "ops"}, store, "tbctl")
	if err != nil {
		return err
	}
	defer nc.Close()
	if err := retry(ctx, func() error { return bus.EnsureStreams(ctx, nc) }); err != nil {
		return err
	}
	fmt.Printf("bus init: %d streams up to date\n", len(bus.Streams))
	return nil
}

func auditVerify(ctx context.Context, store *secrets.Store, verify bool) error {
	pw, err := store.GetString(db.PasswordSecretName("svc_ops"))
	if err != nil {
		return err
	}
	conn, err := pgx.Connect(ctx, dbConfig("svc_ops").DSN(pw))
	if err != nil {
		return err
	}
	defer func() { _ = conn.Close(ctx) }()
	res, err := audit.Verify(ctx, conn)
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{
		"ok": err == nil, "count": res.Count, "head_seq": res.HeadSeq, "head_hash": res.HeadHash,
		"broken_at": res.BrokenAt, "reason": res.Reason, "checked_at": time.Now().UTC().Format(time.RFC3339),
	})
	if err != nil && (verify || !errors.Is(err, audit.ErrChainBroken)) {
		return err
	}
	return nil
}

func retry(ctx context.Context, fn func() error) error {
	var err error
	for i := 0; i < 30; i++ {
		if err = fn(); err == nil {
			return nil
		}
		select {
		case <-ctx.Done():
			return err
		case <-time.After(2 * time.Second):
		}
	}
	return err
}
