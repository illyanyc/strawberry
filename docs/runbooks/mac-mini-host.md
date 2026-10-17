# Runbook: Mac Mini host

Primary host setup and recovery (D21, D28, D29, D17; ADR-0013, ADR-0016).

## First-time setup
1. macOS: enable FileVault; create the owner account; install Homebrew.
2. `brew install colima docker docker-compose uv tailscale make`.
3. Tailscale (open-source daemon, runs before login): `sudo brew services start tailscale && sudo tailscale up --ssh`. Enable tailnet MFA and restrict ACLs to your devices.
4. Clone the repo, then `infra/macos/install.sh` (dry run) and `infra/macos/install.sh --apply` (installs the Colima and stack LaunchDaemons, sets `pmset autorestart 1 sleep 0`).
5. `make bootstrap && make up`, then `make setup-token`.
6. Decide the hostname you will use **everywhere** (WebAuthn binds passkeys to it): for Tailscale, `tailscale serve --bg --https=443 http://127.0.0.1:8080` and put `TB_AUTH_RP_ID=<host>.<tailnet>.ts.net` and `TB_AUTH_ORIGINS=https://<host>.<tailnet>.ts.net` in `infra/compose/.env`; `make up`; open the `ts.net` URL and run setup there. Register two passkeys (laptop + phone) and an authenticator app.
7. Store `.secrets/backup_age_identity` and `.secrets/broker_kek` offline (runbook `secrets-and-keys.md`).
8. Put the Mac on a UPS.

## After a reboot or power loss
Nothing to do: `com.strawberry.colima` starts the VM at boot, `com.strawberry.stack` runs `make up`, containers restart. With FileVault, unlock first: `ssh <owner>@<host>.<tailnet>.ts.net` (Tailscale SSH at the pre-boot unlock screen is supported by `tailscaled`), or unlock at the console.

Check: `make ps`, Grafana "Stack overview", `tail ~/Library/Logs/strawberry-*.log`.

## Common problems
| Symptom | Action |
|---|---|
| `docker: Cannot connect` | `launchctl print system/com.strawberry.colima`; `colima status`; `colima start` manually to see errors |
| Stack not up after boot | `tail ~/Library/Logs/strawberry-stack.log`; run `make up` |
| Clock skew alert after sleep/wake | see `clock-skew.md` (`colima ssh -- sudo chronyc makestep`) |
| Disk alert | see `disk-full.md` |

## Upgrades
`brew upgrade colima docker` then `sudo launchctl kickstart -k system/com.strawberry.colima`. Pull new code, `make up` (images rebuild). Never restart trading services mid-session once execution exists (Phase 2 freeze rule).
