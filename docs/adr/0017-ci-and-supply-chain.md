# ADR-0017: CI on GitHub Actions with supply-chain gates

- **Status:** Accepted
- **Date:** 2026-10-17
- **Decision register:** D35
- **Related:** section 01 Stage A (CI pipeline), section 02 Stage A (supply chain); rules OP-002..OP-007, OP-043, OP-061, SE-053

## Context

The repo lives on GitHub. Section 01 asks for lint, type-check, tests, contract checks, image builds, vulnerability scanning, SBOMs, secret scanning, and a smoke test that starts the stack; section 02 asks CI to block committed secrets and high-severity vulnerable dependencies, with pinned dependencies and automated update PRs. Images must run on the owner's Apple Silicon Mac Mini.

## Decision

- **GitHub Actions**, one workflow (`.github/workflows/ci.yml`) on every PR and push to `main`; every third-party Action pinned by commit SHA; Dependabot updates Actions, Go modules, uv, npm, Dockerfile bases, and Compose images weekly.
- Jobs: docs gate; contracts (gen freshness, buf lint/breaking); Go (golangci-lint, `go test -race` with a Postgres service for integration tests); Python (ruff, mypy, pytest); web (tsc, eslint, vitest, build); security (gitleaks over full history, Trivy fs on lockfiles); images (per image: buildx `linux/amd64,linux/arm64` with QEMU, Trivy scan of the amd64 image, SPDX SBOM via Syft); arm64 availability of every pinned third-party image; stack smoke (clean-clone `make bootstrap && make up`, `make smoke`, alert on container kill, `make backup-drill`, fresh stack + Playwright browser e2e).
- Vulnerability gate: fail on **fixable** HIGH/CRITICAL; exceptions only via `.trivyignore` with a reason and review date.
- Secret scanning: gitleaks default rules; only test files with fake secret-shaped fixtures are allowlisted (`.gitleaks.toml`).
- Image signing (cosign) and pushing to a registry are deferred to Phase 6 (deploy).

## Alternatives considered

| Option | Why not |
|---|---|
| Grype instead of Trivy | Equivalent; Trivy covers fs and image with one tool. |
| Self-hosted runner on the Mac Mini | Would run untrusted PR code next to broker credentials. |
| Tag-pinned Actions | Tags are mutable; SHA pins plus Dependabot keep both safety and freshness. |

## Consequences

- Positive: every gate criterion is checked on every change; arm64 builds are proven before they reach the Mac.
- Negative / risks: multi-arch builds under QEMU and the stack job make CI take several minutes; caches (GHA cache for buildx, setup-go, pnpm, uv) mitigate.

## Validation

The Phase 0 PR runs the full workflow green; a proto edit without `make gen` fails `contracts`; a fake key in a non-test file fails `security`.
