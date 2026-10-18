package db

import (
	"context"
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"io/fs"

	"github.com/golang-migrate/migrate/v4"
	pgxmigrate "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

//go:embed migrations
var migrationsFS embed.FS

// Schema is one migration set: a schema, its NOLOGIN owner role, and its SQL files under
// migrations/<Name>/.
type Schema struct {
	Name  string
	Owner string
}

// Schemas are migrated in this order (audit first: other schemas' code writes audit events).
var Schemas = []Schema{
	{Name: "audit", Owner: "audit_owner"},
	{Name: "auth", Owner: "auth_owner"},
	{Name: "config", Owner: "config_owner"},
	{Name: "secrets", Owner: "secrets_owner"},
	{Name: "broker", Owner: "broker_owner"},
	{Name: "refdata", Owner: "refdata_owner"},
	{Name: "marketdata", Owner: "marketdata_owner"},
	{Name: "broker_ibkr", Owner: "broker_ibkr_owner"},
}

// LoginRoles are the service roles that may log in to the platform database. Their passwords are
// read from secrets pg_<role>_password by `tbctl db init`.
var LoginRoles = []string{
	"svc_api_gateway", "svc_broker_gateway", "svc_broker_gateway_ibkr", "svc_marketdata", "svc_execution", "svc_ledger",
	"svc_ops", "svc_metrics",
}

// GroupRoles are NOLOGIN roles granted to login roles.
var GroupRoles = map[string][]string{
	// svc_broker_gateway_ibkr appends order-path audit events (BR-014); its tables are in broker_ibkr.
	"audit_writer": {"svc_api_gateway", "svc_broker_gateway", "svc_broker_gateway_ibkr", "svc_marketdata", "svc_execution", "svc_ledger"},
	"audit_reader": {"svc_api_gateway", "svc_ops"},
	// Read-only reference data (instrument master, calendar, corporate actions, universes).
	"refdata_reader": {"svc_ledger", "svc_execution", "svc_api_gateway", "svc_broker_gateway"},
	// Built-in monitoring role for postgres-exporter (read-only statistics, no table data).
	"pg_monitor": {"svc_metrics"},
}

// AppDatabases are separate databases owned by a login role of the same name (MLflow, Prefect).
var AppDatabases = []string{"mlflow", "prefect"}

// Bootstrap creates roles, databases, and schemas as the superuser, then applies every schema's
// migrations as that schema's owner. It is idempotent. passwords maps role → password.
func Bootstrap(ctx context.Context, superDSN, platformDB string, passwords map[string]string) error {
	admin, err := pgx.Connect(ctx, superDSN)
	if err != nil {
		return fmt.Errorf("db: connect superuser: %w", err)
	}
	defer admin.Close(ctx)

	roles := append(append([]string{}, LoginRoles...), AppDatabases...)
	for _, r := range roles {
		pw, ok := passwords[r]
		if !ok || pw == "" {
			return fmt.Errorf("db: missing password for role %s", r)
		}
		if err := ensureRole(ctx, admin, r, true); err != nil {
			return err
		}
		if err := execFormatted(ctx, admin, `SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', $1::text, $2::text)`, r, pw); err != nil {
			return fmt.Errorf("db: set password %s: %w", r, err)
		}
	}
	for _, s := range Schemas {
		if err := ensureRole(ctx, admin, s.Owner, false); err != nil {
			return err
		}
	}
	for g, members := range GroupRoles {
		if err := ensureRole(ctx, admin, g, false); err != nil {
			return err
		}
		for _, m := range members {
			if err := execFormatted(ctx, admin, `SELECT format('GRANT %I TO %I', $1::text, $2::text)`, g, m); err != nil {
				return err
			}
		}
	}
	for _, name := range append([]string{platformDB}, AppDatabases...) {
		owner := name
		if name == platformDB {
			owner = "postgres"
		}
		if err := ensureDatabase(ctx, admin, name, owner); err != nil {
			return err
		}
	}

	pcfg, err := pgx.ParseConfig(superDSN)
	if err != nil {
		return err
	}
	pcfg.Database = platformDB
	plat, err := pgx.ConnectConfig(ctx, pcfg)
	if err != nil {
		return fmt.Errorf("db: connect %s: %w", platformDB, err)
	}
	defer plat.Close(ctx)
	stmts := []string{
		fmt.Sprintf(`REVOKE ALL ON DATABASE %s FROM PUBLIC`, pgx.Identifier{platformDB}.Sanitize()),
		`REVOKE CREATE ON SCHEMA public FROM PUBLIC`,
	}
	for _, r := range LoginRoles {
		stmts = append(stmts, fmt.Sprintf(`GRANT CONNECT ON DATABASE %s TO %s`,
			pgx.Identifier{platformDB}.Sanitize(), pgx.Identifier{r}.Sanitize()))
	}
	for _, s := range Schemas {
		stmts = append(stmts, fmt.Sprintf(`CREATE SCHEMA IF NOT EXISTS %s AUTHORIZATION %s`,
			pgx.Identifier{s.Name}.Sanitize(), pgx.Identifier{s.Owner}.Sanitize()))
	}
	for _, q := range stmts {
		if _, err := plat.Exec(ctx, q); err != nil {
			return fmt.Errorf("db: %s: %w", q, err)
		}
	}
	for _, s := range Schemas {
		if err := Migrate(ctx, pcfg, s); err != nil {
			return err
		}
	}
	return nil
}

// Migrate applies one schema's migrations as its owner role (superuser connection + SET ROLE).
func Migrate(ctx context.Context, base *pgx.ConnConfig, s Schema) error {
	cfg := base.Copy()
	cfg.RuntimeParams["role"] = s.Owner
	cfg.RuntimeParams["search_path"] = s.Name
	sqlDB := stdlib.OpenDB(*cfg)
	defer func(c *sql.DB) { _ = c.Close() }(sqlDB)
	if err := sqlDB.PingContext(ctx); err != nil {
		return fmt.Errorf("db: migrate %s: %w", s.Name, err)
	}
	sub, err := fs.Sub(migrationsFS, "migrations/"+s.Name)
	if err != nil {
		return err
	}
	src, err := iofs.New(sub, ".")
	if err != nil {
		return fmt.Errorf("db: migrations %s: %w", s.Name, err)
	}
	drv, err := pgxmigrate.WithInstance(sqlDB, &pgxmigrate.Config{MigrationsTable: "schema_migrations", SchemaName: s.Name})
	if err != nil {
		return fmt.Errorf("db: migrate driver %s: %w", s.Name, err)
	}
	m, err := migrate.NewWithInstance("iofs", src, "pgx5", drv)
	if err != nil {
		return fmt.Errorf("db: migrate %s: %w", s.Name, err)
	}
	if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return fmt.Errorf("db: migrate %s up: %w", s.Name, err)
	}
	return nil
}

