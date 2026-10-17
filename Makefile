# Developer entry points. Run `make help` for the list.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

export PATH := $(CURDIR)/.tools/bin:$(PATH)
BASE_REF ?= origin/main
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
