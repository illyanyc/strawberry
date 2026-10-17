# Trading Bot

A self-hosted platform for researching, training, backtesting, deploying, and monitoring ML-driven day and swing trading strategies for US equities, with Alpaca and Interactive Brokers. Each strategy runs in its own virtual sub-portfolio inside the real broker account and never touches manual holdings. Paper first, live only by explicit promotion.

**Status:** pre-code. The repo holds the agent and documentation structure; application code starts with Phase 0 (foundation). Stack choices are pending a research report.

## Start here

- [`AGENTS.md`](AGENTS.md): system map, where things live, how to run and test, and the mandatory documentation protocol. Read it before any change.
- [`docs/`](docs/): [architecture](docs/architecture/overview.md), [file map](docs/file-map.md), [business logic](docs/business-logic/README.md), [decisions](docs/decisions.md) and [ADRs](docs/adr/README.md), [glossary](docs/glossary.md).
- [`CHANGELOG.md`](CHANGELOG.md): what changed.
- [`.cursor/rules/`](.cursor/rules/): rules auto-loaded by Cursor agents, including trading safety.

## Running locally

Not available yet. The planned entry point is `make up` (Docker Compose); see [`AGENTS.md`](AGENTS.md#how-to-run-and-test).
