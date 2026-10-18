import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  curveSeries,
  draftDefaults,
  fmt,
  hyperparamErrors,
  hyperparamValues,
  type JobProgress,
  promotionTargets,
  schemaFields,
  STAGE_ORDER,
  tearsheetUrl,
  useProgressStream,
} from "@/lib/research";
import { CandidatePage, ExperimentsPage, LineagePage, StudyPage } from "@/routes/experiments";
import { RunPage, TrainingJobPage, TrainingPage } from "@/routes/training";

const LGBM_SCHEMA = JSON.stringify({
  type: "object",
  additionalProperties: false,
  properties: {
    num_leaves: { type: "integer", minimum: 2, maximum: 256, default: 15, description: "Maximum leaves per tree" },
    learning_rate: { type: "number", minimum: 0.001, maximum: 0.5, default: 0.05 },
    filter: { type: "string", enum: ["lgbm_binary", "logistic"], default: "lgbm_binary" },
    nested: { type: "object" },
  },
});

const family = (name: string, horizon = "intraday", schema = LGBM_SCHEMA) => ({
  family: name,
  tier: 1,
  task: "regression",
  description: `${name} description`,
  hyperparamSchemaJson: schema,
  cpuThreadsHint: 2,
  memoryMbHint: 512,
  supportsGpu: false,
  devices: ["cpu"],
  horizon,
  experimental: false,
});

type Handler = (body: unknown, url: URL) => unknown;
let routes: Record<string, Handler>;
let calls: { method: string; path: string; body: unknown }[];

