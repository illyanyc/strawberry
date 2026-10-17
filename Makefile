# Developer entry points. Run `make help` for the list.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

export PATH := $(CURDIR)/.tools/bin:$(PATH)
BASE_REF ?= origin/main

## ---- stack configuration (override on the command line or in the environment) ----
# Default `make up` runs the core stack plus observability; add ml with PROFILES=core,observability,ml.
PROFILES ?= core,observability
export COMPOSE_PROFILES := $(PROFILES)
export TB_HOST_UID ?= $(shell id -u)
export TB_HOST_GID ?= $(shell id -g)
export TB_SECRETS_DIR ?= $(CURDIR)/.secrets
export TB_LAKE_DIR ?= $(CURDIR)/data/lake
export TB_ARTIFACTS_DIR ?= $(CURDIR)/data/mlflow-artifacts
export TB_BACKUP_DIR ?= $(CURDIR)/backups
export TB_VERSION ?= dev
# NATIVE_ML=1 publishes Postgres/NATS on 127.0.0.1 for native macOS ML workers (D29, ADR-0013).
COMPOSE := docker compose -f infra/compose/docker-compose.yml $(if $(NATIVE_ML),-f infra/compose/docker-compose.native-ml.yml)
OPS_IMAGE := strawberry/ops:$(TB_VERSION)
GO_MODULES := $(shell sed -n 's#^\s*\./##p' go.work)

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

.PHONY: tools
tools: ## Install pinned dev tools into .tools/bin
	scripts/bootstrap-tools.sh

.PHONY: deps
deps: ## Install Go, Python (uv), and Node (pnpm) dependencies from lockfiles
	uv sync --all-packages --frozen
	pnpm install --frozen-lockfile
	go mod download

## ---- local stack ----
.PHONY: bootstrap
bootstrap: dirs secrets build ## First run on a clean clone: data dirs, secrets, images (then `make up`)

.PHONY: dirs
dirs:
	@mkdir -p data/lake data/mlflow-artifacts backups/metrics

.PHONY: secrets
secrets: dirs ## Generate any missing secrets into .secrets/ (idempotent; never overwrites)
	$(COMPOSE) build db-init
	@mkdir -p $(TB_SECRETS_DIR) && chmod 700 $(TB_SECRETS_DIR)
	docker run --rm --network none --user $(TB_HOST_UID):$(TB_HOST_GID) -v $(TB_SECRETS_DIR):/out $(OPS_IMAGE) secrets init /out

.PHONY: secrets-encrypt
secrets-encrypt: ## SOPS+age-encrypt .secrets/ into infra/secrets/$(TB_ENV).sops.yaml (safe to back up off-host)
	docker run --rm --network none --user $(TB_HOST_UID):$(TB_HOST_GID) -v $(TB_SECRETS_DIR):/secrets:ro -v $(CURDIR)/infra/secrets:/bundle \
	  -e SOPS_AGE_RECIPIENTS="$${TB_SOPS_AGE_RECIPIENTS:-$$(cat $(TB_SECRETS_DIR)/backup_age_recipient)}" \
	  --entrypoint /usr/local/lib/tb-ops/secrets-bundle.sh $(OPS_IMAGE) encrypt /bundle/$${TB_ENV:-local}.sops.yaml

.PHONY: secrets-decrypt
secrets-decrypt: ## Restore .secrets/ from infra/secrets/$(TB_ENV).sops.yaml (AGE_KEY_FILE = your offline age identity)
	@mkdir -p $(TB_SECRETS_DIR) && chmod 700 $(TB_SECRETS_DIR)
	docker run --rm --network none --user $(TB_HOST_UID):$(TB_HOST_GID) -v $(TB_SECRETS_DIR):/secrets -v $(CURDIR)/infra/secrets:/bundle:ro \
	  -v $${AGE_KEY_FILE:?set AGE_KEY_FILE to your age identity file}:/run/age-key:ro -e SOPS_AGE_KEY_FILE=/run/age-key \
	  --entrypoint /usr/local/lib/tb-ops/secrets-bundle.sh $(OPS_IMAGE) decrypt /bundle/$${TB_ENV:-local}.sops.yaml

.PHONY: build
build: ## Build every image (all profiles)
	COMPOSE_PROFILES=core,observability,ml,ml-workers,ops $(COMPOSE) build

.PHONY: up
up: dirs ## Start the stack (PROFILES=$(PROFILES)) and wait until healthy
	@test -f $(TB_SECRETS_DIR)/pg_superuser_password || { echo "no secrets: run 'make bootstrap' (or 'make secrets') first"; exit 1; }
	$(COMPOSE) up -d --build --wait --wait-timeout 600

.PHONY: down
down: ## Stop the stack (volumes are kept)
	COMPOSE_PROFILES=core,observability,ml,ml-workers,ops,remote-cloudflare $(COMPOSE) down --remove-orphans

.PHONY: clean
clean: ## Stop the stack AND delete its Docker volumes (data loss; host data/ and backups/ are kept)
	COMPOSE_PROFILES=core,observability,ml,ml-workers,ops,remote-cloudflare $(COMPOSE) down --remove-orphans --volumes

.PHONY: ps
ps: ## Show stack status
	$(COMPOSE) ps

.PHONY: logs
logs: ## Follow logs (SERVICE=name to filter)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

