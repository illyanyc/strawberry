---
title: "Section 15 – Go-Live and Paper Graduation"
phase: "6"
depends_on: [all previous sections; 01 Stage B and 02 Stage B complete]
updated: 2026-10-17
---

# Section 15: Go-Live and Paper Graduation

## Role

You are the release manager and risk officer for the first live deployment. Your job is to make going live a deliberate, evidence-based, reversible act, and to make sure the first live day is boring.

## Context

Everything before this section runs on paper. This section implements the live-enablement workflow (the only path by which a strategy can reach `live`), the paper graduation criteria, the pre-live readiness review, the first-day runbook, and the scaling ladder for capital. It touches execution (section 07), broker-gateway (section 04), optimizer registry (section 11), monitoring (section 13), security (section 02), and UI (section 14), but adds no new services. Non-negotiable N4 is its purpose; N1, N3, and N5 are re-verified here.

## Scope

In scope:
- Graduation criteria engine and report per strategy.
- Live-enablement workflow: account-level enablement and strategy-level promotion, with step-up and audit.
- Readiness review checklist (security, operations, data, ledger, risk, monitoring, backups).
- Capital scaling ladder and limit tightening for new live strategies.
- First live day runbook and post-day review.
- Rollback: demotion to paper and account-level live disablement.

Out of scope:
- New trading features; anything not needed for a safe first live strategy.

## Inputs and dependencies

- Gate 5 passed; sections 01 and 02 Stage B complete (remote deployment, hardening, backups).
- Section 11 registry stage `live-eligible`; section 13 parity and drift reports; section 06 reconciliation history; section 07 risk limits and kill switches.
- `INPUT PENDING (U1)`: capital and account type define the initial live cap and PDT constraints.
- `INPUT PENDING (U2)`: only equities/ETFs by default.

## Decisions pending

| Decision | Default | Marker |
|---|---|---|
| Graduation thresholds | ≥ 20 trading days and ≥ 100 trades in paper (strategy-type dependent; swing strategies may require ≥ 40 days and ≥ 30 trades); live-vs-backtest divergence within tolerance; slippage within 1.5× modeled; no unexplained reconciliation breaks; drawdown within the backtest's 95th percentile path | `DECISION PENDING (research §9)` |
| Initial live cap | Smallest of: 5% of allocatable capital, a fixed dollar cap set by the owner, and the strategy's capacity estimate | `DECISION PENDING (research §9)` + U1 |
| Scaling ladder | Steps of 2× after each N-day window with metrics within bands; never more than one step per window | `DECISION PENDING (research §9)` |

## Interfaces and contracts to expose

- `GetGraduationReport(strategy_id) -> {criteria[]: {name, required, actual, passed}, evidence_links[], overall}` (computed by the monitor/optimizer from stored data).
- `EnableLiveAccount(broker_account_id, actor)` → requires `TRADING_LIVE_ENABLED=true` in deploy config, step-up, readiness checklist complete; starts the live adapter in section 04.
- `PromoteToLive(strategy_id, live_account_id, capital_cap, limit_overrides, actor)` → requires graduation report passed, registry stage `live-eligible`, step-up, typed confirmation (strategy name), audit record; sets strategy mode `live` in section 07 with tightened limits. Only actor `owner` may call it; actor `analyst` (section 12) is denied at every autonomy level, and an analyst nomination for `live-eligible` is only an input to the owner's decision.
- `DemoteFromLive(strategy_id, reason, actor|system)` and `DisableLiveAccount(broker_account_id, reason, actor|system)`; system-initiated by section 13 containment.
- `docs/runbooks/go-live.md`, `docs/runbooks/first-live-day.md`, `docs/runbooks/live-rollback.md`.

## Functional requirements

