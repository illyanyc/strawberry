// Package config loads typed service configuration: an optional JSON file named by
// TB_CONFIG_FILE, then environment variable overrides, then validation (fail fast).
//
// Secrets never come from config; use package secrets.
package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/caarlos0/env/v11"
)

// Validator is implemented by config structs that need cross-field checks.
type Validator interface {
	Validate() error
}

// Load fills cfg (a pointer to a struct with `env` and `json` tags) from TB_CONFIG_FILE (if set)
// and the environment, then calls cfg.Validate() when implemented.
func Load(cfg any) error {
	if path := os.Getenv("TB_CONFIG_FILE"); path != "" {
		b, err := os.ReadFile(path) //nolint:gosec // path is operator-provided config, not user input
		if err != nil {
			return fmt.Errorf("config: read %s: %w", path, err)
		}
		dec := json.NewDecoder(bytes.NewReader(b))
		dec.DisallowUnknownFields()
		if err := dec.Decode(cfg); err != nil {
			return fmt.Errorf("config: parse %s: %w", path, err)
		}
	}
	if err := env.Parse(cfg); err != nil {
		return fmt.Errorf("config: env: %w", err)
	}
	if v, ok := cfg.(Validator); ok {
		if err := v.Validate(); err != nil {
			return fmt.Errorf("config: invalid: %w", err)
		}
	}
	return nil
}

// Common holds settings every service shares.
type Common struct {
	Service     string `env:"TB_SERVICE,required" json:"service"`
	Environment string `env:"TB_ENVIRONMENT" envDefault:"local" json:"environment"`
	LogLevel    string `env:"TB_LOG_LEVEL" envDefault:"info" json:"log_level"`
	OpsAddr     string `env:"TB_OPS_ADDR" envDefault:":8081" json:"ops_addr"`
	// OTLP HTTP endpoint for traces, e.g. http://otel-collector:4318. Empty disables export.
	OTLPEndpoint string `env:"TB_OTLP_ENDPOINT" json:"otlp_endpoint"`
	SecretsDir   string `env:"TB_SECRETS_DIR" envDefault:"/run/secrets" json:"secrets_dir"`
}

// Validate checks the shared fields.
func (c Common) Validate() error {
	switch c.LogLevel {
	case "debug", "info", "warn", "error":
	default:
		return errors.New("TB_LOG_LEVEL must be debug|info|warn|error")
	}
	if c.Service == "" {
		return errors.New("TB_SERVICE is required")
	}
	return nil
}
