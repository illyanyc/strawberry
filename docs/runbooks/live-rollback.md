# Live rollback

Demote and disable reduce risk. They need no step-up and work from a phone (GL-030, GL-031). They never sell manual holdings.

## Demote one strategy

Go live page, or `POST /api/v1/live/strategies/{id}/demote` with `{"reason":"…","position_policy":"hold"}`.

- `hold` (default): cancel the strategy's open live orders, leave its positions, return it to the paper account in mode `paper`.
- `flatten`: also send exits for **strategy-owned** quantity only, through the normal risk gate.

The monitor's automatic containment uses `DemoteStrategy` (live → paper through a latched strategy kill switch). Re-promotion is a new `PromoteToLive`, not a kill-switch release.

## Disable the live account

`POST /api/v1/live/accounts/{account}/disable` with a reason.

This turns enablement off, freezes the account (cancel platform orders, keep protective stops), and demotes every live strategy on it with policy `hold`. The adapter stops accepting orders immediately and stops the session on its next gate check.

## If mode live appears without a promotion

Execution demotes it on the next tick and pages (GL-050). Treat the alert as a real event: check the audit log for `execution.live.promote` versus a raw row change.
