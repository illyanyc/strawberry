// Package db connects services to PostgreSQL with their least-privilege login role (rule SE-050)
// and owns the platform's database bootstrap and migrations (ADR-0005).
//
// Layout: one database `platform` with one schema per owning area (audit, auth, config, secrets);
// each schema is owned by a NOLOGIN `<schema>_owner` role; services log in as `svc_<service>` and
// receive only the grants their migrations give them. MLflow and Prefect get their own databases.
package db

import (
	"context"
	"fmt"
	"net/url"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/illyanyc/strawberry/libs/go/secrets"
)

// Config locates the database. The password always comes from a mounted secret.
type Config struct {
	Host     string `env:"TB_DB_HOST" envDefault:"postgres" json:"db_host"`
	Port     int    `env:"TB_DB_PORT" envDefault:"5432" json:"db_port"`
	Name     string `env:"TB_DB_NAME" envDefault:"platform" json:"db_name"`
	User     string `env:"TB_DB_USER" json:"db_user"`
	SSLMode  string `env:"TB_DB_SSLMODE" envDefault:"disable" json:"db_sslmode"`
	MaxConns int32  `env:"TB_DB_MAX_CONNS" envDefault:"8" json:"db_max_conns"`
	// PasswordSecret names the secret file holding the password (default pg_<user>_password).
	PasswordSecret string `env:"TB_DB_PASSWORD_SECRET" json:"db_password_secret"`
}

// PasswordSecretName returns the secret file name for a role's password.
func PasswordSecretName(role string) string { return "pg_" + role + "_password" }

// DSN builds a connection URL (password URL-escaped).
func (c Config) DSN(password string) string {
	u := url.URL{
		Scheme:   "postgres",
		User:     url.UserPassword(c.User, password),
		Host:     fmt.Sprintf("%s:%d", c.Host, c.Port),
		Path:     "/" + c.Name,
		RawQuery: url.Values{"sslmode": {c.SSLMode}}.Encode(),
	}
	return u.String()
}

// Connect opens a pool as c.User with the password from store.
func Connect(ctx context.Context, c Config, store *secrets.Store) (*pgxpool.Pool, error) {
	name := c.PasswordSecret
	if name == "" {
		name = PasswordSecretName(c.User)
	}
	pw, err := store.GetString(name)
	if err != nil {
		return nil, err
	}
	cfg, err := pgxpool.ParseConfig(c.DSN(pw))
	if err != nil {
		return nil, fmt.Errorf("db: config: %w", err)
	}
	cfg.MaxConns = c.MaxConns
	cfg.ConnConfig.RuntimeParams["application_name"] = c.User
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("db: connect as %s: %w", c.User, err)
	}
	return pool, nil
}

// Ping is an ops readiness check.
func Ping(pool *pgxpool.Pool) func(context.Context) error {
	return func(ctx context.Context) error { return pool.Ping(ctx) }
}
