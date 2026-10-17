// Package secrets reads deploy-time secrets from files mounted into the container (Compose
// `secrets:` → /run/secrets/<name>), never from environment variables or images (rule SE-010).
//
// Every value read is registered with the log redactor so it can never be logged (rule SE-012).
package secrets

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"

	"github.com/illyanyc/strawberry/libs/go/logging"
)

var validName = regexp.MustCompile(`^[a-z0-9][a-z0-9_.-]{0,127}$`)

// ErrNotFound is returned when the named secret file does not exist.
var ErrNotFound = errors.New("secret not found")

// Store reads secrets from a directory.
type Store struct {
	dir string
}

// NewStore returns a Store rooted at dir (typically /run/secrets).
func NewStore(dir string) *Store { return &Store{dir: dir} }

// Get returns the secret's bytes with one trailing newline trimmed. Callers should Zero the slice
// when done with it.
func (s *Store) Get(name string) ([]byte, error) {
	if !validName.MatchString(name) {
		return nil, fmt.Errorf("secrets: invalid name %q", name)
	}
	b, err := os.ReadFile(filepath.Join(s.dir, name)) //nolint:gosec // name is validated against validName (no separators)
	if errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("secrets: %s: %w", name, ErrNotFound)
	}
	if err != nil {
		return nil, fmt.Errorf("secrets: read %s: %w", name, err)
	}
	b = bytes.TrimSuffix(b, []byte("\n"))
	b = bytes.TrimSuffix(b, []byte("\r"))
	logging.RegisterSecret(string(b))
	return b, nil
}

// GetString is Get for text secrets such as passwords.
func (s *Store) GetString(name string) (string, error) {
	b, err := s.Get(name)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// Zero overwrites b in place. Go strings cannot be zeroed; keep long-lived secrets as []byte.
func Zero(b []byte) {
	for i := range b {
		b[i] = 0
	}
}
