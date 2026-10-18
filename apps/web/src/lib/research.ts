/**
 * Types and helpers for the gateway's research resources (`/api/v1/research/*`, section 14 Phase 4
 * slice). Responses are canonical protojson of the tb.ml.v1 / tb.optimizer.v1 messages typed with the
 * generated `*Json` types (ADR-0028). The UI displays what ml and optimizer compute (metrics, DSR,
 * effective N, gate verdicts, permissions) and never derives or overrides them (UI-I2, UI-026).
 */

import { useEffect, useState } from "react";

import type {
  CancelJobResponseJson,
  EstimateTrainingJobResponseJson,
  GetDiagnosticsResponseJson,
  GetFeatureImportanceResponseJson,
  GetJobResponseJson,
  GetModelCardResponseJson,
  GetRunResponseJson,
  ListFeatureSetsResponseJson,
  ListJobsResponseJson,
  ListLabelPresetsResponseJson,
  ListModelFamiliesResponseJson,
  ListRunsResponseJson,
  RegisterModelResponseJson,
  SubmitTrainingJobResponseJson,
} from "@strawberry/contracts/tb/ml/v1/ml_service_pb";
import type { DiagnosticsReportJson, FindingJson, JobProgressJson, ModelFamilyJson, TrainingConfigJson } from "@strawberry/contracts/tb/ml/v1/training_pb";
import type { EvaluateOnHoldoutResponseJson, GetHoldoutStatusResponseJson } from "@strawberry/contracts/tb/optimizer/v1/holdout_pb";
import type {
  CandidateJson,
  CheckTransitionResponseJson,
  GetCandidateResponseJson,
  GetCumulativeOverfittingStatsResponseJson,
  GetGateReportResponseJson,
  GetLineageResponseJson,
  GetStudyDiagnosticsResponseJson,
  GetStudyResponseJson,
  GetTrialLedgerResponseJson,
  LineageJson,
  ListCandidatesResponseJson,
  ListLineagesResponseJson,
  ListStudiesResponseJson,
  ListTrialsResponseJson,
  OpenCycleResponseJson,
  PromoteCandidateResponseJson,
  StudyProgressJson,
  StudySpecJson,
  TrialJson,
} from "@strawberry/contracts/tb/optimizer/v1/optimizer_pb";

import type { Emitted } from "./trading";

export type FamiliesResponse = Emitted<ListModelFamiliesResponseJson>;
export type ModelFamily = Emitted<ModelFamilyJson>;
export type FeatureSetsResponse = Emitted<ListFeatureSetsResponseJson>;
export type LabelPresetsResponse = Emitted<ListLabelPresetsResponseJson>;
export type EstimateResponse = Emitted<EstimateTrainingJobResponseJson>;
export type SubmitJobResponse = Emitted<SubmitTrainingJobResponseJson>;
export type JobResponse = Emitted<GetJobResponseJson>;
export type JobsResponse = Emitted<ListJobsResponseJson>;
export type CancelJobResponse = Emitted<CancelJobResponseJson>;
export type JobProgress = Emitted<JobProgressJson>;
export type RunResponse = Emitted<GetRunResponseJson>;
export type RunsResponse = Emitted<ListRunsResponseJson>;
export type DiagnosticsResponse = Emitted<GetDiagnosticsResponseJson>;
export type DiagnosticsReport = Emitted<DiagnosticsReportJson>;
export type Finding = Emitted<FindingJson>;
export type ImportanceResponse = Emitted<GetFeatureImportanceResponseJson>;
export type ModelCardResponse = Emitted<GetModelCardResponseJson>;
export type RegisterResponse = Emitted<RegisterModelResponseJson>;
/** Request bodies are the generated JSON shapes (protojson accepts omitted fields as defaults). */
export type TrainingConfigBody = TrainingConfigJson;
export type StudySpecBody = StudySpecJson;
export type LineageBody = LineageJson;

