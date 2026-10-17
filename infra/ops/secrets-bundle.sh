#!/usr/bin/env bash
# SOPS + age bundle of the runtime secrets directory (D16, ADR-0011).
#   encrypt <bundle.sops.yaml>   /secrets/* -> {filename: base64(content)} -> SOPS-encrypted YAML
#   decrypt <bundle.sops.yaml>   bundle -> /secrets/* (existing files are overwritten)
# Encryption needs SOPS_AGE_RECIPIENTS; decryption needs SOPS_AGE_KEY_FILE (your offline age identity).
set -euo pipefail
cmd=${1:?encrypt|decrypt}
bundle=${2:?bundle path}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

case "$cmd" in
  encrypt)
    : "${SOPS_AGE_RECIPIENTS:?set SOPS_AGE_RECIPIENTS}"
    echo '{}' >"$tmp/plain.json"
    for f in /secrets/*; do
      [ -f "$f" ] || continue
      jq --arg k "$(basename "$f")" --arg v "$(base64 -w0 <"$f")" '. + {($k): $v}' "$tmp/plain.json" >"$tmp/next.json"
      mv "$tmp/next.json" "$tmp/plain.json"
    done
    sops --encrypt --age "$SOPS_AGE_RECIPIENTS" --input-type json --output-type yaml "$tmp/plain.json" >"$tmp/out.yaml"
    mv "$tmp/out.yaml" "$bundle"
    echo "encrypted $(jq 'length' "$tmp/plain.json") secret file(s) into $(basename "$bundle")"
    ;;
  decrypt)
    : "${SOPS_AGE_KEY_FILE:?set SOPS_AGE_KEY_FILE}"
    sops --decrypt --input-type yaml --output-type json "$bundle" >"$tmp/plain.json"
    jq -r 'del(.sops) | keys[]' "$tmp/plain.json" | while IFS= read -r name; do
      case "$name" in */*|..*|"") echo "skipping unsafe name: $name" >&2; continue ;; esac
      jq -r --arg k "$name" '.[$k]' "$tmp/plain.json" | base64 -d >"/secrets/$name"
    done
    echo "restored $(jq 'del(.sops) | length' "$tmp/plain.json") secret file(s)"
    ;;
  *) echo "usage: $0 encrypt|decrypt <bundle>" >&2; exit 2 ;;
esac
