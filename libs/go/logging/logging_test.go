package logging_test

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/illyanyc/strawberry/libs/go/logging"
)

// SE-012: secrets never appear in logs, whichever path they take into a log record.
func TestSE012RedactsSecretsOnEveryPath(t *testing.T) {
	const registered = "s3cr3t-registered-value-0123456789"
	logging.RegisterSecret(registered)
	t.Cleanup(func() { logging.UnregisterSecret(registered) })

	cases := map[string]string{
		"registered":   registered,
		"alpaca key":   "PKABCDEFGHIJKLMNOPQRST",
		"bearer":       "Bearer abcdefghijklmnop.qrstuvwx",
		"jwt":          "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
		"aws":          "AKIAABCDEFGHIJKLMNOP",
		"kv in text":   "api_secret=hunter2hunter2",
		"age identity": "AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ",
	}
	for name, secret := range cases {
		t.Run(name, func(t *testing.T) {
			var buf bytes.Buffer
			log := logging.New("test", "debug", &buf)
			ctx := logging.WithCorrelationID(context.Background(), "corr-1")
			log.InfoContext(ctx, "message with "+secret)
			log.InfoContext(ctx, "attr", "detail", secret)
			log.ErrorContext(ctx, "err", "error", errors.New("upstream said "+secret))
			log.InfoContext(ctx, "wrapped", "error", fmt.Errorf("outer: %w", errors.New(secret)))
			log.With("ctx_attr", secret).InfoContext(ctx, "with")
			log.InfoContext(ctx, "group", "g", map[string]string{"inner": secret})
			out := buf.String()
			secretCore := secret
			if strings.Contains(secret, "=") {
				secretCore = strings.SplitN(secret, "=", 2)[1]
			}
			if strings.Contains(out, secretCore) {
				t.Fatalf("secret leaked into logs:\n%s", out)
			}
			if !strings.Contains(out, `"correlation_id":"corr-1"`) || !strings.Contains(out, `"service":"test"`) {
				t.Fatalf("missing standard fields:\n%s", out)
			}
		})
	}
}

func TestSE012SensitiveKeysAlwaysRedacted(t *testing.T) {
	var buf bytes.Buffer
	log := logging.New("test", "info", &buf)
	log.Info("login", "password", "short", "Authorization", "x", "session_id", "abc", "api_key", "k")
	for _, v := range []string{`"short"`, `"x"`, `"abc"`, `"k"`} {
		if strings.Contains(buf.String(), v) {
			t.Fatalf("sensitive key value %s leaked: %s", v, buf.String())
		}
	}
}

func TestRedactLeavesOrdinaryTextAlone(t *testing.T) {
	in := "order tb1-abc123-01JABCDEFGHJKMNPQRSTVWXYZ0-1 accepted; hash=9f86d081884c7d65"
	if got := logging.Redact(in); got != in {
		t.Fatalf("over-redaction: %q", got)
	}
}
