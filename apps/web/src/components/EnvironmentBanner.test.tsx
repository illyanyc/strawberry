import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EnvironmentBanner } from "./EnvironmentBanner";

describe("EnvironmentBanner", () => {
  it("labels LIVE in text, not only by color (N4, accessibility)", () => {
    render(<EnvironmentBanner mode="live" environment="local" />);
    const banner = screen.getByRole("status");
    expect(banner).toHaveTextContent("LIVE TRADING ENABLED");
    expect(banner).toHaveAttribute("data-mode", "live");
  });

  it("shows PAPER calmly and a connecting state when unknown", () => {
    const { rerender } = render(<EnvironmentBanner mode="paper" />);
    expect(screen.getByRole("status")).toHaveTextContent("PAPER");
    rerender(<EnvironmentBanner mode={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("CONNECTING");
  });
});