export type Lineage = Emitted<LineageJson>;
export type LineagesResponse = Emitted<ListLineagesResponseJson>;
export type LineageResponse = Emitted<GetLineageResponseJson>;
export type LedgerResponse = Emitted<GetTrialLedgerResponseJson>;
export type StatsResponse = Emitted<GetCumulativeOverfittingStatsResponseJson>;
export type HoldoutResponse = Emitted<GetHoldoutStatusResponseJson>;
export type HoldoutEvalResponse = Emitted<EvaluateOnHoldoutResponseJson>;
export type OpenCycleResponse = Emitted<OpenCycleResponseJson>;
export type StudiesResponse = Emitted<ListStudiesResponseJson>;
export type StudyResponse = Emitted<GetStudyResponseJson>;
export type StudyProgress = Emitted<StudyProgressJson>;
export type TrialsResponse = Emitted<ListTrialsResponseJson>;
export type Trial = Emitted<TrialJson>;
export type StudyDiagnosticsResponse = Emitted<GetStudyDiagnosticsResponseJson>;
export type Candidate = Emitted<CandidateJson>;
export type CandidatesResponse = Emitted<ListCandidatesResponseJson>;
export type CandidateResponse = Emitted<GetCandidateResponseJson>;
export type GateReportResponse = Emitted<GetGateReportResponseJson>;
export type CheckResponse = Emitted<CheckTransitionResponseJson>;
export type PromoteResponse = Emitted<PromoteCandidateResponseJson>;

export const R = "/api/v1/research";

/** Path segment for an ID taken from a server response or the URL. */
export const seg = (id: string) => encodeURIComponent(id);

/** The tearsheet is its own sandboxed document (UI-025): opened in a new tab, never inlined. */
export const tearsheetUrl = (runId: string) => `${R}/runs/${seg(runId)}/tearsheet`;
/**
 * The same document for the in-app viewer (UI-047): the gateway allows framing by this origin only
 * for `embed=1`, and the viewer's <iframe sandbox> grants nothing (no scripts, opaque origin).
 */
export const tearsheetEmbedUrl = (runId: string) => `${tearsheetUrl(runId)}?embed=1`;
export const evidenceUrl = (candidateId: string) => `${R}/candidates/${seg(candidateId)}/evidence?format=html`;

/** protojson doubles may be the strings "NaN" / "Infinity"; show them as such, never as a number. */
export type JsonDouble = number | "NaN" | "Infinity" | "-Infinity";

export function fmt(v: JsonDouble | string | undefined | null, digits = 3): string {
  if (v === undefined || v === null || v === "") return "—";
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) && v.trim() !== "" ? String(n) : v;
  }
  if (!Number.isFinite(v)) return String(v);
  if (v !== 0 && Math.abs(v) < 10 ** -digits) return v.toExponential(1);
  return v.toFixed(digits);
}

export function pct(v: JsonDouble | undefined | null, digits = 1): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return fmt(v);
  return `${(v * 100).toFixed(digits)}%`;
}

/** A protojson map (typed nullable by `Emitted`; the gateway always emits `{}`) as a plain record. */
export function kv<V>(m: Record<string, V> | null | undefined): Record<string, V> {
  return m ?? {};
}

/** Finite number for plotting (drawing only), or null. */
export function plotNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// ---- hyperparameter JSON Schema forms (ML-011 subset) ----

export interface SchemaField {
  name: string;
  type: "number" | "integer" | "boolean" | "string";
  description: string;
  minimum?: number;
  maximum?: number;
  enum?: string[];
  default?: unknown;
}

/**
 * Fields of a family's hyperparameter schema. Only the subset ml validates is supported (flat object,
 * number/integer/boolean/string, minimum/maximum/enum/default); anything else is reported so the form
 * never silently drops a parameter (UI-023).
 */
export function schemaFields(schemaJson: string): { fields: SchemaField[]; unsupported: string[] } {
  let schema: unknown;
  try {
    schema = JSON.parse(schemaJson || "{}");
  } catch {
    return { fields: [], unsupported: ["schema is not valid JSON"] };
  }
  const props = (schema as { properties?: Record<string, Record<string, unknown>> }).properties ?? {};
  const fields: SchemaField[] = [];
  const unsupported: string[] = [];
  for (const [name, p] of Object.entries(props)) {
    const t = p.type;
    if (t !== "number" && t !== "integer" && t !== "boolean" && t !== "string") {
      unsupported.push(name);
      continue;
    }
    fields.push({
      name,
      type: t,
      description: typeof p.description === "string" ? p.description : "",
      minimum: typeof p.minimum === "number" ? p.minimum : undefined,
      maximum: typeof p.maximum === "number" ? p.maximum : undefined,
      enum: Array.isArray(p.enum) ? p.enum.map(String) : undefined,
      default: p.default,
    });
  }
  return { fields, unsupported };
}

