import { describe, expect, it } from "vitest";

import { formatTime } from "./utils";

describe("formatTime (UI-008)", () => {
  it("formats RFC 3339 strings and UTC nanoseconds with a zone label", () => {
    const iso = formatTime("2026-10-17T20:00:00Z");
    expect(iso).toMatch(/2026/);
    expect(iso).toMatch(/[A-Z]{2,5}|GMT|UTC/);
    expect(formatTime(1_792_000_000_000_000_000)).toMatch(/2026/);
  });

  it("renders a dash for empty or invalid input", () => {
    expect(formatTime(null)).toBe("—");
    expect(formatTime("")).toBe("—");
    expect(formatTime("not a date")).toBe("—");
  });
});
