#!/usr/bin/env bash
# Installs pinned developer tools into ./.tools/bin (never system-wide).
# Versions come from tools/versions.env. Safe to re-run; skips tools already at the pinned version.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../tools/versions.env
source "$ROOT/tools/versions.env"
BIN="$ROOT/.tools/bin"
mkdir -p "$BIN"
export GOBIN="$BIN"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch_go=amd64; arch_buf=x86_64; arch_protoc=x86_64 ;;
  arm64|aarch64) arch_go=arm64; arch_buf=$([ "$os" = darwin ] && echo arm64 || echo aarch64); arch_protoc=aarch_64 ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac
os_protoc=$([ "$os" = darwin ] && echo osx || echo linux)

stamp() { [ -f "$BIN/.$1-$2" ]; }
mark() { rm -f "$BIN/.$1-"*; touch "$BIN/.$1-$2"; }

if ! stamp buf "$BUF_VERSION"; then
  echo "installing buf $BUF_VERSION"
  curl -fsSL "https://github.com/bufbuild/buf/releases/download/v${BUF_VERSION}/buf-$(uname -s)-${arch_buf}" -o "$BIN/buf"
  chmod +x "$BIN/buf"; mark buf "$BUF_VERSION"
fi

if ! stamp protoc "$PROTOC_VERSION"; then
  echo "installing protoc $PROTOC_VERSION"
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VERSION}/protoc-${PROTOC_VERSION}-${os_protoc}-${arch_protoc}.zip" -o "$tmp/protoc.zip"
  (cd "$tmp" && unzip -q protoc.zip)
  cp "$tmp/bin/protoc" "$BIN/protoc"
  rm -rf "$ROOT/.tools/include" && cp -r "$tmp/include" "$ROOT/.tools/include"
  rm -rf "$tmp"; mark protoc "$PROTOC_VERSION"
fi

if ! stamp protoc-gen-go "$PROTOC_GEN_GO_VERSION"; then
  go install "google.golang.org/protobuf/cmd/protoc-gen-go@v${PROTOC_GEN_GO_VERSION}"
  mark protoc-gen-go "$PROTOC_GEN_GO_VERSION"
fi

if ! stamp protoc-gen-go-grpc "$PROTOC_GEN_GO_GRPC_VERSION"; then
  go install "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v${PROTOC_GEN_GO_GRPC_VERSION}"
  mark protoc-gen-go-grpc "$PROTOC_GEN_GO_GRPC_VERSION"
fi

if ! stamp golangci-lint "$GOLANGCI_LINT_VERSION"; then
  echo "installing golangci-lint $GOLANGCI_LINT_VERSION"
  curl -fsSL "https://github.com/golangci/golangci-lint/releases/download/v${GOLANGCI_LINT_VERSION}/golangci-lint-${GOLANGCI_LINT_VERSION}-${os}-${arch_go}.tar.gz" \
    | tar -xz -C "$BIN" --strip-components=1 "golangci-lint-${GOLANGCI_LINT_VERSION}-${os}-${arch_go}/golangci-lint"
  mark golangci-lint "$GOLANGCI_LINT_VERSION"
fi

if ! stamp gitleaks "$GITLEAKS_VERSION"; then
  echo "installing gitleaks $GITLEAKS_VERSION"
  gl_arch=$([ "$arch_go" = amd64 ] && echo x64 || echo arm64)
  curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${os}_${gl_arch}.tar.gz" \
    | tar -xz -C "$BIN" gitleaks
  mark gitleaks "$GITLEAKS_VERSION"
fi

echo "tools ready in $BIN"
