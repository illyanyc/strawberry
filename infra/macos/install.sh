#!/bin/bash
# Mac Mini host setup (D21, D28, D29, D17; runbook docs/runbooks/mac-mini-host.md).
#   infra/macos/install.sh            check prerequisites and print what would change
#   infra/macos/install.sh --apply    install the LaunchDaemons and set power policy (sudo)
# Idempotent. Never touches secrets.
set -euo pipefail
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
REPO=$(cd "$(dirname "$0")/../.." && pwd)
USER_NAME=$(id -un); HOME_DIR=$HOME
BREW=$(brew --prefix 2>/dev/null || echo /opt/homebrew)
CPUS=${TB_COLIMA_CPUS:-6}; MEMORY_GB=${TB_COLIMA_MEMORY_GB:-16}; DISK_GB=${TB_COLIMA_DISK_GB:-200}

say() { printf '%s\n' "$*"; }
check() { if command -v "$1" >/dev/null 2>&1; then say "  ok   $1"; else say "  MISSING $1 ($2)"; MISSING=1; fi; }

[ "$(uname -s)" = Darwin ] || { echo "macOS only"; exit 1; }
say "prerequisites:"
MISSING=0
check colima "brew install colima docker docker-compose"
check docker "brew install docker"
check make "xcode-select --install"
check uv "brew install uv (native ML workers, D29)"
check tailscale "install the open-source tailscaled daemon: brew install tailscale && sudo brew services start tailscale (D17)"
fdesetup status 2>/dev/null | grep -q "On" && say "  ok   FileVault on" || say "  WARN FileVault is off (enable it; unlock remotely over Tailscale SSH)"
[ "$MISSING" = 0 ] || { say "install the missing tools, then re-run"; exit 1; }

render() {
  sed -e "s#@USER@#$USER_NAME#g" -e "s#@HOME@#$HOME_DIR#g" -e "s#@BREW@#$BREW#g" -e "s#@REPO@#$REPO#g" \
      -e "s#@CPUS@#$CPUS#g" -e "s#@MEMORY_GB@#$MEMORY_GB#g" -e "s#@DISK_GB@#$DISK_GB#g" "$1"
}

say "launch daemons (/Library/LaunchDaemons):"
for name in com.strawberry.colima com.strawberry.stack; do
  dst=/Library/LaunchDaemons/$name.plist
  if [ "$APPLY" = 1 ]; then
    render "$REPO/infra/macos/$name.plist" | sudo tee "$dst" >/dev/null
    sudo chown root:wheel "$dst" && sudo chmod 644 "$dst"
    sudo launchctl bootout system "$dst" 2>/dev/null || true
    sudo launchctl bootstrap system "$dst"
    say "  installed $name"
  else
    say "  would install $name -> $dst"
  fi
done

say "power policy (pmset): restart after power loss, never sleep, wake on network"
if [ "$APPLY" = 1 ]; then
  sudo pmset -a autorestart 1 sleep 0 disksleep 0 womp 1 powernap 0
else
  say "  would run: sudo pmset -a autorestart 1 sleep 0 disksleep 0 womp 1 powernap 0"
fi

cat <<EOF

next steps:
  1. make bootstrap && make up                 # first run (or let com.strawberry.stack do it at boot)
  2. make setup-token                          # then open http://localhost:8080 and register two passkeys
  3. remote access (D17): sudo tailscale up --ssh && tailscale serve --bg --https=443 http://127.0.0.1:8080
     and set TB_AUTH_RP_ID / TB_AUTH_ORIGINS in infra/compose/.env (see infra/compose/env.example)
  4. native ML workers (D29): make up NATIVE_ML=1, then render infra/macos/com.strawberry.ml-worker.plist
     per worker (PACKAGE=ml MODULE=tb_ml OPS_PORT=18081) into /Library/LaunchDaemons
  5. copy .secrets/backup_age_identity and broker_kek to your password manager (runbook: secrets-and-keys)
EOF
