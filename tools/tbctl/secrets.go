package main

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"filippo.io/age"

	"github.com/illyanyc/strawberry/libs/go/db"
	"github.com/illyanyc/strawberry/libs/go/svcauth"
)

// natsUsers lists bus users and their subject permissions (least privilege; widened per phase).
var natsUsers = map[string]struct{ pub, sub []string }{
	"api_gateway": {pub: []string{"alert.>", "sys.health.api-gateway"}, sub: []string{"_INBOX.>", "md.bar.>", "exec.>"}},
	"marketdata":  {pub: []string{"md.>", "ref.>", "alert.>", "sys.health.marketdata", "sys.health.alpaca.>", "sys.health.fmp"}, sub: []string{"_INBOX.>"}},
	// JetStream pull consumers need the consumer API and ack subjects ($JS.API.CONSUMER.>,
	// $JS.API.STREAM.INFO.>, $JS.ACK.>).
	"execution": {pub: []string{"ord.>", "risk.kill", "alert.>", "sys.health.execution", "$JS.API.CONSUMER.>", "$JS.API.STREAM.INFO.>", "$JS.API.INFO", "$JS.ACK.>"},
		sub: []string{"_INBOX.>", "sig.>", "ord.>", "exec.>", "risk.kill", "recon.result.>", "acct.snapshot.>", "md.bar.>", "sys.health.strategy-runtime"}},
	"ledger": {pub: []string{"ledger.update.>", "recon.result.>", "alert.>", "sys.health.ledger", "$JS.API.CONSUMER.>", "$JS.API.STREAM.INFO.>", "$JS.API.INFO", "$JS.ACK.>"},
		sub: []string{"_INBOX.>", "exec.>", "acct.snapshot.>", "recon.request.>", "ref.>"}},
	// Broker adapters publish their accounts' order updates, executions, snapshots, and
	// reconciliation requests through their outbox relay (ADR-0018).
	"broker_gateway":      {pub: []string{"ord.update.>", "exec.>", "acct.snapshot.>", "recon.request.>", "alert.>", "sys.health.broker-gateway"}, sub: []string{"_INBOX.>", "sys.health.execution"}},
	"broker_gateway_ibkr": {pub: []string{"ord.update.>", "exec.>", "acct.snapshot.>", "recon.request.>", "alert.>", "sys.health.broker-gateway-ibkr"}, sub: []string{"_INBOX.>"}},
	// The strategy runtime only publishes intents and its heartbeat; it reads bars and its own
	// strategies' ledger updates (ADR-0026).
	"strategy_runtime": {pub: []string{"ord.intent", "sys.health.strategy-runtime", "alert.>", "$JS.API.CONSUMER.>", "$JS.API.STREAM.INFO.>", "$JS.API.INFO", "$JS.ACK.>"},
		sub: []string{"_INBOX.>", "md.bar.>", "ledger.update.>"}},
	// The monitor forwards bus alerts to Alertmanager and builds daily reports from order-path and
	// ledger events (section 13).
	"monitor": {pub: []string{"alert.>", "sys.health.monitor", "$JS.API.CONSUMER.>", "$JS.API.STREAM.INFO.>", "$JS.API.INFO", "$JS.ACK.>"},
		sub: []string{"_INBOX.>", "alert.>", "sys.health.>", "recon.result.>", "ord.decision", "exec.>", "ledger.update.>"}},
	"ops": {pub: []string{">"}, sub: []string{">"}},
}

// signingServices get Ed25519 service-token keys (SE-040).
var signingServices = []string{"api-gateway", "broker-gateway", "execution", "ledger", "marketdata"}

