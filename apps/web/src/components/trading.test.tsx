import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { EngageKillDialog } from "@/components/KillSwitch";
import { orderLines, toCandles, toMarkers } from "@/components/PriceChart";
import { OwnershipTable, RiskChecks, Signed } from "@/components/trading";
import { OrderTicketPage, ticketErrors } from "@/routes/order-ticket";
import { dec, decSign, enumLabel, nsToMs, type Bar, type InstrumentBreakdown, type Order } from "@/lib/trading";

beforeAll(() => {
  // jsdom lacks the modal dialog API.
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});

afterEach(() => vi.restoreAllMocks());

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const d = (value: string) => ({ value });

describe("trading helpers", () => {
  it("UI-008: decimals stay strings; enums and timestamps are converted for display only", () => {
    expect(dec(d("1234.5000"))).toBe("1234.5000");
    expect(dec(null)).toBe("—");
    expect(enumLabel("ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_")).toBe("partially filled");
    expect(nsToMs("1760900000123456789")).toBe(1760900000123);
    expect(nsToMs("0")).toBe(0);
    expect([decSign(d("-0.01")), decSign(d("0.000")), decSign(d("5"))]).toEqual([-1, 0, 1]);
  });

  it("shows P&L sign as text, not color alone", () => {
    render(
      <>
        <Signed d={d("12.50")} />
        <Signed d={d("-3.10")} />
      </>,
    );
    expect(screen.getByText("+12.50")).toBeInTheDocument();
    expect(screen.getByText("-3.10")).toBeInTheDocument();
  });
});

describe("OwnershipTable", () => {
  it("VP-001, N3: the same ticker shows manual and strategy quantities separately, as the ledger reports them", () => {
    const rows: InstrumentBreakdown[] = [
      {
        instrumentId: "I-AAPL",
        symbol: "AAPL",
        brokerQty: d("150"),
        ledgerQty: d("150"),
        owners: [
          { owner: "manual", qty: d("100") },
          { owner: "orb-15m", qty: d("50") },
        ],
        brokerLots: [],
      },
    ];
    render(<OwnershipTable instruments={rows} strategies={["orb-15m"]} />);
    const row = screen.getByText("AAPL").closest("tr")!;
    expect(within(row).getAllByText("150")).toHaveLength(2);
    expect(row.querySelector('[data-owner="manual"]')).toHaveTextContent("100");
    expect(row.querySelector('[data-owner="orb-15m"]')).toHaveTextContent("50");
    expect(row).not.toHaveTextContent("differs");
  });

  it("flags a broker/ledger difference", () => {
    render(
      <OwnershipTable
        instruments={[{ instrumentId: "I1", symbol: "MSFT", brokerQty: d("30"), ledgerQty: d("50"), owners: [], brokerLots: [] }]}
        strategies={[]}
      />,
    );
    expect(screen.getByText("(differs)")).toBeInTheDocument();
  });
});