function respond(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  routes = {};
  calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, body });
    const h = routes[`${method} ${url.pathname}`];
    if (!h) return respond(404, { error: "not_found", message: `${method} ${url.pathname}` });
    const out = h(body, url);
    return out instanceof Response ? out : respond(200, out);
  });
  vi.stubGlobal("EventSource", FakeEventSource);
  window.scrollTo = vi.fn() as typeof window.scrollTo;
  FakeEventSource.instances = [];
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  private listeners = new Map<string, ((e: MessageEvent<string>) => void)[]>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  emit(type: string, data: unknown = {}) {
    for (const fn of this.listeners.get(type) ?? []) fn(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
  close() {
    this.closed = true;
  }
}

function renderAt(path: string) {
  const root = createRootRoute({ component: Outlet });
  const app = createRoute({ getParentRoute: () => root, id: "app", component: Outlet });
  const pages = [
    ["/training", TrainingPage],
    ["/training/jobs/$jobId", TrainingJobPage],
    ["/training/runs/$runId", RunPage],
    ["/experiments", ExperimentsPage],
    ["/experiments/lineages/$lineageId", LineagePage],
    ["/experiments/studies/$studyId", StudyPage],
    ["/experiments/candidates/$candidateId", CandidatePage],
  ] as const;
  const router = createRouter({
    routeTree: root.addChildren([app.addChildren(pages.map(([p, c]) => createRoute({ getParentRoute: () => app, path: p, component: c })))]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("hyperparameter schema forms (UI-023)", () => {
  it("UI-023: renders the ML-011 subset, reports unsupported keywords, and validates ranges and types", () => {
    const { fields, unsupported } = schemaFields(LGBM_SCHEMA);
    expect(fields.map((f) => f.name)).toEqual(["num_leaves", "learning_rate", "filter"]);
    expect(unsupported).toEqual(["nested"]);
    const draft = draftDefaults(fields);
    expect(draft).toEqual({ num_leaves: "15", learning_rate: "0.05", filter: "lgbm_binary" });
    expect(hyperparamErrors(fields, draft)).toEqual({});
    expect(hyperparamErrors(fields, { ...draft, num_leaves: "1" }).num_leaves).toMatch(/≥ 2/);
    expect(hyperparamErrors(fields, { ...draft, num_leaves: "3.5" }).num_leaves).toMatch(/whole number/);
    expect(hyperparamErrors(fields, { ...draft, learning_rate: "abc" }).learning_rate).toMatch(/number/);
    expect(hyperparamErrors(fields, { ...draft, filter: "svm" }).filter).toMatch(/One of/);
    expect(hyperparamValues(fields, { ...draft, learning_rate: "" })).toEqual({ num_leaves: 15, filter: "lgbm_binary" });
    expect(schemaFields("not json").unsupported).toEqual(["schema is not valid JSON"]);
  });

  it("formats protojson doubles without inventing numbers", () => {
    expect(fmt("NaN")).toBe("NaN");
    expect(fmt(undefined)).toBe("—");
    expect(fmt(0.12345)).toBe("0.123");
    expect(fmt("12")).toBe("12");
  });
});

describe("registry stages (UI-026)", () => {
  it("UI-026, SM-004: the UI never offers live; synthetic candidates never reach live_eligible (EX-052)", () => {
    for (const s of [...STAGE_ORDER, "unknown" as const]) {
      for (const synthetic of [true, false]) {
        const t = promotionTargets(s, synthetic);
        expect(t).not.toContain("live");
        if (synthetic) expect(t).not.toContain("live_eligible");
      }
    }
    expect(promotionTargets("paper", false)).toEqual(["live_eligible"]);
    expect(promotionTargets("shadow", true)).toEqual(["paper"]);
  });
});

describe("progress streams (UI-023, D14)", () => {
  it("collects progress events and closes on done so a finished job is not reconnected", () => {
    const { result } = renderHook(() => useProgressStream<JobProgress>("/api/v1/research/training/jobs/J1/stream"));
    const es = FakeEventSource.instances[0]!;
    expect(es.url).toBe("/api/v1/research/training/jobs/J1/stream");
    act(() => {
      es.emit("open");
      es.emit("progress", { seq: "1", fold: 0, step: 1, metrics: { train_l2: 1.0, val_l2: 1.2 } });
      es.emit("progress", { seq: "2", fold: 0, step: 2, metrics: { train_l2: 0.9, val_l2: "NaN" } });
    });
    expect(result.current.events).toHaveLength(2);
    expect(curveSeries(result.current.events, 0)).toEqual([
      { name: "train_l2", points: [[1, 1.0], [2, 0.9]] },
      { name: "val_l2", points: [[1, 1.2]] },
    ]);
    act(() => es.emit("done"));
    expect(result.current.done).toBe(true);
    expect(es.closed).toBe(true);
  });
});

describe("Training page (UI-023)", () => {
  beforeEach(() => {
    routes["GET /api/v1/research/families"] = () => ({ families: [family("lgbm_regression"), family("lgbm_lambdarank", "swing")] });
    routes["GET /api/v1/research/feature-sets"] = () => ({ featureSets: [{ name: "core_intraday", version: 1, hash: "h", features: ["a", "b"], description: "core", maxLookback: 20 }] });
    routes["GET /api/v1/research/label-presets"] = () => ({ presets: [] });
    routes["GET /api/v1/research/lineages"] = () => ({ lineages: [] });
    routes["GET /api/v1/research/training/jobs"] = () => ({ jobs: [] });
    routes["GET /api/v1/research/runs"] = () => ({ runs: [] });
  });

  it("UI-023, ML-031: estimates before launch, refuses a stale estimate, and launches the estimated config", async () => {
    routes["POST /api/v1/research/training/estimate"] = () => ({ estimate: { rows: "1000", fits: 6, cpuSeconds: 12, gpuSeconds: 0, memoryMb: 300, overBudget: false, device: "cpu" }, errors: [] });
    routes["POST /api/v1/research/training/jobs"] = () => ({ job: { jobId: "JOB1", runId: "run_JOB1", status: "JOB_STATUS_QUEUED" } });
    routes["GET /api/v1/research/training/jobs/JOB1"] = () => ({ job: { jobId: "JOB1", runId: "run_JOB1", status: "JOB_STATUS_QUEUED", progress: 0, phase: "queued", config: { family: "lgbm_regression" } } });
    const router = renderAt("/training");
    const leaves = await screen.findByLabelText("num_leaves");
    fireEvent.click(screen.getByRole("button", { name: "Estimate" }));
    expect(await screen.findByText("1000")).toBeInTheDocument();
    // Editing after the estimate invalidates it: the button goes back to Estimate.
    fireEvent.change(leaves, { target: { value: "31" } });
    expect(screen.getByRole("button", { name: "Estimate" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Estimate" }));
    fireEvent.click(await screen.findByRole("button", { name: "Launch training" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/training/jobs/JOB1"));
    const submit = calls.find((c) => c.method === "POST" && c.path.endsWith("/training/jobs"));
    expect(submit?.body).toMatchObject({ config: { family: "lgbm_regression", timeframe: "15m", hyperparams: { num_leaves: 31 } }, confirmOverBudget: false });
  });

  it("UI-023: a field outside the schema range shows its message and blocks the estimate", async () => {
    renderAt("/training");
    fireEvent.change(await screen.findByLabelText("num_leaves"), { target: { value: "1000" } });
    expect(screen.getByText("Must be ≤ 256")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Estimate" })).toBeDisabled();
  });

  it("ML-031: an over-budget estimate needs an explicit confirmation, and server validation errors are shown", async () => {
    routes["POST /api/v1/research/training/estimate"] = () => ({ estimate: { rows: "9", fits: 6, cpuSeconds: 99999, memoryMb: 1, overBudget: true, device: "cpu" }, errors: [] });
    renderAt("/training");
    await screen.findByLabelText("num_leaves");
    fireEvent.click(screen.getByRole("button", { name: "Estimate" }));
    const confirm = await screen.findByLabelText(/Over the compute budget/);
    expect(screen.queryByRole("button", { name: "Launch training" })).toBeNull();
    fireEvent.click(confirm);
    expect(screen.getByRole("button", { name: "Launch training" })).toBeInTheDocument();

    routes["POST /api/v1/research/training/estimate"] = () => ({ estimate: null, errors: ["period ends after the holdout boundary 123 (ML-011, EX-010)"] });
    fireEvent.click(screen.getByRole("button", { name: "Re-estimate" }));
    expect(await screen.findByText(/period ends after the holdout boundary/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Launch training" })).toBeNull();
  });

  it("UI-023: the job page shows live progress from the stream", async () => {
    routes["GET /api/v1/research/training/jobs/JOB1"] = () => ({ job: { jobId: "JOB1", runId: "run_JOB1", status: "JOB_STATUS_RUNNING", progress: 0.1, phase: "fold", submittedAt: "0", config: { family: "lgbm_regression" } } });
    renderAt("/training/jobs/JOB1");
    expect(await screen.findByText("Job JOB1")).toBeInTheDocument();
    const es = FakeEventSource.instances.find((e) => e.url.endsWith("/training/jobs/JOB1/stream"));
    act(() => es?.emit("progress", { seq: "3", phase: "fold", fold: 1, nFolds: 4, step: 0, metrics: { val_ic: 0.04 }, log: "fold 2 done", progress: 0.5 }));
    expect(await screen.findByText("fold 2 done")).toBeInTheDocument();
    expect(screen.getByText("2 / 4")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });
});

describe("Run page (UI-023, UI-025)", () => {
  it("UI-025: the tearsheet opens as its own document; findings show critical first with evidence", async () => {
    routes["GET /api/v1/research/runs/RUN1"] = () => ({
      run: { runId: "RUN1", config: { family: "ridge", dataSource: "synthetic:intraday_edge", featureSet: { name: "core_intraday", version: 1 } }, oosMetrics: { oos_ic: 0.05 }, folds: [], repro: null, dataset: null, lineageId: "", trialId: "" },
    });
    routes["GET /api/v1/research/runs/RUN1/diagnostics"] = () => ({
      report: {
        findings: [
          { code: "regime_dependence", severity: "SEVERITY_INFO", message: "info finding", evidence: [] },
          { code: "leakage_suspicion", severity: "SEVERITY_CRITICAL", message: "one feature explains everything", evidence: [{ name: "share", value: 0.9, threshold: 0.5, ref: "" }] },
        ],
        errorSlices: [],
        learningCurves: null,
        truncated: false,
      },
    });
    routes["GET /api/v1/research/runs/RUN1/model-card"] = () => ({ card: { runId: "RUN1", markdown: "# Model card\n<script>alert(1)</script>", fields: {} } });
    routes["GET /api/v1/research/runs/RUN1/importance"] = () => ({ importance: { method: "gain", perFold: [], stability: [{ feature: "ret_1", mean: 2, std: 0.5, signFlipRate: 0, rankStd: 0 }], topInteractions: [] } });
    renderAt("/training/runs/RUN1");
    const link = await screen.findByRole("link", { name: /Tearsheet/ });
    expect(link).toHaveAttribute("href", tearsheetUrl("RUN1"));
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(await screen.findByText("one feature explains everything")).toBeInTheDocument();
    const codes = [...document.querySelectorAll("[data-finding]")].map((e) => e.getAttribute("data-finding"));
    expect(codes).toEqual(["leakage_suspicion", "regime_dependence"]);
    expect(screen.getByText(/share 0.900 \(threshold 0.500\)/)).toBeInTheDocument();
    // UI-005: the model card is text, never HTML.
    expect(await screen.findByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });
});

const lineage = {
  lineageId: "LIN1",
  name: "orb-intraday",
  family: "lgbm_regression",
  timeframe: "15m",
  universe: "synthetic:intraday_edge",
  autonomyLevel: 0,
  analystBudget: null,
  analystUsed: null,
  promotionLimits: null,
  holdoutBoundary: "1767225600000000000",
  createdAt: "0",
  currentCycleId: "CYC1",
  championCandidateId: "",
};

describe("Experiments (UI-024, UI-026)", () => {
  beforeEach(() => {
    routes["GET /api/v1/research/lineages/LIN1"] = () => ({ lineage });
    routes["GET /api/v1/research/lineages/LIN1/ledger"] = () => ({
      ledger: { lineageId: "LIN1", trialsTotal: 14, bySource: { owner: 12, abandoned: 2 }, byDimension: {}, byStatus: {}, holdoutEvaluations: 1, cycles: [{ cycleId: "CYC1", status: "CYCLE_STATUS_OPEN", effectiveTrialBudget: 20, effectiveTrialsUsed: 6.5, targetSharpe: 1, availableYears: 2 }] },
    });
    routes["GET /api/v1/research/lineages/LIN1/stats"] = () => ({ stats: { nTrials: 14, nEffective: 6.5, bestSharpe: 0.1, deflatedSharpeProb: 0.4, pbo: 0.3, minTrackRecordLength: 300, minBacktestLengthYears: 3, availableYears: 2, significanceReachable: false, dsrThreshold: 0.95 } });
    routes["GET /api/v1/research/lineages/LIN1/holdout"] = () => ({ status: { lineageId: "LIN1", family: "lgbm_regression", boundary: "1767225600000000000", touches: [], touchesThisQuarter: 1, remainingThisQuarter: 0, trialInflationPerTouch: 1, retiredBoundaries: [] } });
    routes["GET /api/v1/research/studies"] = () => ({ studies: [] });
    routes["GET /api/v1/research/candidates"] = () => ({ candidates: [] });
    routes["GET /api/v1/research/families"] = () => ({ families: [family("lgbm_regression")] });
  });

  it("UI-024, EX-023: shows the optimizer's ledger, effective N, cycle budget, holdout counter, and the stop signal verbatim", async () => {
    renderAt("/experiments/lineages/LIN1");
    expect(await screen.findByText("14 / 6.5")).toBeInTheDocument();
    expect(screen.getByText("6.5 / 20.0")).toBeInTheDocument();
    expect(screen.getByText(/Significance is no longer reachable/)).toBeInTheDocument();
    const counter = await screen.findByTestId("holdout-counter");
    expect(counter).toHaveTextContent("2026-01-01");
    expect(counter).toHaveTextContent("Remaining0");
    expect(screen.getByText(/owner 12/)).toHaveTextContent("abandoned 2");
  });

  it("UI-024, EX-026: a study cannot be launched without a pre-registered hypothesis, and launches with it", async () => {
    routes["POST /api/v1/research/studies"] = () => ({ study: { studyId: "ST1", status: "STUDY_STATUS_CREATED" } });
    routes["POST /api/v1/research/studies/ST1/start"] = () => ({ study: { studyId: "ST1", status: "STUDY_STATUS_RUNNING" } });
    routes["GET /api/v1/research/studies/ST1"] = () => ({ study: { studyId: "ST1", status: "STUDY_STATUS_RUNNING", spec: null, bestTrials: [], trialsCompleted: 0, trialsPruned: 0, trialsFailed: 0, trialCountTotal: 14, budgetUsed: null, outerEstimate: null, message: "", candidateId: "" } });
    routes["GET /api/v1/research/studies/ST1/trials"] = () => ({ trials: [] });
    routes["GET /api/v1/research/studies/ST1/diagnostics"] = () => ({ diagnostics: null });
    const router = renderAt("/experiments/lineages/LIN1");
    fireEvent.change(await screen.findByLabelText("Name", { selector: "input[maxlength='80']" }), { target: { value: "first study" } });
    const launch = screen.getByRole("button", { name: "Create and start study" });
    expect(launch).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Hypothesis (pre-registered)"), { target: { value: "Residual momentum predicts 1h returns" } });
    expect(launch).toBeEnabled();
    fireEvent.click(launch);
    await waitFor(() => expect(router.state.location.pathname).toBe("/experiments/studies/ST1"));
    const create = calls.find((c) => c.method === "POST" && c.path === "/api/v1/research/studies");
    expect(create?.body).toMatchObject({ lineageId: "LIN1", families: ["lgbm_regression"], preregistration: { hypothesis: "Residual momentum predicts 1h returns", metric: "sharpe", stopRule: "budget" } });
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/v1/research/studies/ST1/start")).toBe(true);
  });

  it("UI-025, UI-026: the candidate page shows every gate reason, never offers live, and blocks a spent holdout", async () => {
    routes["GET /api/v1/research/candidates/CAND1"] = () => ({
      candidate: { candidateId: "CAND1", lineageId: "LIN1", runId: "RUN9", stage: "REGISTRY_STAGE_PAPER", syntheticData: true, demoted: false, config: { family: "lgbm_regression" }, strategyId: "", shadowSessions: 5, createdAt: "0", origin: "training" },
    });
    routes["GET /api/v1/research/candidates/CAND1/gate-report"] = () => ({
      report: { passed: false, reasons: ["dsr 0.41 < 0.95", "pbo 0.6 > 0.5"], warnings: [], outer: null, dsr: 0.41, nEffective: 6.5, pbo: 0.6, spaP: 0.2, romanoWolfP: 0.3, walkForward: [], regimes: [], costSensitivity: null, paramStability: [], leakagePassed: true, survivorshipStatus: "synthetic", thresholds: { dsr: 0.95 }, engine: "event", createdAt: "0" },
    });
    renderAt("/experiments/candidates/CAND1");
    const reasons = await screen.findByTestId("gate-reasons");
    expect(reasons).toHaveTextContent("dsr 0.41 < 0.95");
    expect(reasons).toHaveTextContent("pbo 0.6 > 0.5");
    expect(screen.getByRole("link", { name: /Evidence pack/ })).toHaveAttribute("href", "/api/v1/research/candidates/CAND1/evidence?format=html");
    expect(screen.queryByRole("button", { name: /live/i })).toBeNull();
    expect(await screen.findByTestId("holdout-remaining")).toHaveTextContent("0 remaining");
    fireEvent.change(screen.getByLabelText("Reason (required, audited)"), { target: { value: "final check" } });
    expect(screen.getByRole("button", { name: /Evaluate on holdout/ })).toBeDisabled();
  });

  it("UI-026: promotion to paper is offered with the passkey label and posts the target stage", async () => {
    routes["GET /api/v1/research/candidates/CAND2"] = () => ({
      candidate: { candidateId: "CAND2", lineageId: "LIN1", runId: "", stage: "REGISTRY_STAGE_SHADOW", syntheticData: false, demoted: false, config: null, strategyId: "", shadowSessions: 12, createdAt: "0", origin: "training" },
    });
    routes["GET /api/v1/research/candidates/CAND2/gate-report"] = () => ({ report: null });
    routes["POST /api/v1/research/candidates/CAND2/check"] = () => ({ decision: { allowed: true, reason: "owner", requiresStepUp: true, failedGates: [] } });
    routes["POST /api/v1/research/candidates/CAND2/promote"] = () => ({ candidate: { candidateId: "CAND2" }, decision: { allowed: true } });
    renderAt("/experiments/candidates/CAND2");
    fireEvent.click(await screen.findByRole("button", { name: "Check paper" }));
    expect(await screen.findByText(/needs a passkey/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Promote to paper (passkey)" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/CAND2/promote"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/CAND2/promote"))?.body).toEqual({ toStage: "paper" });
  });
});