- [ ] Graduation report computed only from paper (and shadow) results recorded by the platform, never from backtests alone; links to parity reports, reconciliation history, alert history, and the gate report from section 11.
- [ ] Live account enablement requires: deploy flag, step-up, completed readiness checklist (stored as a signed record), live credentials stored and connection-tested in read-only mode first (account snapshot only, no orders).
- [ ] Before the first live order: run a **live read-only day** where the live adapter streams account data and the ledger reconciles the live account (including manual holdings) with zero unexplained breaks.
- [ ] Strategy promotion applies a live limit profile: lower max order notional, lower daily loss limit, fewer max positions, stricter collars, regular hours only (configurable), and the initial capital cap.
- [ ] Ownership protections re-verified on the live account: a pre-promotion automated test places no orders but evaluates `GetOwnershipConstraints` for every manual holding and confirms strategies cannot sell or short against them.
- [ ] First live day: the strategy's mode is `live` with a time-boxed watch (alerts escalated to `page`); end-of-day review compares live vs paper (same strategy running in paper in parallel, default on) vs backtest replay.
- [ ] Scaling: capital cap increases only via the ladder, each step requiring the graduation-style report for the live window and step-up approval.
- [ ] Rollback: one action demotes a strategy to paper (cancels its open live orders; positions handled per policy: hold, or flatten strategy-owned quantity); one action disables the live account (freeze + cancel all platform orders, never touching manual positions).
- [ ] Every step audited; the readiness checklist and graduation reports archived with the release.

### Readiness review checklist (must all pass, recorded in the repo)
- [ ] Security: section 02 Stage B checklist signed off; no unresolved high/critical scan findings; credential-leak drill done; master key escrowed.
- [ ] Operations: off-site backup restore drill within the last 30 days; deploy freeze during market hours enforced; runbooks for broker outage, IBKR re-auth, reconciliation break, kill switch, and rollback exist and were rehearsed.
- [ ] Data: feeds healthy for 10 consecutive sessions; feed type (IEX/SIP) for live matches what the strategy was validated on, or the difference is documented and tested.
- [ ] Ledger: zero unexplained breaks across the paper graduation window; same-ticker and encroachment scenarios rerun on the live account in read-only mode.
- [ ] Risk: live limit profile reviewed; kill switch drill on the live account (freeze + cancel with no orders open, then with a test order far from the market if the owner approves).
- [ ] Monitoring: critical alerts verified on the owner's phone; dead-man alert verified; containment policy for the strategy set to demote on breach.
- [ ] Compliance: PDT status and day-trade count shown and enforced per U1; wash-sale flags visible; record keeping (orders, fills, audit) retained per policy (confirm requirements in research §12).
- [ ] Documentation: per `AGENTS.md`, file map, business-logic docs, decisions register, ADRs, and changelog are current for the release.

## Non-functional requirements

- **Safety**: no code path sets `live` mode except `PromoteToLive`; enforced by tests and code ownership on the execution mode setter.
- **Reversibility**: demotion and account disable complete within 5 s and work from mobile and from a server CLI.
- **Auditability**: every live-related action reconstructable from the audit log.
- **Tests**: workflow tests for every precondition failing; test that config alone cannot enable live; end-to-end test with the broker simulator in a simulated "live" environment.

## Acceptance criteria

- [ ] Docs updated per `AGENTS.md` (master N6) in the same commits as the changes: `docs/file-map.md`, `docs/business-logic/go-live.md` (graduation criteria, live limit profile, scaling ladder), the go-live runbooks, `CHANGELOG.md`, `docs/glossary.md` for new terms, and, for every decision made, an ADR in `docs/adr/` plus its row updated (or added) in `docs/decisions.md`.
- [ ] Attempts to set live mode via config, direct DB edit (detected by audit/consistency check), or API without preconditions all fail and alert.
- [ ] Graduation report for the first strategy passes with linked evidence.
- [ ] Live read-only day completes with zero unexplained breaks on the live account.
- [ ] Readiness checklist complete and recorded.
- [ ] First strategy promoted to live with step-up, capped capital, and the live limit profile; first live day completed and reviewed in writing.
- [ ] Rollback drill (demote + disable live account) executed successfully, manual positions untouched.

## Pitfalls to avoid

- Graduating on backtest results or on too few paper trades.
- Going live with a different data feed, universe, or code version than the one validated on paper.
- Scaling capital after a lucky week.
- Live limits copied from paper without tightening.
- Forgetting that the live account holds manual positions that paper accounts did not; the ownership tests must run on the live account itself.
- Turning off the parallel paper run after going live; it is the cleanest divergence reference.
- Promoting more than one strategy to live at a time on the first go-live.