describe("RiskChecks", () => {
  it("RM-003: lists every check with failures first", () => {
    render(
      <RiskChecks
        checks={[
          { name: "max_order_notional", passed: true, value: "1000", limit: "25000", detail: "" },
          { name: "ownership", passed: false, value: "-20", limit: "0", detail: "would sell manual holdings" },
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("ownership");
    expect(rows[0]).toHaveTextContent("fail");
    expect(rows[1]).toHaveTextContent("max_order_notional");
  });
});

describe("chart mapping", () => {
  const bar = (ts: string, close: string): Bar =>
    ({
    instrumentId: "I1",
    tsOpen: ts,
    open: d("1"),
    high: d("2"),
    low: d("0.5"),
    close: d(close),
    volume: d("10"),
    isFinal: true,
    feed: "alpaca.sip",
    symbol: "X",
  }) as unknown as Bar;

  it("UI-012: candles are sorted and the newest version of a bar wins", () => {
    const c = toCandles([bar("2000000000000", "1.5"), bar("1000000000000", "1.1"), bar("2000000000000", "1.7")]);
    expect(c.map((x) => [x.time, x.close])).toEqual([
      [1000, 1.1],
      [2000, 1.7],
    ]);
  });

  it("UI-012: fill markers snap to their bar; resting orders become price lines", () => {
    const m = toMarkers([{ timeNs: String(1_000_123n * 1_000_000_000n), side: "buy", text: "orb 10 @ 1" }], 900);
    expect(m[0]!.time).toBe(1_000_123 - (1_000_123 % 900));
    expect(m[0]!.position).toBe("belowBar");
    const lines = orderLines([
      { strategyId: "orb-15m", side: "SIDE_SELL", stopPrice: d("95"), limitPrice: null } as unknown as Order,
      { strategyId: "", side: "SIDE_BUY", stopPrice: null, limitPrice: d("101") } as unknown as Order,
    ]);
    expect(lines).toEqual([
      { price: "95", title: "orb-15m stop", kind: "stop" },
      { price: "101", title: "manual buy", kind: "limit" },
    ]);
  });
});

describe("EngageKillDialog", () => {
  it("UI-006, RM-006: engaging needs one confirmation and posts the scope and action", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { killSwitch: { eventId: "K1" } }));
    const onClose = vi.fn();
    wrap(<EngageKillDialog open onClose={onClose} target={{ scope: "global", label: "all trading" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Engage kill switch" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/kill-switches");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ scope: "global", target: "", action: "cancel_open", reason: "" });
  });
});

describe("Order ticket", () => {
  it("UI-015: validates shape locally", () => {
    const base = { broker_account_id: "a", instrument_id: "I1", side: "buy", quantity: "10", order_type: "limit", limit_price: "1.5", tif: "day", reason: "" } as const;
    expect(ticketErrors({ ...base })).toEqual([]);
    expect(ticketErrors({ ...base, quantity: "0" })).toHaveLength(1);
    expect(ticketErrors({ ...base, order_type: "limit", limit_price: "" })).toHaveLength(1);
    expect(ticketErrors({ ...base, order_type: "market", limit_price: "" })).toEqual([]);
  });

  it("UI-015: the preview shows the ownership note and failed checks, and a rejected preview cannot be submitted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/api/v1/system/status")) {
        return respond(200, {
          mode: "paper",
          server_time: new Date().toISOString(),
          trading: { globalState: "running", accounts: [{ brokerAccountId: "alpaca-paper", environment: "paper" }], strategies: [], activeKillSwitches: [], activeHalts: [] },
        });
      }
      if (url.startsWith("/api/v1/instruments/resolve")) return respond(200, { matches: [{ symbol: "AAPL", instrumentId: "I-AAPL" }], unknown: [] });
      if (url.startsWith("/api/v1/orders/preview")) {
        return respond(200, {
          decision: { approved: false, firstFailure: "ownership", checks: [{ name: "ownership", passed: false, value: "-20", limit: "0", detail: "exceeds manual quantity" }] },
          estimatedPrice: d("190.10"),
          estimatedNotional: d("1901.00"),
          ownershipNote: "this order affects manual holdings (owner: manual)",
        });
      }
      return respond(404, { error: "not_found" });
    });
    wrap(<OrderTicketPage />);
    await screen.findByRole("option", { name: "alpaca-paper (paper)" });
    fireEvent.change(screen.getByLabelText("Account"), { target: { value: "alpaca-paper" } });
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "aapl" } });
    fireEvent.click(screen.getByRole("button", { name: "Look up" }));
    await screen.findByText("instrument I-AAPL");
    fireEvent.change(screen.getByLabelText("Side"), { target: { value: "sell" } });
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "120" } });
    fireEvent.change(screen.getByLabelText("Limit price"), { target: { value: "190.10" } });
    fireEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText("this order affects manual holdings (owner: manual)")).toBeInTheDocument();
    expect(screen.getByText("exceeds manual quantity")).toBeInTheDocument();
    expect(screen.getByText("1901.00")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit paper order" })).toBeDisabled();
  });
});
