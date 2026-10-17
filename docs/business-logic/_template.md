# <Domain name>

<!-- Copy this file to docs/business-logic/<domain>.md. Keep every section; write "None yet" rather than deleting. -->

**Prefix:** `XX-`  ·  **Owners:** <services/libs that enforce these rules>  ·  **Related:** <links to other domain docs, ADRs>

## Scope

What this domain covers and, explicitly, what it does not (link the neighbor doc that does).

## Concepts and states

Key entities, their fields that matter for the rules, and state machines (Mermaid `stateDiagram-v2` if non-trivial).

## Rules

| ID | Rule | Status | Code / tests |
|---|---|---|---|
| XX-001 | <Actor> MUST / MUST NOT <action> when <condition>. Limit: `<config.key>` (default `<value>`). | Specified | — |

## Invariants

Properties that always hold. Each names how it is checked (property test, continuous check, reconciliation).

| ID | Invariant | Checked by |
|---|---|---|
| XX-I1 | <statement> | <test / runtime check> |

## Edge cases

| Scenario | Expected behavior | Rules |
|---|---|---|
| <concrete situation> | <exact outcome> | XX-001 |

## Open questions

Undecided behavior, with the decision or input it waits on (`DECISION PENDING Dn`, `INPUT PENDING Un`). Never resolve these silently in code.
