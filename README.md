# Trading Bot

A self-hosted platform for researching, training, backtesting, deploying, and monitoring ML-driven day and swing trading strategies for US equities, with Alpaca and Interactive Brokers. Each strategy runs in its own virtual sub-portfolio inside the real broker account and never touches manual holdings. Paper first, live only by explicit promotion.

**Status:** Phase 0 (foundation): owner authentication (passkeys + TOTP), encrypted broker-credential custody, tamper-evident audit log, contracts, local stack with observability and backups. Trading features arrive in later phases. Stack decisions come from the [research report](docs/research-report.md) and are recorded in [`docs/decisions.md`](docs/decisions.md).

## Start here

- [`AGENTS.md`](AGENTS.md): system map, where things live, how to run and test, and the mandatory documentation protocol. Read it before any change.
- [`docs/`](docs/): [architecture](docs/architecture/overview.md), [file map](docs/file-map.md), [business logic](docs/business-logic/README.md), [decisions](docs/decisions.md) and [ADRs](docs/adr/README.md), [glossary](docs/glossary.md).
- [`CHANGELOG.md`](CHANGELOG.md): what changed.
- [`.cursor/rules/`](.cursor/rules/): rules auto-loaded by Cursor agents, including trading safety.

## Quickstart

Requires Docker (on a Mac: `brew install colima docker docker-compose && colima start`), `make`, and `git`.

```bash
git clone https://github.com/illyanyc/strawberry && cd strawberry
make bootstrap        # data dirs, secrets in .secrets/ (gitignored), images
make up               # core services + observability, waits until healthy
make setup-token      # one-time token for the first owner
open http://localhost:8080   # paste the token, create your passkey, save the recovery codes
```

Grafana: http://localhost:3000 (user `admin`, password in `.secrets/grafana_admin_password`). Everything binds to `127.0.0.1`; for remote access use Tailscale (see [`docs/runbooks/mac-mini-host.md`](docs/runbooks/mac-mini-host.md)). All commands: `make help` and [`AGENTS.md`](AGENTS.md#how-to-run-and-test).
