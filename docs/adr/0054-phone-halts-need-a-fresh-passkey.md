# ADR-0054: A phone halts after a fresh passkey assertion; a desktop halts with one confirmation; only a desktop resumes

- **Status:** Accepted
- **Date:** 2026-10-18
- **Decision register:** D72 (refines UI-006 for phones; keeps RM-007)
- **Related:** RM-020, RM-006, RM-007, UI-050, UI-006, UI-015, SE-003, [ADR-0025](0025-oms-risk-gate-and-kill-switches.md), section 14 prompt (mobile), master prompt Gate 5, research 07 §4

## Context

Phase 2 made engaging a kill switch one confirmation from any client (UI-006) and releasing it desktop-only with a fresh passkey (RM-007). Gate 5 and research 07 §4 specify the phone path: "the phone can halt (freeze, cancel, flatten strategy-owned) after a fresh passkey assertion", and "re-enabling trading is desktop-only, after step-up". A halt is risk-reducing, but `flatten_strategy_owned` realizes strategy P&L, and an unlocked phone is the device most likely to be lost or borrowed. On the phone the passkey is a biometric prompt of about a second.

## Decision

- From a phone (`Sec-CH-UA-Mobile` or the user agent, the same detection as UI-015 and RM-007), `killswitch.engage` escalates to a passkey step-up: `policy.Authorize` with `Resource.Mode = "mobile"`. A fresh passkey step-up (within the 5-minute window, SE-003) satisfies it; TOTP or recovery step-ups do not. The UI's step-up dialog opens on the `step_up_required` answer and retries the engagement once (UI-002), so the phone flow is: tap, confirm, passkey.
- From a desktop, engaging stays one confirmation, no step-up (UI-006).
- Release stays desktop-only with a fresh passkey (RM-007); phones get `desktop_required`.
- Phone order entry stays behind `mobile.order_entry_enabled` (U6, UI-015), enabling it needs a passkey.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep one confirmation on phones | Contradicts Gate 5 and research 07 §4; a borrowed unlocked phone could flatten every strategy |
| Passkey only for `flatten` on phones | Simpler for freeze, but splits the rule by action; the gate names all three |
| Passkey on desktop too | Adds friction where the device is under the owner's control; RM-006's "always reachable" intent |

## Consequences

- Positive: the phone can halt everything in three interactions with proof of presence; resumption still needs the desktop.
- Negative / risks: if the phone's passkey is unavailable, halting must be done from a desktop, `tbctl kill engage` on the host, or automatically (risk limits, dead-man, monitoring containment).
- Follow-up: none.

## Validation

`TestRM020MobileKillEngageNeedsFreshPasskey` (policy), `TestRM007KillSwitchEngageAndDesktopPasskeyRelease` (gateway: phone without step-up or with TOTP is refused before execution; with a passkey it reaches execution with `mobile=true`; desktop engages without step-up), `phase5.test.tsx` (`UI-050`: step-up then automatic retry), and the Playwright mobile journey.