// initSecrets writes every missing secret into dir. Existing files are never overwritten, so the
// command is safe to re-run after adding a new service. Files are 0644 inside a 0700 directory: the
// directory protects them on the host, and containers running as non-root users can read the
// individual bind-mounted files.
func initSecrets(dir string) error {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		return err
	}
	created := []string{}
	put := func(name string, gen func() (string, error)) error {
		p := filepath.Join(dir, name)
		if _, err := os.Stat(p); err == nil {
			return nil
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		v, err := gen()
		if err != nil {
			return err
		}
		if err := os.WriteFile(p, []byte(v+"\n"), 0o644); err != nil {
			return err
		}
		created = append(created, name)
		return nil
	}
	pw := func() (string, error) { return randomHex(24) }
	b64 := func(n int) func() (string, error) {
		return func() (string, error) {
			b := make([]byte, n)
			_, err := rand.Read(b)
			return base64.StdEncoding.EncodeToString(b), err
		}
	}

	if err := put("pg_superuser_password", pw); err != nil {
		return err
	}
	for _, r := range append(append([]string{}, db.LoginRoles...), db.AppDatabases...) {
		if err := put(db.PasswordSecretName(r), pw); err != nil {
			return err
		}
	}
	users := make([]string, 0, len(natsUsers))
	for u := range natsUsers {
		users = append(users, u)
		if err := put("nats_"+u+"_password", pw); err != nil {
			return err
		}
	}
	sort.Strings(users)
	if err := writeNATSUsers(dir, users, &created); err != nil {
		return err
	}
	if err := put("valkey_password", pw); err != nil {
		return err
	}
	if err := put("valkey_users.acl", func() (string, error) {
		v, err := os.ReadFile(filepath.Join(dir, "valkey_password"))
		if err != nil {
			return "", err
		}
		return "user default off\nuser tb on >" + strings.TrimSpace(string(v)) + " ~* &* +@all", nil
	}); err != nil {
		return err
	}
	if err := put("gateway_auth_key", b64(32)); err != nil {
		return err
	}
	if err := put("broker_kek", func() (string, error) {
		k, err := b64(32)()
		return "k1:" + k, err
	}); err != nil {
		return err
	}
	for _, s := range signingServices {
		name := svcauth.KeySecretName(s)
		if _, err := os.Stat(filepath.Join(dir, name)); errors.Is(err, os.ErrNotExist) {
			seed, pub, err := svcauth.GenerateKey()
			if err != nil {
				return err
			}
			if err := os.WriteFile(filepath.Join(dir, name), []byte(base64.StdEncoding.EncodeToString(seed)+"\n"), 0o644); err != nil {
				return err
			}
			if err := os.WriteFile(filepath.Join(dir, name+".pub"), []byte(base64.StdEncoding.EncodeToString(pub)+"\n"), 0o644); err != nil {
				return err
			}
			created = append(created, name, name+".pub")
		}
	}
	if err := put("grafana_admin_password", pw); err != nil {
		return err
	}
	if _, err := os.Stat(filepath.Join(dir, "backup_age_identity")); errors.Is(err, os.ErrNotExist) {
		id, err := age.GenerateX25519Identity()
		if err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dir, "backup_age_identity"), []byte(id.String()+"\n"), 0o600); err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(dir, "backup_age_recipient"), []byte(id.Recipient().String()+"\n"), 0o644); err != nil {
			return err
		}
		created = append(created, "backup_age_identity", "backup_age_recipient")
	}
	// VNC for the manual IBKR login fallback (ADR-0023). x11vnc uses only the first 8 characters.
	if err := put("ibkr_vnc_password", func() (string, error) { return randomHex(4) }); err != nil {
		return err
	}
	// Optional integrations: placeholders keep Compose happy until the owner fills them in. The IBKR
	// login files are mounted only into the matching ib-gateway container (IB-070).
	for _, name := range []string{
		"pushover_user_key", "pushover_app_token", "cloudflared_tunnel_token", "fmp_api_key",
		"ibkr_paper_username", "ibkr_paper_password", "ibkr_live_username", "ibkr_live_password",
	} {
		if err := put(name, func() (string, error) { return "unset", nil }); err != nil {
			return err
		}
	}
	sort.Strings(created)
	fmt.Printf("secrets init: %d new file(s) in %s\n", len(created), dir)
	for _, c := range created {
		fmt.Println("  +", c)
	}
	return nil
}

func writeNATSUsers(dir string, users []string, created *[]string) error {
	path := filepath.Join(dir, "nats_users.conf")
	var b strings.Builder
	b.WriteString("# Generated by tbctl secrets init. Bus users and subject permissions.\nauthorization {\n  users = [\n")
	for _, u := range users {
		raw, err := os.ReadFile(filepath.Join(dir, "nats_"+u+"_password"))
		if err != nil {
			return err
		}
		p := natsUsers[u]
		fmt.Fprintf(&b, "    {user: %q, password: %q, permissions: {publish: %s, subscribe: %s}}\n",
			u, strings.TrimSpace(string(raw)), quoteList(p.pub), quoteList(p.sub))
	}
	b.WriteString("  ]\n}\n")
	old, err := os.ReadFile(path)
	if err == nil && string(old) == b.String() {
		return nil
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		return err
	}
	*created = append(*created, "nats_users.conf")
	return nil
}

func quoteList(xs []string) string {
	q := make([]string, len(xs))
	for i, x := range xs {
		q[i] = fmt.Sprintf("%q", x)
	}
	return "[" + strings.Join(q, ", ") + "]"
}

func randomHex(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}