func ensureRole(ctx context.Context, c *pgx.Conn, role string, login bool) error {
	var exists bool
	if err := c.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)`, role).Scan(&exists); err != nil {
		return err
	}
	if exists {
		return nil
	}
	opt := "NOLOGIN"
	if login {
		opt = "LOGIN"
	}
	if _, err := c.Exec(ctx, fmt.Sprintf(`CREATE ROLE %s %s`, pgx.Identifier{role}.Sanitize(), opt)); err != nil {
		return fmt.Errorf("db: create role %s: %w", role, err)
	}
	return nil
}

func ensureDatabase(ctx context.Context, c *pgx.Conn, name, owner string) error {
	var exists bool
	if err := c.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = $1)`, name).Scan(&exists); err != nil {
		return err
	}
	if exists {
		return nil
	}
	_, err := c.Exec(ctx, fmt.Sprintf(`CREATE DATABASE %s OWNER %s`, pgx.Identifier{name}.Sanitize(), pgx.Identifier{owner}.Sanitize()))
	if err != nil {
		return fmt.Errorf("db: create database %s: %w", name, err)
	}
	return nil
}

// execFormatted runs a server-side format() query and executes the resulting statement, so
// identifiers and literals (passwords) are quoted by Postgres itself.
func execFormatted(ctx context.Context, c *pgx.Conn, formatQuery string, args ...any) error {
	var stmt string
	if err := c.QueryRow(ctx, formatQuery, args...).Scan(&stmt); err != nil {
		return err
	}
	_, err := c.Exec(ctx, stmt)
	return err
}
