import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { setStepUpHandler } from "@/lib/api";
import { num, statusView } from "@/lib/monitoring";
import { AlertsCard, ContainmentView, DivergenceView, GraduationView, HealthCard } from "@/routes/monitoring";

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

describe("monitoring (UI-028..UI-031)", () => {
  it("maps every health status and formats protojson doubles", () => {
    expect(statusView("HEALTH_STATUS_BREACH")).toEqual({ tone: "danger", label: "breach" });
    expect(statusView("HEALTH_STATUS_INSUFFICIENT_DATA").label).toBe("insufficient data");
    expect(num("NaN")).toBe("—");
    expect(num(1.234, 1)).toBe("1.2");
  });

  it("shows a strategy's score, status, and components", () => {
    wrap(
      <ul>
        <HealthCard
          active={false}
          onSelect={() => undefined}
          h={{
            strategyId: "ml-a",
            score: 20,
            status: "HEALTH_STATUS_BREACH",
            mode: "halted",
            horizon: "intraday",
            sessions: 3,
            trades: 40,
            components: [{ name: "drawdown", status: "HEALTH_STATUS_BREACH", score: 20, detail: "drawdown 18% vs p95 6%" }],
          }}
        />
      </ul>,
    );
    expect(screen.getByText("ml-a")).toBeInTheDocument();
    expect(screen.getAllByText("breach").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("health score")).toHaveTextContent("20");
    expect(screen.getByLabelText("Drawdown vs backtest p95 score")).toBeInTheDocument();
  });

  it("lists daily divergence with recording completeness and graduation criteria", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const u = String(input);
      if (u.endsWith("/divergence"))
        return respond(200, {
          history: [{ reportId: "dv1", sessionDate: "2026-10-16", matchedIntents: 4, missedIntents: 1, parity: { tradeMatchRate: 0.8 }, recordingComplete: true }],
        });
      return respond(200, {
        evidence: {
          criteria: [
            { name: "sessions", required: ">= 20", actual: "5", passed: false },
            { name: "dsr_combined", required: ">= 0.95", actual: "computed by section 15", evidenceOnly: true },
          ],
        },
      });
    });
    wrap(
      <>
        <DivergenceView id="orb-15m" />
        <GraduationView id="orb-15m" />
      </>,
    );
    const table = await screen.findByRole("table", { name: "Divergence history" });
    expect(within(table).getByText("2026-10-16")).toBeInTheDocument();
    expect(within(table).getByText("complete")).toBeInTheDocument();
    const crit = await screen.findByRole("list", { name: "Graduation criteria" });
    expect(within(crit).getByText("not met")).toBeInTheDocument();
    expect(within(crit).getByText("section 15")).toBeInTheDocument();
  });

  it("acknowledges an alert and silences it after a passkey step-up (MN-042)", async () => {
    const stepUp = vi.fn().mockResolvedValue(true);
    setStepUpHandler(stepUp);
    let silences = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const u = String(input);
      if (u.includes("/silence")) {
        silences += 1;
        if (silences === 1) return respond(403, { error: "step_up_required", method: "passkey", action: "alerts.silence" });
        return respond(200, { silenceId: "sil-1" });
      }
      if (u.includes("/ack")) return respond(200, { alert: {} });
      void init;
      return respond(200, {
        alerts: [
          {
            alert: { dedupeKey: "ibkr-live-main-session", severity: "SEVERITY_CRITICAL", subject: "IBKR session needs login", message: "approve the push", runbookUrl: "docs/runbooks/ibkr-weekly-2fa.md" },
            status: "firing",
            count: 2,
            origin: "bus",
          },
        ],
      });
    });
    wrap(<AlertsCard />);
    expect(await screen.findByText("IBKR session needs login")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/ibkr-live-main-session/ack"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Silence…" }));
    fireEvent.change(screen.getByLabelText("Reason (audited)"), { target: { value: "approving on the phone" } });
    fireEvent.click(screen.getByRole("button", { name: "Silence (passkey)" }));
    await waitFor(() => expect(silences).toBe(2));
    expect(stepUp).toHaveBeenCalled();
    const body = JSON.parse(String(fetchMock.mock.calls.find(([u]) => String(u).includes("/silence"))?.[1]?.body));
    expect(body).toEqual({ durationSeconds: 3600, reason: "approving on the phone" });
  });

  it("saves a containment policy and shows automatic actions (MN-034..MN-036)", async () => {
    const puts: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const u = String(input);
      if (init?.method === "PUT") {
        puts.push(JSON.parse(String(init.body)));
        return respond(200, { policy: {}, loosened: false });
      }
      if (u.includes("/actions"))
        return respond(200, {
          actions: [{ actionId: "ca-1", kind: "CONTAINMENT_ACTION_KIND_DEMOTE", fromMode: "paper", toMode: "halted", executed: true, reason: "breach: drawdown 3 vs 1.5" }],
        });
      return respond(200, {
        inherited: true,
        policy: { enabled: true, drawdownWarningMultiple: 1, drawdownBreachMultiple: 1.5, breachAction: "CONTAINMENT_ACTION_KIND_DEMOTE", severeAction: "CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN" },
      });
    });
    wrap(<ContainmentView id="ml-a" />);
    expect(await screen.findByText(/Inherits the default policy/)).toBeInTheDocument();
    expect(await screen.findByText("paper → halted")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Drawdown breach (× p95)"), { target: { value: "1.2" } });
    fireEvent.click(screen.getByRole("button", { name: "Save policy" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect((puts[0] as { policy: { drawdownBreachMultiple: number } }).policy.drawdownBreachMultiple).toBe(1.2);
  });
});
