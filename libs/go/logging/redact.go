package logging

import (
	"regexp"
	"strings"
	"sync"
)

// Redacted replaces any secret value removed from logs, traces, and error responses (rule SE-012).
const Redacted = "[REDACTED]"

// sensitiveKeys are attribute-key fragments whose values are always redacted, whatever they contain.
var sensitiveKeys = []string{
	"password", "passwd", "secret", "token", "authorization", "cookie", "api_key", "apikey",
	"private_key", "credential", "session_id", "recovery_code", "totp", "setup_code",
}

// secretPatterns catch well-known secret shapes in free text.
var secretPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}`),
	regexp.MustCompile(`\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}`),
	regexp.MustCompile(`\b(?:AKIA|ASIA)[0-9A-Z]{16}\b`),
	// Alpaca API key IDs (paper "PK...", live "AK...").
	regexp.MustCompile(`\b(?:PK|AK|CK)[A-Z0-9]{16,}\b`),
	regexp.MustCompile(`-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----`),
	regexp.MustCompile(`AGE-SECRET-KEY-1[0-9A-Z]{50,}`),
	regexp.MustCompile(`(?i)\b(api[_-]?key|api[_-]?secret|secret|token|password|passwd)(["']?\s*[=:]\s*["']?)[^\s"',;&]{4,}`),
}

var registry = struct {
	sync.RWMutex
	values map[string]struct{}
}{values: map[string]struct{}{}}

// minRegisteredLen avoids redacting common short strings if someone registers a trivial value.
const minRegisteredLen = 6

// RegisterSecret adds an exact value that must never appear in output. Package secrets calls this for
// every secret it loads; broker-gateway calls it for credential material while handling it.
func RegisterSecret(value string) {
	if len(value) < minRegisteredLen {
		return
	}
	registry.Lock()
	registry.values[value] = struct{}{}
	registry.Unlock()
}

// UnregisterSecret removes a value registered with RegisterSecret (e.g. after a credential is rotated).
func UnregisterSecret(value string) {
	registry.Lock()
	delete(registry.values, value)
	registry.Unlock()
}

// IsSensitiveKey reports whether an attribute/field name always carries secret material.
func IsSensitiveKey(key string) bool {
	k := strings.ToLower(key)
	for _, s := range sensitiveKeys {
		if strings.Contains(k, s) {
			return true
		}
	}
	return false
}

// Redact removes registered secret values and known secret patterns from s.
func Redact(s string) string {
	if s == "" {
		return s
	}
	registry.RLock()
	for v := range registry.values {
		if strings.Contains(s, v) {
			s = strings.ReplaceAll(s, v, Redacted)
		}
	}
	registry.RUnlock()
	for i, re := range secretPatterns {
		if i == len(secretPatterns)-1 {
			s = re.ReplaceAllString(s, "${1}${2}"+Redacted)
			continue
		}
		s = re.ReplaceAllString(s, Redacted)
	}
	return s
}
