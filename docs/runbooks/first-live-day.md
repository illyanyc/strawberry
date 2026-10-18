# First live day

Follow [go-live.md](go-live.md) through promotion before the session. One strategy, at the capital cap, with the live limit profile (GL-023).

## During the session

- The strategy's containment policy stays at demote-on-breach (MN-034). Do not loosen it today.
- The paper twin is the divergence reference. Leave it running (GL-022).
- Critical alerts page. A kill switch from the phone still needs a passkey (RM-020); it cancels platform orders and does not touch manual holdings.
- New intents stop if data is stale, the broker session drops, reconciliation breaks, or the kill switch is on. Do not override that.

## After the close

Write down, in the audit notes or the day's journal:

- live fills versus the paper twin versus the backtest replay of the same session
- implementation shortfall against the modeled cost
- any rejected intents and the check that rejected them
- reconciliation: zero unexplained breaks, manual quantity unchanged
- whether the capital cap should stay for the next ladder window (at least 10 sessions, GL-040) or the strategy should be demoted ([live-rollback.md](live-rollback.md))

A lucky day is not a reason to raise the cap.
