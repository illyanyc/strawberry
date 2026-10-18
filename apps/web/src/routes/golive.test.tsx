import { describe, expect, it } from "vitest";

import { liveRefusals, type LiveStatus, type Readiness } from "./golive";

const open: LiveStatus = {
  flagEnabled: true,
  accounts: [{ brokerAccountId: "alpaca-live", environment: "live", enabled: true, ordersEnabled: true }],
  candidates: [{ strategyId: "orb", overall: true }],
  liveStrategies: [],
};
const ready: Readiness = { complete: true, items: [{ id: "gl-flag", status: "passed" }] };

describe("UI-052 go-live refuses until every gate passes", () => {
  it("lists every closed gate and allows nothing through", () => {
    const reasons = liveRefusals({ flagEnabled: false, accounts: [], candidates: [], liveStrategies: [] }, { complete: false, items: [] });
    expect(reasons.length).toBeGreaterThan(0);
    expect(reasons.some((r) => r.includes("TB_TRADING_LIVE_ENABLED"))).toBe(true);
  });

  it("is quiet only when the flag, the trading stage, the checklist, and graduation have passed", () => {
    expect(liveRefusals(open, ready)).toEqual([]);
    expect(liveRefusals({ ...open, liveStrategies: [{ strategyId: "orb" }] }, ready).length).toBe(1);
  });
});