.PHONY: migrate
migrate: ## Create roles/databases/schemas and apply migrations
	$(COMPOSE) run --rm db-init

.PHONY: seed
seed: ## Load development seed data (none in Phase 0; the dev universe arrives with Phase 1)
	@echo "Phase 0 has no seed data. Settings have code defaults; the development universe is added in Phase 1."

.PHONY: setup-token
setup-token: ## Print a one-time first-run setup token (terminal only, never logged)
	@$(COMPOSE) exec api-gateway /app/service setup-token

.PHONY: audit-verify
audit-verify: ## Recompute the audit hash chain (SE-022); non-zero exit if broken
	$(COMPOSE) run --rm ops audit verify

.PHONY: backup
backup: dirs ## Encrypted backup of Postgres, lake, MLflow artifacts, checksums, audit head -> backups/
	$(COMPOSE) run --rm --entrypoint /usr/local/lib/tb-ops/backup.sh ops

.PHONY: restore
restore: ## Restore BACKUP=backups/<file> into the running stack and verify checksums + audit chain
	@test -n "$(BACKUP)" || { echo "usage: make restore BACKUP=backups/strawberry-<ts>.tar.zst.age"; exit 1; }
	$(COMPOSE) stop api-gateway broker-gateway marketdata execution ledger context-data monitor
	$(COMPOSE) run --rm --entrypoint /usr/local/lib/tb-ops/restore.sh ops /backups/$(notdir $(BACKUP))
	$(COMPOSE) up -d --wait --wait-timeout 300

.PHONY: backup-drill
backup-drill: ## Backup, wipe all volumes, restore into a fresh stack, verify (OP-051)
	scripts/backup-drill.sh

.PHONY: smoke
smoke: ## Smoke test the running stack: every service ready, auth gate, ports, audit chain
	scripts/smoke.sh

.PHONY: e2e
e2e: ## End-to-end test against the running stack (passkey setup/login/step-up, credential canary)
	cd tests/e2e && TB_E2E_BASE_URL=$${TB_E2E_BASE_URL:-http://localhost:8080} go test -count=1 -v ./...

.PHONY: e2e-ui
e2e-ui: ## Browser e2e (Playwright + virtual passkey) against a FRESH running stack; screenshots in apps/web/test-results
	TB_E2E_SETUP_TOKEN=$$($(COMPOSE) exec -T api-gateway /app/service setup-token | awk '{print $$NF}') \
	  pnpm --filter @strawberry/web e2e

.PHONY: port-check
port-check: ## Fail if any container publishes a port on a non-loopback address (SE-051)
	scripts/port-check.sh

.PHONY: check-arm64
check-arm64: ## Verify every pinned third-party image has a linux/arm64 variant (Mac Mini)
	scripts/check-arm64.sh

## ---- contracts ----
.PHONY: gen
gen: ## Regenerate Go/Python/TypeScript code from contracts/proto
	find contracts/gen \( -name '*.pb.go' -o -name '*_pb2.py' -o -name '*_pb2.pyi' -o -name '*_pb.ts' \) -not -path '*/node_modules/*' -delete
	cd contracts && buf generate

.PHONY: gen-check
gen-check: gen ## Fail if generated contract code is stale (CI)
	@if ! git diff --quiet -- contracts/gen || [ -n "$$(git ls-files --others --exclude-standard -- contracts/gen)" ]; then \
	  echo "generated contracts are stale; run 'make gen' and commit:"; git status --short -- contracts/gen; exit 1; fi

.PHONY: lint-proto
lint-proto: ## buf lint + breaking-change check against BASE_REF
	cd contracts && buf lint
	@if git cat-file -e $(BASE_REF):contracts/buf.yaml 2>/dev/null; then \
	  cd contracts && buf breaking --against "../.git#ref=$(BASE_REF),subdir=contracts"; \
	else echo "no contracts on $(BASE_REF); skipping breaking check"; fi

## ---- quality ----
.PHONY: lint
lint: lint-proto lint-go lint-py lint-web docs-check ## All linters and the docs gate

.PHONY: lint-go
lint-go: ## golangci-lint on every Go module
	@for m in $(GO_MODULES); do echo "golangci-lint $$m"; (cd $$m && golangci-lint run ./...); done

.PHONY: lint-py
lint-py: ## ruff + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

.PHONY: lint-web
lint-web: ## TypeScript type checks and web lint
	pnpm -r --if-present typecheck
	pnpm -r --if-present lint

.PHONY: test
test: test-go test-py test-web ## All unit and contract tests

.PHONY: test-go
test-go: ## go test -race on every Go module
	@for m in $(GO_MODULES); do echo "go test $$m"; (cd $$m && go test -race -count=1 ./...); done

.PHONY: test-integration
test-integration: ## Go tests including Postgres integration tests (throwaway container unless TB_TEST_PG_SUPER_DSN is set)
	scripts/test-integration.sh

.PHONY: test-py
test-py: ## pytest
	uv run pytest

.PHONY: test-web
test-web: ## Web unit tests
	pnpm -r --if-present test

.PHONY: docs-check
docs-check: ## Documentation protocol gate: file-map coverage + same-commit doc rules vs BASE_REF
	python3 scripts/docs_check.py coverage
	python3 scripts/docs_check.py diff --base $(BASE_REF)
