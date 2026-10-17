# Business logic

The specification of how the platform behaves, by domain. **These docs are the source of truth; code implements them.** A rule that exists only in code is a defect.

| Domain | Doc | Rule prefix | Main owners (planned) |
|---|---|---|---|
| Orders and execution | [orders-and-execution.md](orders-and-execution.md) | `OE-` | `execution`, `broker-gateway` |
| Virtual portfolios and attribution | [virtual-portfolios-and-attribution.md](virtual-portfolios-and-attribution.md) | `VP-` | `ledger`, `execution` |
| Risk management | [risk-management.md](risk-management.md) | `RM-` | `execution`, `monitor` |
| Strategies and model lifecycle | [strategies-and-model-lifecycle.md](strategies-and-model-lifecycle.md) | `SM-` | `ml`, `optimizer`, `backtest`, `execution`, `monitor` |
| Data and features | [data-and-features.md](data-and-features.md) | `DF-` | `marketdata`, `context-data`, `libs/py/tbcore` |
| Operations | [operations.md](operations.md) | `OP-` | `Makefile`, `scripts/`, `infra/`, CI |

New domain: copy [`_template.md`](_template.md), pick a two-letter prefix, add it to this table and to `.cursor/rules/documentation-discipline.mdc`.

## Writing rules

- **ID:** `<PREFIX>-<NNN>`, sequential, never reused. Retired rules stay in the table with status `Retired` and the reason.
- **Statement:** testable MUST / MUST NOT with the exact condition or limit. Configurable limits name the config key and default.
- **Status:** `Specified` (agreed, not built) → `Implemented` (code + tests exist) → `Retired`.
- **Code / tests:** path of the enforcement point and the test(s). Code cites the ID in a comment at that point; tests include the ID in their name.
- **Rules vs invariants:** a rule constrains an action ("reject a sell that..."); an invariant is a property of state that must always hold ("owner quantities sum to the broker position"). Invariants should have a continuous check or a property test.
- **Edge cases:** concrete scenarios with the expected outcome. Every edge case maps to at least one test once implemented.

Initial rules are seeded from the build plan's non-negotiables and are `Specified`. Defaults marked `INPUT PENDING` wait on owner answers and must stay configurable.
