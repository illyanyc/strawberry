# ADR-0043: Analyst coder sandbox, evaluator-side candidate checks, and review before merge

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D61
- **Related:** AN-070..AN-073, BT-064 (point-in-time lint), BT-081 (evaluator manifest), BT-082; ADR-0034; research [03 §2.3, §4](../research/03-optimization-and-ai-agent.md) (MLE-STAR leakage checker, METR reward hacking, sandbox on macOS)

## Context

Section 12 includes a coder role that writes feature code. LLM-written feature code is a prime look-ahead vector, and agents reward-hack when they can see the evaluator. Phase 3 left the analyst sandbox mounts open: the evaluator manifest says the sandbox must not mount evaluator paths, but nothing enforced it at runtime. Constraints: no Docker socket in any service (it would be root on the host), no network path between the sandbox and anything, native workers must run only reviewed code, and the analyst must not import evaluator modules (BT-081).

## Decision

1. **Scope:** a candidate is one pure feature module `compute(<series>) -> array` over open/high/low/close/volume.
2. **Static policy first** (`tb_assistant.ast_policy`): imports only `numpy` and `math`; no dynamic code, builtins that reach the interpreter, private or dunder access, classes, try/with, module-level statements, or numpy storage/random/process entry points; `compute` parameters must be declared inputs.
3. **File-based job protocol in a shared volume** (`analyst-jobs`), three containers, none with a network: the assistant writes `request.json` + `candidate.py`; the `candidate-checker` (backtest image, evaluator side) writes synthetic `inputs.npz` and `ready`; the `analyst-sandbox` runs the candidate and writes `outputs/` and `done`; the checker writes `verdict.json`.
4. **Synthetic inputs only.** Determinism and future-shift invariance are properties of the code, so the sandbox never receives lake data, real prices, or anything near the holdout. Real-data use happens only after a human merges the feature and it runs through the normal training path and leakage suite.
5. **Sandbox container:** its own minimal image (`infra/docker/analyst-sandbox.Dockerfile`: pinned Python slim + pinned numpy + the standalone `services/assistant/sandbox/worker.py`; no `tbcore`, no assistant, no evaluator code), `network_mode: none`, read-only root, non-root, no secrets, no lake, CPU/memory/pids limits; per job a child process with CPU-seconds, address-space, file-size, and wall-clock limits runs the candidate on the full series twice and on four prefixes.
6. **Evaluator-side verdict** (`tb_backtest.candidates`): point-in-time lint on the source (PIT001–PIT008), sandbox status, determinism (bit-identical runs), future shift (every prefix run equals the full run on that prefix), output sanity. The analyst sees only check names, pass/fail, and rule IDs or short messages.
7. **Review before merge.** A passing candidate becomes a `CodeProposal` (`AWAITING_REVIEW`) with a unified diff that only adds `libs/py/tbcore/src/tbcore/features/candidates/<name>.py`. Owner approval (`analyst.code.review`, via api-gateway) marks it approved for merge and audits it; nothing is applied, committed, or deployed automatically. Native workers execute only merged code.

## Alternatives considered

| Option | Why not |
|---|---|
| Docker-in-Docker or the Docker socket in the assistant to start per-job containers | Socket access is host root; one long-lived no-network worker gives the same isolation |
| Run candidates in the backtest container with the evaluator | Candidate code could read the evaluator from disk (reward hacking risk, BT-081) |
| Real lake data in the sandbox | Unnecessary for the checks and widens what agent code can see; holdout clipping would become a security boundary |
| Apple `container` VM-per-job | Needs macOS 26 and has no Compose integration yet (research 03 §4); revisit as hardening |
| Auto-merge passing candidates | Violates review-before-merge; checks catch leakage patterns, not every bad idea |

## Consequences

- Positive: the Phase 3 sandbox-mount gap is closed with an enforceable boundary; candidate code is checked for look-ahead without exposing data or the evaluator; humans stay in the merge loop.
- Negative / risks: the static policy restricts feature expressiveness (no pandas/polars in candidates); the future-shift probe samples four prefixes, so a feature that only peeks at specific positions could slip through (the PIT lint and human review are the other walls).
- Follow-up: Compose services and image (infra change), CI image build and scan, file map.

## Validation

`services/assistant/tests/test_coder.py` (policy refusals, review rules, loop integration), `tests/python/test_analyst_sandbox.py` (good, leaky, centered, stateful, and spinning candidates through the real checker and worker; PIT lint; synthetic inputs), and the Compose structure test (no network, no evaluator or lake mounts, no secrets).
