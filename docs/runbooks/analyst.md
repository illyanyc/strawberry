# Runbook: AI Research Analyst

Alerts and escalations from the analyst (section 12) and its live feedback (section 13 slice). The analyst can only waste compute on paper: it cannot trade, see the holdout, or promote to live. Containment of live risk is the kill switch and demotion path ([kill-switch.md](kill-switch.md)), never this runbook.

## Analyst autonomy lowered (critical)

**Cause:** a critical live-feedback condition (decay together with below-p10 performance, or three conditions), a critical alert naming the strategy, repeated failed analyst rounds, or PBO rising above 0.5 (MN-025, AN-063). The lineage is at level 0 and its loop is paused.

1. Open **Research → AI analyst**, select the lineage, read the latest journal round and the linked `LiveFeedbackReport`.
2. If live risk is involved, handle it first with the strategy kill switch or demotion (the analyst never does this).
3. Decide whether the analyst should keep investigating: resume the loop (it stays at level 0, so every launch needs your approval), or leave it paused.
4. Raising the level again needs a passkey step-up and is audited.

## Analyst round escalated (warning)

**Cause:** the planner's output failed validation twice, the egress guard blocked a prompt, the LLM spend cap was reached, the provider was unreachable, or the lineage could not be read (`analyst.escalation`, kind `round_failed`).

1. The journal entry's reasoning names the cause.
2. Egress blocked: check the LLM call log for the finding (`ticker`, `calendar_date`, ...). A real leak is a bug in masking; never loosen the guard to get past it.
3. Spend cap: wait for the next day/month or raise `TB_MAX_LLM_USD_PER_DAY`/`_MONTH` deliberately; local providers keep working.
4. Provider unreachable: check `llm-egress` logs and `TB_LLM_EGRESS_ALLOW`; switch to local-only (`TB_LLM_LOCAL_ONLY=true`) if needed.

## Analyst loop stopped (info)

**Cause:** plateau, no edge, cycle budget spent, significance unreachable, or the round budget (`analyst.escalation`, kind `loop_stopped`). Read the summary and recommendation in the journal. A spent cycle needs you to open a new research cycle (section 11, step-up) before the analyst can launch again.

## Analyst budget at 80 % (warning)

**Cause:** trials per round, CPU hours per day, rounds, or LLM spend reached 80 % of its limit. No action is required; at 100 % launches are refused. Raise budgets only with step-up and a reason.

## Code proposal awaiting review

A candidate feature passed the AST policy, the sandbox, the point-in-time lint, determinism, and future-shift checks. Review the diff in the UI; approval only marks it ready. Merge it yourself in a normal pull request; native workers run only merged code.
