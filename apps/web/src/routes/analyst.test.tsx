import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { label, prettyDiff } from "@/lib/analyst";
import { setStepUpHandler } from "@/lib/api";
import { JournalCard, LedgerCard, LoopCard, ProposalsCard } from "@/routes/analyst";

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

const round = {
  roundId: "R1",
  lineageId: "L1",
  seq: 1,
  trigger: "ROUND_TRIGGER_RUN_COMPLETED",
  decision: "ROUND_DECISION_AWAITING_APPROVAL",
  autonomyLevel: 0,
  startedAt: "1760900000000000000",
  summary: "",
  reasoning: "rule-based",
  provider: "fake",
  model: "heuristic-v1",
  promptHashes: ["abcdef0123456789"],
  trialsAdded: 0,
  outcome: { bestDsr: 0.42, pbo: 0.31, lineageTrials: 12 },
  diagnosis: {
    summary: "Findings: overfitting",
    findings: [
      {
        findingId: "F1",
        category: "FINDING_CATEGORY_OVERFITTING",
        severity: "SEVERITY_HIGH",
        explanation: "Training score is far above validation score.",
        evidence: [{ reportId: "diag-run-1", metric: "learning_curve.gap", value: 0.35, context: "" }],
      },
    ],
  },
  plan: {
    actions: [
      {
        actionId: "A1",
        rank: 1,
        description: "Strengthen regularization",
        tool: "launch_ablation",
        status: "ACTION_STATUS_PROPOSED",
        statusReason: "",
        trialCost: 1,
        estComputeHours: 0.5,
        overfitRisk: "OVERFIT_RISK_LOW",
        configDiffJson: '{"hyperparams":{"lambda_l2":10}}',
      },
    ],
  },
};

describe("analyst helpers", () => {
  it("labels enums and pretty-prints config diffs for display only", () => {
    expect(label("ROUND_DECISION_STOP_NO_EDGE", "ROUND_DECISION_")).toBe("stop no edge");
    expect(prettyDiff('{"a":{"b":1}}')).toBe('{\n  "a": {\n    "b": 1\n  }\n}');
    expect(prettyDiff("not json")).toBe("not json");
  });
});

describe("JournalCard", () => {
  it("UI-018/UI-020: shows evidence-cited findings and the config diff, and approves selected actions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/journal")) return respond(200, { rounds: [round] });
      if (url.includes("/rounds/R1/review")) {
        const body = JSON.parse(String(init?.body));
        expect(body).toEqual({ actionIds: ["A1"], approve: true, reason: "" });
        return respond(200, { plan: round.plan, launches: [] });
      }
      return respond(404, {});
    });
    wrap(<JournalCard lineage="L1" />);
    expect(await screen.findByText("Training score is far above validation score.")).toBeInTheDocument();
    expect(screen.getByText(/learning_curve.gap=0.3500 \(diag-run-1\)/)).toBeInTheDocument();
    expect(screen.getByText(/"lambda_l2": 10/)).toBeInTheDocument();
    expect(screen.getByText("awaiting approval")).toBeInTheDocument();
    const reject = screen.getByRole("button", { name: "Reject selected" });
    fireEvent.click(screen.getByLabelText("Select action 1"));
    expect(reject).toBeDisabled(); // a rejection needs a reason
    fireEvent.click(screen.getByRole("button", { name: "Approve selected" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/rounds/R1/review"))).toBe(true));
  });

  it("UI-007: shows the empty state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { rounds: [] }));
    wrap(<JournalCard lineage="L1" />);
    expect(await screen.findByText(/No rounds yet/)).toBeInTheDocument();
  });
});

describe("LoopCard", () => {
  const status = {
    lineageId: "L1",
    state: "LOOP_STATE_IDLE",
    roundsCompleted: 2,
    roundsWithoutImprovement: 1,
    lastDecision: "ROUND_DECISION_CONTINUE",
    llmUsdToday: 0.12,
    llmUsdMonth: 1.5,
    budgetUsed: { trials: 1 },
    autonomy: { level: 0, paused: false, budget: { maxTrialsPerRound: 5 } },
  };

  it("UI-019, AN-061: raising autonomy goes through the passkey step-up and retries", async () => {
    let calls = 0;
    const stepUp = vi.fn(async () => true);
    setStepUpHandler(stepUp);
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      if (String(input).includes("/autonomy")) {
        calls += 1;
        expect(String(input)).toContain("/api/v1/research/lineages/L1/autonomy");
        expect(JSON.parse(String(init?.body))).toEqual({ level: 1 });
        if (calls === 1) return respond(403, { error: "step_up_required", method: "passkey", action: "analyst.autonomy.set" });
        return respond(200, { lineage: { lineageId: "L1", autonomyLevel: 1 } });
      }
      return respond(200, {});
    });
    wrap(<LoopCard status={status as never} />);
    fireEvent.click(screen.getByRole("radio", { name: /1 · Auto-run on paper/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/You will confirm with your passkey/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(calls).toBe(2));
    expect(stepUp).toHaveBeenCalledWith("passkey", "analyst.autonomy.set");
  });

  it("pauses the loop without a step-up", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(200, { lineages: [] }));
    wrap(<LoopCard status={status as never} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause loop" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toContain("/api/v1/analyst/lineages/L1/pause");
    expect(JSON.parse(String(init?.body)).paused).toBe(true);
  });
});

describe("LedgerCard", () => {
  it("UI-021: shows every trial with its source and the deflated outcome", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond(200, {
        lineageId: "L1",
        trials: [
          { trialId: "t1", source: "owner", status: "complete", sharpe: 0.05 },
          { trialId: "t2", source: "analyst", status: "pruned", sharpe: 0.01 },
        ],
        outcome: { lineageTrials: 2, effectiveTrials: 1.6, bestDsr: 0.2, pbo: 0.55 },
        cycleBudget: 30,
        cycleUsed: 2,
      }),
    );
    wrap(<LedgerCard lineage="L1" />);
    const table = await screen.findByRole("table", { name: "Trials" });
    expect(within(table).getByText("analyst")).toBeInTheDocument();
    expect(within(table).getByText("pruned")).toBeInTheDocument();
    expect(screen.getByText("0.200")).toBeInTheDocument();
    expect(screen.getByText("2 / 30")).toBeInTheDocument();
  });

  it("UI-007: shows the error state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(respond(503, { error: "assistant_unavailable", message: "assistant unavailable" }));
    wrap(<LedgerCard lineage="L1" />);
    expect(await screen.findByText("assistant unavailable")).toBeInTheDocument();
  });
});

describe("ProposalsCard", () => {
  it("AN-073: shows checks and the patch; approve marks it ready for a human merge", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/review")) return respond(200, {});
      return respond(200, {
        proposals: [
          {
            proposalId: "P1",
            name: "close_zscore",
            description: "Trailing z-score",
            status: "CODE_PROPOSAL_STATUS_AWAITING_REVIEW",
            patch: "+++ b/libs/py/tbcore/src/tbcore/features/candidates/close_zscore.py",
            checks: [
              { name: "ast_policy", passed: true, messages: [] },
              { name: "future_shift", passed: true, messages: [] },
            ],
          },
        ],
      });
    });
    wrap(<ProposalsCard lineage="L1" />);
    expect(await screen.findByText(/features\/candidates\/close_zscore.py/)).toBeInTheDocument();
    expect(screen.getByText("future_shift: pass")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve for merge" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => String(u).includes("/api/v1/analyst/proposals/P1/review"))).toBe(true));
  });
});
