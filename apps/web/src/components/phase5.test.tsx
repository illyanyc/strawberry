import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { paletteEntries, type PaletteActions } from "@/components/CommandPalette";
import { EngageKillDialog } from "@/components/KillSwitch";
import { toMarkers, withAlpha } from "@/components/PriceChart";
import { StatusStrip } from "@/components/StatusStrip";
import { setStepUpHandler, type TradingStatus } from "@/lib/api";
import { compute, ema, nyDay, sessionVwap, sma, type Candle } from "@/lib/indicators";
import { matchNav, mobilePrimary, NAV, navByKey } from "@/lib/nav";
import { b64urlToBytes, deviceLabel } from "@/lib/push";
import { handleKey, isTypingTarget } from "@/lib/shortcuts";
import type { Signal } from "@/lib/trading";
import { scoreSeries, signalMarkers, tradingViewUrl, tradingViewWidgetUrl } from "@/routes/charts";
import { nyDayStartNs, ownerColor } from "@/routes/overview";
import { alertTemplate } from "@/routes/tradingview";

beforeAll(() => {
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});

afterEach(() => {
  vi.restoreAllMocks();
  setStepUpHandler(null);
});

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("navigation registry (UI-041)", () => {
  it("has unique paths and shortcut keys, at most four phone tabs, and palette matching by keyword", () => {
    expect(new Set(NAV.map((n) => n.to)).size).toBe(NAV.length);
    const keys = NAV.filter((n) => n.key).map((n) => n.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(mobilePrimary().length).toBeLessThanOrEqual(4);
    expect(navByKey("c")?.to).toBe("/charts");
    expect(matchNav("kill")[0]?.to).toBe("/risk");
    expect(matchNav("eod").map((n) => n.to)).toContain("/overview");
    expect(matchNav("zzzz")).toEqual([]);
  });
});

describe("keyboard shortcuts (UI-042)", () => {
  const handlers = () => ({ palette: vi.fn(), help: vi.fn(), go: vi.fn(), toggleTheme: vi.fn() });
  const key = (k: string, extra: Partial<KeyboardEvent> = {}) => ({ key: k, metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, target: document.body, ...extra });

  it("opens the palette with Ctrl/⌘K anywhere and navigates with g <key>", () => {
    const h = handlers();
    const st = { pendingG: 0 };
    expect(handleKey(key("k", { ctrlKey: true }), h, st, 0)).toBe(true);
    expect(h.palette).toHaveBeenCalled();
    expect(handleKey(key("g"), h, st, 1000)).toBe(true);
    expect(handleKey(key("p"), h, st, 1500)).toBe(true);
    expect(h.go).toHaveBeenCalledWith("/portfolio");
    // The sequence times out.
    handleKey(key("g"), h, st, 3000);
    expect(handleKey(key("p"), h, st, 5000)).toBe(false);
    expect(h.go).toHaveBeenCalledTimes(1);
    expect(handleKey(key("?"), h, st, 6000)).toBe(true);
    expect(handleKey(key("T", { shiftKey: true }), h, st, 7000)).toBe(true);
    expect(h.help).toHaveBeenCalled();
    expect(h.toggleTheme).toHaveBeenCalled();
  });

  it("ignores plain keys while typing", () => {
    const input = document.createElement("input");
    const h = handlers();
    expect(isTypingTarget(input)).toBe(true);
    expect(handleKey(key("g", { target: input }), h, { pendingG: 0 }, 0)).toBe(false);
    expect(handleKey(key("k", { target: input, metaKey: true }), h, { pendingG: 0 }, 0)).toBe(true);
  });

  it("palette: a ticker-like query offers its chart; the kill-switch entry only opens the confirmation", () => {
    const a: PaletteActions = { go: vi.fn(), toggleTheme: vi.fn(), theme: "dark", openKillSwitch: vi.fn(), logout: vi.fn() };
    const e = paletteEntries("aapl", a);
    expect(e[0]?.label).toBe("Open AAPL chart");
    e[0]!.run();
    expect(a.go).toHaveBeenCalledWith("/charts", { symbol: "AAPL" });
    const kill = paletteEntries("kill", a).find((x) => x.id === "act:kill")!;
    kill.run();
    expect(a.openKillSwitch).toHaveBeenCalled();
  });
});

describe("indicators (UI-049, display only)", () => {
  const c: Candle[] = [1, 2, 3, 4, 5, 6].map((v, i) => ({ time: 1_700_000_000 + i * 900, open: v, high: v + 1, low: v - 1, close: v, volume: 10 }));

  it("computes SMA, EMA, and session VWAP", () => {
    expect(sma(c, 3).map((p) => p.value)).toEqual([2, 3, 4, 5]);
    const e = ema(c, 3);
    expect(e[0]!.value).toBe(2);
    expect(e[1]!.value).toBeCloseTo(3);
    // Typical price (h + l + c) / 3 equals close here, so VWAP is the running mean of closes.
    expect(sessionVwap(c, () => "d").map((p) => p.value)).toEqual([1, 1.5, 2, 2.5, 3, 3.5]);
    expect(sessionVwap(c, (t) => (t < 1_700_002_000 ? "a" : "b"))[3]!.value).toBe(4);
    expect(compute("sma20", c)).toEqual([]);
    expect(nyDay(1_760_000_000)).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("signal overlays (UI-049)", () => {
  const sig = (id: string, dir: string, extra: Partial<Signal> = {}): Signal =>
    ({ signalId: id, strategyId: "ml-lgbm", modelVersion: "lgbm@3", instrumentId: "I1", asOf: "1760000000000000000", direction: dir, score: 0.42, probability: 0, horizon: "0", featureSnapshotId: "", correlationId: "", ...extra }) as Signal;

  it("labels every marker in text, de-duplicates by signal ID, and plots only model scores", () => {
    const m = signalMarkers([
      sig("a", "DIRECTION_LONG"),
      sig("a", "DIRECTION_LONG"),
      sig("a2", "DIRECTION_LONG", { asOf: "1760000000900000000" }),
      sig("b", "DIRECTION_FLAT", { asOf: "1759990000000000000" }),
      sig("c", "DIRECTION_SHORT", { strategyId: "tv-x", modelVersion: "tradingview", score: 0 }),
      sig("d", "DIRECTION_FLAT", { strategyId: "tv-x", modelVersion: "tradingview", score: 0 }),
    ]);
    // A model's flat decisions live in the score pane only; webhook exits are markers.
    // A repeated model decision keeps its marker but is labelled only once.
    expect(m.map((x) => x.text)).toEqual(["ml-lgbm long 0.42", "tv-x short", "tv-x flat", ""]);
    const shapes = toMarkers(m, 900).map((x) => x.shape);
    expect(shapes).toEqual(["circle", "arrowDown", "square", "circle"]);
    const s = scoreSeries([sig("a", "DIRECTION_LONG"), sig("c", "DIRECTION_SHORT", { modelVersion: "tradingview", strategyId: "tv-x" })]);
    expect(s.map((x) => x.id)).toEqual(["ml-lgbm score"]);
    expect(withAlpha("#3fb27f", 0.45)).toBe("#3fb27f73");
  });

  it("deep links to tradingview.com and the widget carry only the symbol and interval", () => {
    expect(tradingViewUrl("BRK.B", "1h")).toBe("https://www.tradingview.com/chart/?symbol=BRK.B&interval=60");
    const w = new URL(tradingViewWidgetUrl("AAPL", "1d", "dark"));
    expect(w.origin).toBe("https://s.tradingview.com");
    expect(w.searchParams.get("symbol")).toBe("AAPL");
    expect(w.searchParams.get("interval")).toBe("D");
  });
});

describe("status strip (UI-013)", () => {
  it("never shows healthy when execution or marketdata is unreachable, and states feed staleness in text", () => {
    const base: TradingStatus = { environment: "local", live_enabled: false, mode: "paper", server_time: new Date().toISOString(), session_expires: "", trading: null, feeds: null };
    const { rerender } = render(<StatusStrip status={base} />);
    expect(screen.getByText("Execution: unknown")).toBeInTheDocument();
    expect(screen.getByText("Feeds: unknown")).toBeInTheDocument();
    rerender(
      <StatusStrip
        status={{
          ...base,
          feeds: { feeds: [{ feed: "alpaca.sip", connected: true, lastMessageAt: String(BigInt(Date.now() - 3000) * 1_000_000n), lagMs: "10", symbolsStale: ["X", "Y"], state: "streaming" }] },
        }}
      />,
    );
    expect(screen.getByText(/ALPACA\.SIP .* ago · 2 stale/)).toBeInTheDocument();
  });
});

describe("portfolio overview (UI-046)", () => {
  it("starts the end-of-day window at midnight New York and keeps manual gray", () => {
    const ns = BigInt(nyDayStartNs(new Date("2026-10-16T18:30:00Z")));
    expect(new Date(Number(ns / 1_000_000n)).toISOString()).toBe("2026-10-16T04:00:00.000Z");
    expect(ownerColor("manual", ["orb"])).toBe("bg-subtle");
    expect(ownerColor("orb", ["orb"])).not.toBe("bg-subtle");
  });
});

describe("PWA and Web Push helpers (UI-044)", () => {
  it("decodes VAPID keys and labels devices without identifiers", () => {
    expect([...b64urlToBytes("AQID_-8")]).toEqual([1, 2, 3, 255, 239]);
    expect(deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 Version/19.0 Mobile/15E148 Safari/604.1")).toBe("Safari on iOS");
    expect(deviceLabel("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/537.36 Chrome/141.0 Safari/537.36")).toBe("Chrome on macOS");
  });

  it("the TradingView alert template carries placeholders, never a secret (SE-064)", () => {
    const t = JSON.parse(alertTemplate()) as Record<string, string>;
    expect(t.ticker).toBe("{{ticker}}");
    expect(t.time).toBe("{{timenow}}");
    expect(t.secret).toMatch(/make tv-secret/);
  });
});

describe("phone halt (UI-050, RM-020)", () => {
  it("a phone engagement answered with step_up_required runs the passkey step-up and retries once", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(respond(403, { error: "step_up_required", method: "passkey", action: "killswitch.engage" }))
      .mockResolvedValueOnce(respond(200, { killSwitch: { eventId: "K1" } }));
    const stepUp = vi.fn().mockResolvedValue(true);
    setStepUpHandler(stepUp);
    const onClose = vi.fn();
    wrap(<EngageKillDialog open onClose={onClose} target={{ scope: "global", label: "all trading" }} />);
    fireEvent.click(screen.getByRole("radio", { name: /close strategy-owned positions/ }));
    fireEvent.click(screen.getByRole("button", { name: "Engage kill switch" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(stepUp).toHaveBeenCalledWith("passkey", "killswitch.engage");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String((fetchMock.mock.calls[1]![1] as RequestInit).body))).toMatchObject({ scope: "global", action: "flatten" });
  });
});
