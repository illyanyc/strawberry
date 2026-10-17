# Per-service `AGENTS.md` convention

The root [`AGENTS.md`](../../AGENTS.md) explains the whole system. Each service, app, library, and the `infra/` tree gets its own `AGENTS.md` so an agent dropped into that directory can work without reading everything else.

## Rules

- **When:** create it in the same commit that creates the directory (`services/<name>/`, `apps/<name>/`, `libs/<lang>/<name>/`, `infra/`, `contracts/`). Update it whenever build, run, test, config, interfaces, or owned rules change.
- **Scope:** only what is specific to that directory. Link to root docs and business-logic rules instead of copying them.
- **Precedence:** the nearest `AGENTS.md` adds detail. It never overrides the root `AGENTS.md` or `.cursor/rules/trading-safety.mdc`.
- **Size:** aim for one screen. If a section grows, move detail to `docs/` and link it.
- **File map:** the service's own internal layout lives in its `AGENTS.md`; `docs/file-map.md` lists the service directory and links to it.

## Template

Copy into `<dir>/AGENTS.md` and fill every section; write `n/a` rather than deleting a section.

```markdown
# <service-name>

<One or two sentences: what this service owns and what it must never do.>

**Status:** Planned | In progress | Stable  ·  **Language/stack:** <per docs/decisions.md, ADR link>

## Responsibilities
- Owns: <data, processes, decisions>
- Does not own: <neighbors' scope, to prevent drift>

## Interfaces
| Direction | Kind | Name | Contract |
|---|---|---|---|
| in | bus subject / RPC / HTTP | `ord.intent` | `contracts/proto/...` |
| out | ... | ... | ... |

## Business rules enforced here
Rule IDs from docs/business-logic/ with the enforcement point:
- `RM-001` pre-trade risk gate → `<path/to/file>`

## Layout
| Path | Purpose |
|---|---|
| `cmd/` | entrypoints |

## Configuration
| Variable | Default | Notes |
|---|---|---|
| `TRADING_LIVE_ENABLED` | `false` | secrets come from the secrets interface, never env files in git |

## Run and test
- Run: `<command>`
- Test: `<command>` (unit), `<command>` (integration / contract)
- Fixtures: `<path>`

## Operational notes
Health/metrics endpoints, failure modes, fail-closed behavior, runbook links.

## Gotchas
Non-obvious facts a new agent would otherwise rediscover the hard way.
```