/** Raw form values (strings for numbers, so partial input stays editable). */
export type HyperparamDraft = Record<string, string | boolean>;

export function draftDefaults(fields: SchemaField[]): HyperparamDraft {
  const out: HyperparamDraft = {};
  for (const f of fields) {
    if (f.type === "boolean") out[f.name] = f.default === true;
    else out[f.name] = f.default === undefined ? "" : String(f.default);
  }
  return out;
}

/**
 * Per-field validation messages mirroring ml's checks (ML-011), so a bad config fails at the form,
 * not late in the job (section 14 risk). The server re-validates; this is presentation only.
 */
export function hyperparamErrors(fields: SchemaField[], draft: HyperparamDraft): Record<string, string> {
  const errs: Record<string, string> = {};
  for (const f of fields) {
    const raw = draft[f.name];
    if (f.type === "boolean" || raw === undefined) continue;
    const s = String(raw).trim();
    if (f.type === "string") {
      if (f.enum && !f.enum.includes(s)) errs[f.name] = `One of ${f.enum.join(", ")}`;
      continue;
    }
    if (s === "") {
      if (f.default === undefined) errs[f.name] = "Required";
      continue;
    }
    const n = Number(s);
    if (!Number.isFinite(n)) errs[f.name] = "Must be a number";
    else if (f.type === "integer" && !Number.isInteger(n)) errs[f.name] = "Must be a whole number";
    else if (f.minimum !== undefined && n < f.minimum) errs[f.name] = `Must be ≥ ${f.minimum}`;
    else if (f.maximum !== undefined && n > f.maximum) errs[f.name] = `Must be ≤ ${f.maximum}`;
  }
  return errs;
}

/** The Struct sent as `hyperparams`: only fields the owner filled; empty means the schema default. */
export function hyperparamValues(fields: SchemaField[], draft: HyperparamDraft): Record<string, number | boolean | string> {
  const out: Record<string, number | boolean | string> = {};
  for (const f of fields) {
    const raw = draft[f.name];
    if (raw === undefined) continue;
    if (f.type === "boolean") out[f.name] = raw === true;
    else if (f.type === "string") out[f.name] = String(raw);
    else if (String(raw).trim() !== "") out[f.name] = Number(raw);
  }
  return out;
}

// ---- dates ----

/** A `YYYY-MM-DD` date (UTC midnight) as protojson int64 ns, or undefined when empty/invalid. */
export function dateToNs(date: string): string | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return undefined;
  const ms = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(ms) ? undefined : (BigInt(ms) * 1_000_000n).toString();
}

/** UTC date of an int64 ns string (for display beside boundaries). */
export function nsToDate(ns: string | undefined): string {
  if (!ns || ns === "0") return "—";
  return new Date(Number(BigInt(ns) / 1_000_000n)).toISOString().slice(0, 10);
}

// ---- registry stages (EX-050) ----

export const STAGE_ORDER = ["candidate", "validated", "shadow", "paper", "live_eligible", "live", "retired"] as const;
export type Stage = (typeof STAGE_ORDER)[number];

export function stageOf(name: string | undefined): Stage | "unknown" {
  const s = (name ?? "").replace("REGISTRY_STAGE_", "").toLowerCase();
  return (STAGE_ORDER as readonly string[]).includes(s) ? (s as Stage) : "unknown";
}

/**
 * Stages the UI offers as promotion targets from `current`. `live` is never offered: going live is
 * the section 15 flow only (SM-004, UI-026). Synthetic-data candidates never reach live_eligible
 * (EX-052); the server enforces both regardless.
 */
