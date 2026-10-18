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
	// The AI analyst behind the analyst page (section 14 Phase 4 slice, ADR-0041).
	AssistantAddr string `env:"TB_ASSISTANT_ADDR" envDefault:"assistant:9090" json:"assistant_addr"`
	MonitorAddr   string `env:"TB_MONITOR_ADDR" envDefault:"monitor:9090" json:"monitor_addr"`
	// WebDir holds the static UI export (apps/web out/). Empty disables UI serving.
	WebDir string `env:"TB_WEB_DIR" envDefault:"/app/web" json:"web_dir"`
	// ClerkFrontendAPI is the hosted Clerk Frontend API origin. Empty keeps script-src 'self' (SE-065).
	// A value that is not a single https origin is ignored (SE-066).
	ClerkFrontendAPI string `env:"TB_CLERK_FRONTEND_API" json:"clerk_frontend_api"`
	// OwnerID is the single owner's ID (U7: single owner today, owner_id on every row).
	OwnerID string `env:"TB_OWNER_ID" envDefault:"owner" json:"owner_id"`

	// InternalAddr serves the Alertmanager → Web Push webhook on the backend network only (SE-063).
	InternalAddr string `env:"TB_INTERNAL_HTTP_ADDR" envDefault:":8082" json:"internal_addr"`
	// PushSubject is the VAPID contact (RFC 8292 "sub"): a mailto: or https: URL.
	PushSubject string `env:"TB_PUSH_SUBJECT" envDefault:"mailto:owner@strawberry.invalid" json:"push_subject"`
	// PushRedact keeps alert summaries off lock screens: severity and alert name only (SE-062).
	PushRedact bool `env:"TB_PUSH_REDACT" envDefault:"true" json:"push_redact"`
	// PushAllowedHosts are the push services subscriptions may point at (SE-061); empty = defaults.
	PushAllowedHosts []string `env:"TB_PUSH_ALLOWED_HOSTS" envSeparator:"," json:"push_allowed_hosts"`

	// TVWebhookAddr enables the TradingView webhook listener (empty = off, the default; D69).
	TVWebhookAddr string `env:"TB_TV_WEBHOOK_ADDR" json:"tv_webhook_addr"`
	// TVPublicURL is the dedicated Cloudflare Access hostname, for display (e.g. https://tv.example.com).
	TVPublicURL string `env:"TB_TV_PUBLIC_URL" json:"tv_public_url"`
	// TVAllowedIPs are accepted client addresses (CIDRs); default TradingView's published four.
	TVAllowedIPs []string `env:"TB_TV_ALLOWED_IPS" envSeparator:"," json:"tv_allowed_ips"`
	// TVTrustedProxies are peers (cloudflared) whose Cf-Connecting-Ip is the client address.
	TVTrustedProxies []string      `env:"TB_TV_TRUSTED_PROXIES" envSeparator:"," json:"tv_trusted_proxies"`
	TVMaxAge         time.Duration `env:"TB_TV_MAX_AGE" envDefault:"60s" json:"tv_max_age"`

	// Cloudflare Access (SE-064): team domain, the webhook application's audience tag, and the UI
	// application's audience tag plus the public hostnames it protects. Empty = not verified.
	CFAccessTeam    string   `env:"TB_CF_ACCESS_TEAM" json:"cf_access_team"`
	CFAccessTVAud   string   `env:"TB_CF_ACCESS_TV_AUD" json:"cf_access_tv_aud"`
	CFAccessUIAud   string   `env:"TB_CF_ACCESS_UI_AUD" json:"cf_access_ui_aud"`
	CFAccessUIHosts []string `env:"TB_CF_ACCESS_UI_HOSTS" envSeparator:"," json:"cf_access_ui_hosts"`
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
	if len(c.TVAllowedIPs) == 0 {
		c.TVAllowedIPs = TradingViewIPs
	}
	for _, p := range append(append([]string{}, c.TVAllowedIPs...), c.TVTrustedProxies...) {
		if _, err := netip.ParsePrefix(strings.TrimSpace(p)); err != nil {
			return errors.New("TB_TV_ALLOWED_IPS and TB_TV_TRUSTED_PROXIES must be CIDRs")
		}
	}
	if c.TVMaxAge <= 0 || c.TVMaxAge > 10*time.Minute {
		return errors.New("TB_TV_MAX_AGE must be in (0, 10m]")
	}
	if c.TVWebhookAddr != "" && c.TVWebhookAddr == c.HTTPAddr {
		return errors.New("TB_TV_WEBHOOK_ADDR must differ from TB_HTTP_ADDR (the webhook never shares the UI listener)")
	}
	if (c.CFAccessTVAud != "" || c.CFAccessUIAud != "") && !strings.HasSuffix(c.CFAccessTeam, ".cloudflareaccess.com") {
		return errors.New("TB_CF_ACCESS_TEAM must be <team>.cloudflareaccess.com when an Access audience is set")
	}
	if c.CFAccessUIAud != "" && len(c.CFAccessUIHosts) == 0 {
		return errors.New("TB_CF_ACCESS_UI_HOSTS must list the public hostnames protected by TB_CF_ACCESS_UI_AUD")
	}
	if c.PushSubject != "" && !strings.HasPrefix(c.PushSubject, "mailto:") && !strings.HasPrefix(c.PushSubject, "https://") {
		return errors.New("TB_PUSH_SUBJECT must be a mailto: or https: URL")
	}
	return nil
}
