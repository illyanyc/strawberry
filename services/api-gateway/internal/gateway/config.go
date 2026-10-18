// Package gateway implements the api-gateway: owner authentication (passkeys, TOTP, recovery codes),
// server-side sessions, CSRF protection, step-up, the REST API for the UI, and static UI serving.
// Every route except an explicit public allowlist requires a session (rule SE-004), and every
// authorization goes through policy.Authorize (SE-001).
package gateway

import (
	"errors"
	"net/netip"
	"net/url"
	"strings"
	"time"

	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/config"
	"github.com/illyanyc/strawberry/libs/go/db"
)

// Config is the gateway's typed configuration.
type Config struct {
	config.Common
	DB  db.Config
	Bus bus.Config

	HTTPAddr string `env:"TB_HTTP_ADDR" envDefault:":8080" json:"http_addr"`
	// WebAuthn relying party ID (registrable domain, e.g. "localhost" or "mini.tailnet.ts.net").
	RPID          string `env:"TB_AUTH_RP_ID" envDefault:"localhost" json:"rp_id"`
	RPDisplayName string `env:"TB_AUTH_RP_NAME" envDefault:"Strawberry" json:"rp_name"`
	// Origins allowed for WebAuthn and CSRF origin checks, e.g. "http://localhost:8080".
	Origins []string `env:"TB_AUTH_ORIGINS" envSeparator:"," envDefault:"http://localhost:8080" json:"origins"`
	// CookieSecure sets Secure + the __Host- prefix on cookies. Browsers treat http://localhost as secure.
	CookieSecure    bool          `env:"TB_AUTH_COOKIE_SECURE" envDefault:"true" json:"cookie_secure"`
	SessionIdle     time.Duration `env:"TB_AUTH_SESSION_IDLE" envDefault:"30m" json:"session_idle"`
	SessionAbsolute time.Duration `env:"TB_AUTH_SESSION_ABSOLUTE" envDefault:"12h" json:"session_absolute"`
	StepUpWindow    time.Duration `env:"TB_AUTH_STEP_UP_WINDOW" envDefault:"5m" json:"step_up_window"`
	MaxFailures     int           `env:"TB_AUTH_MAX_FAILURES" envDefault:"5" json:"max_failures"`
	LockoutWindow   time.Duration `env:"TB_AUTH_LOCKOUT_WINDOW" envDefault:"15m" json:"lockout_window"`
	// TrustedProxies are CIDRs whose X-Forwarded-For is honored (e.g. a host-side tunnel proxy).
	TrustedProxies []string `env:"TB_TRUSTED_PROXIES" envSeparator:"," json:"trusted_proxies"`
	// BrokerGatewayAddr is the gRPC address of broker-gateway's CredentialService.
	BrokerGatewayAddr string `env:"TB_BROKER_GATEWAY_ADDR" envDefault:"broker-gateway:9090" json:"broker_gateway_addr"`
	// Trading services behind the portfolio, strategy, order, kill-switch, and chart pages.
	ExecutionAddr  string `env:"TB_EXECUTION_ADDR" envDefault:"execution:9090" json:"execution_addr"`
	LedgerAddr     string `env:"TB_LEDGER_ADDR" envDefault:"ledger:9090" json:"ledger_addr"`
	MarketDataAddr string `env:"TB_MARKETDATA_ADDR" envDefault:"marketdata:9090" json:"marketdata_addr"`
	// Research services behind the training, experiments, and tearsheet pages (section 14 Phase 4).
	MLAddr        string `env:"TB_ML_ADDR" envDefault:"ml:9090" json:"ml_addr"`
	OptimizerAddr string `env:"TB_OPTIMIZER_ADDR" envDefault:"optimizer:9091" json:"optimizer_addr"`
	HoldoutAddr   string `env:"TB_HOLDOUT_ADDR" envDefault:"optimizer:9092" json:"holdout_addr"`
	// WebDir holds the static UI export (apps/web out/). Empty disables UI serving.
	WebDir string `env:"TB_WEB_DIR" envDefault:"/app/web" json:"web_dir"`
	// OwnerID is the single owner's ID (U7: single owner today, owner_id on every row).
	OwnerID string `env:"TB_OWNER_ID" envDefault:"owner" json:"owner_id"`
}

// Validate checks cross-field constraints.
func (c *Config) Validate() error {
	if err := c.Common.Validate(); err != nil {
		return err
	}
	if c.DB.User == "" {
		c.DB.User = "svc_api_gateway"
	}
	if c.Bus.User == "" {
		c.Bus.User = "api_gateway"
	}
	if len(c.Origins) == 0 {
		return errors.New("TB_AUTH_ORIGINS must list at least one origin")
	}
	for _, o := range c.Origins {
		u, err := url.Parse(o)
		if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" || u.Path != "" {
			return errors.New("TB_AUTH_ORIGINS entries must be scheme://host[:port]")
		}
		if u.Scheme == "http" && u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1" {
			return errors.New("plain-http origins are only allowed for localhost")
		}
	}
	for _, p := range c.TrustedProxies {
		if _, err := netip.ParsePrefix(strings.TrimSpace(p)); err != nil {
			return errors.New("TB_TRUSTED_PROXIES must be CIDRs")
		}
	}
	if c.StepUpWindow <= 0 || c.StepUpWindow > 15*time.Minute {
		return errors.New("TB_AUTH_STEP_UP_WINDOW must be in (0, 15m]")
	}
	return nil
}
