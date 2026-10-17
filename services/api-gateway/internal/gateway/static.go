package gateway

import (
	"log/slog"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
)

// webUI serves the static Vite SPA build (D11). Unknown paths fall back to index.html so client-side
// routes work; hashed assets under /assets/ are cached immutably. The SPA has no inline scripts, so the
// strict CSP set by securityHeaders applies unchanged (SE-013).
type webUI struct {
	dir string
	log *slog.Logger
}

func newWebUI(dir string, log *slog.Logger) *webUI { return &webUI{dir: dir, log: log} }

func (u *webUI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	clean := path.Clean("/" + r.URL.Path)
	full := filepath.Join(u.dir, filepath.FromSlash(clean))
	if !strings.HasPrefix(full, filepath.Clean(u.dir)) {
		http.NotFound(w, r)
		return
	}
	// full is path.Clean'ed and prefix-checked against dir above.
	fi, err := os.Stat(full) //nolint:gosec // see above
	if err == nil && !fi.IsDir() {
		if strings.HasPrefix(clean, "/assets/") {
			w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
		} else {
			w.Header().Set("Cache-Control", "no-cache")
		}
		http.ServeFile(w, r, full) //nolint:gosec // see above
		return
	}
	if strings.HasPrefix(clean, "/assets/") || path.Ext(clean) != "" {
		http.NotFound(w, r)
		return
	}
	index := filepath.Join(u.dir, "index.html")
	if _, err := os.Stat(index); err != nil {
		http.Error(w, "UI not built", http.StatusNotFound)
		return
	}
	w.Header().Set("Cache-Control", "no-cache")
	http.ServeFile(w, r, index)
}
