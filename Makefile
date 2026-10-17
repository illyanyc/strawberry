# Developer entry points. Run `make help` for the list.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

export PATH := $(CURDIR)/.tools/bin:$(PATH)
BASE_REF ?= origin/main

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n", $$1, $$2}'

.PHONY: tools
tools: ## Install pinned dev tools into .tools/bin
	scripts/bootstrap-tools.sh

.PHONY: docs-check
docs-check: ## Documentation protocol gate: file-map coverage + same-commit doc rules vs BASE_REF
	python3 scripts/docs_check.py coverage
	python3 scripts/docs_check.py diff --base $(BASE_REF)
