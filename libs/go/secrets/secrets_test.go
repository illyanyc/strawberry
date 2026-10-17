package secrets_test

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/illyanyc/strawberry/libs/go/logging"
	"github.com/illyanyc/strawberry/libs/go/secrets"
)

func TestGetTrimsAndRegisters(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "db_password"), []byte("correct-horse-battery\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := secrets.NewStore(dir)
	got, err := s.GetString("db_password")
	if err != nil || got != "correct-horse-battery" {
		t.Fatalf("got %q, %v", got, err)
	}
	if logging.Redact("pw is correct-horse-battery") != "pw is "+logging.Redacted {
		t.Fatal("secret value was not registered with the redactor")
	}
}

func TestGetRejectsTraversalAndMissing(t *testing.T) {
	s := secrets.NewStore(t.TempDir())
	for _, name := range []string{"../etc/passwd", "a/b", "", "UPPER"} {
		if _, err := s.Get(name); err == nil {
			t.Fatalf("expected error for %q", name)
		}
	}
	if _, err := s.Get("missing"); !errors.Is(err, secrets.ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestZero(t *testing.T) {
	b := []byte("abc")
	secrets.Zero(b)
	if string(b) != "\x00\x00\x00" {
		t.Fatal("not zeroed")
	}
}