export function promotionTargets(current: Stage | "unknown", synthetic: boolean): Stage[] {
  switch (current) {
    case "candidate":
      return ["validated"];
    case "validated":
      return ["shadow"];
    case "shadow":
      return ["paper"];
    case "paper":
      return synthetic ? [] : ["live_eligible"];
    case "live_eligible":
    case "live":
    case "retired":
    case "unknown":
      return [];
    default: {
      const never: never = current;
      return never;
    }
  }
}

export function stageTone(stage: Stage | "unknown"): "neutral" | "ok" | "warn" | "danger" | "paper" | "live" {
  switch (stage) {
    case "candidate":
    case "unknown":
      return "neutral";
    case "validated":
    case "shadow":
      return "ok";
    case "paper":
      return "paper";
    case "live_eligible":
      return "warn";
    case "live":
      return "live";
    case "retired":
      return "danger";
    default: {
      const never: never = stage;
      return never;
    }
  }
}

export function severityTone(sev: string | undefined): "neutral" | "warn" | "danger" {
  if (sev === "SEVERITY_CRITICAL") return "danger";
  if (sev === "SEVERITY_WARNING") return "warn";
  return "neutral";
}

/** Critical findings first, then warnings, then info (the analyst and owner read the worst first). */
export function sortFindings(findings: Finding[]): Finding[] {
  const rank: Record<string, number> = { SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2 };
  return [...findings].sort((a, b) => (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3));
}

export const isTerminalJob = (s: string | undefined) => s === "JOB_STATUS_SUCCEEDED" || s === "JOB_STATUS_FAILED" || s === "JOB_STATUS_CANCELED";
export const isTerminalStudy = (s: string | undefined) => s === "STUDY_STATUS_COMPLETED" || s === "STUDY_STATUS_STOPPED" || s === "STUDY_STATUS_FAILED";

// ---- progress streams (SSE, D14) ----

export interface ProgressStream<T> {
  events: T[];
  connected: boolean;
  done: boolean;
}

/** Most events kept in memory per stream; older ones are dropped (the run record keeps the full history). */
export const MAX_PROGRESS_EVENTS = 2_000;

/**
 * Subscribes to a research progress stream (`progress` events with the upstream sequence as the
 * event ID, then `done`). EventSource resumes with Last-Event-ID after a drop; `done` closes it so a
 * finished job is not reconnected forever. `url` null means no stream.
 */
export function useProgressStream<T>(url: string | null): ProgressStream<T> {
  const [state, setState] = useState<ProgressStream<T> & { url: string | null }>({ url, events: [], connected: false, done: false });
  if (state.url !== url) setState({ url, events: [], connected: false, done: false });

  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url, { withCredentials: true });
    es.addEventListener("open", () => setState((s) => ({ ...s, connected: true })));
    es.addEventListener("error", () => setState((s) => ({ ...s, connected: false })));
    es.addEventListener("progress", (e) => {
      const ev = JSON.parse((e as MessageEvent<string>).data) as T;
      setState((s) => ({ ...s, connected: true, events: [...s.events, ev].slice(-MAX_PROGRESS_EVENTS) }));
    });
    es.addEventListener("done", () => {
      es.close();
      setState((s) => ({ ...s, connected: false, done: true }));
    });
    return () => es.close();
  }, [url]);

  return { events: state.events, connected: state.connected, done: state.done };
}

/** Train/validation metric series per boosting step from job progress (one line per metric key). */
export function curveSeries(events: JobProgress[], fold: number): { name: string; points: [number, number][] }[] {
  const by = new Map<string, [number, number][]>();
  for (const e of events) {
    if (e.fold !== fold || !e.step) continue;
    for (const [k, v] of Object.entries(kv(e.metrics))) {
      const n = plotNum(v);
      if (n === null) continue;
      const pts = by.get(k) ?? [];
      pts.push([e.step, n]);
      by.set(k, pts);
    }
  }
  return [...by.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, points]) => ({ name, points }));
}

/** Numeric trial parameters (for parallel coordinates); categorical values are skipped. */
export function numericParams(trials: Trial[]): string[] {
  const keys = new Set<string>();
  for (const t of trials) {
    for (const [k, v] of Object.entries(t.params ?? {})) if (typeof v === "number") keys.add(k);
  }
  return [...keys].sort();
}
