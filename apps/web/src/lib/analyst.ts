/**
 * Types and helpers for the AI analyst pages (section 14 Phase 4 slice). Responses are the canonical
 * protojson of `tb.analyst.v1` messages (ADR-0028), typed with the generated `*Json` types. The UI only
 * displays what the assistant and the optimizer computed: deflated statistics, decisions, and trial
 * counts come from the services; nothing is recomputed in the browser.
 */

import type {
  AnalystRoundJson,
  CodeProposalJson,
  ConversationJson,
  GetJournalResponseJson,
  GetLoopStatusResponseJson,
  GetTrialLedgerResponseJson,
  ListCodeProposalsResponseJson,
  ListConversationsResponseJson,
  ListLineagesResponseJson,
  LoopStatusJson,
  PlanActionJson,
  PostMessageResponseJson,
  ReviewActionsResponseJson,
} from "@strawberry/contracts/tb/analyst/v1/analyst_pb";

import { api } from "./api";
import type { Emitted } from "./trading";

export type LoopStatus = Emitted<LoopStatusJson>;
export type LineagesResponse = Emitted<ListLineagesResponseJson>;
export type LoopStatusResponse = Emitted<GetLoopStatusResponseJson>;
export type Round = Emitted<AnalystRoundJson>;
export type JournalResponse = Emitted<GetJournalResponseJson>;
export type PlanAction = Emitted<PlanActionJson>;
export type TrialLedger = Emitted<GetTrialLedgerResponseJson>;
export type CodeProposal = Emitted<CodeProposalJson>;
export type ProposalsResponse = Emitted<ListCodeProposalsResponseJson>;
export type Conversation = Emitted<ConversationJson>;
export type ConversationsResponse = Emitted<ListConversationsResponseJson>;
export type PostMessageResponse = Emitted<PostMessageResponseJson>;
export type ReviewResponse = Emitted<ReviewActionsResponseJson>;

export const LEVELS = [
  { level: 0, name: "Suggest-only", detail: "Diagnoses and proposes; every launch needs your approval." },
  { level: 1, name: "Auto-run on paper", detail: "Launches training, ablations, and studies within budget; deploys gated challengers to shadow." },
  { level: 2, name: "Auto-promote within limits", detail: "Also replaces the paper champion after the shadow period, within promotion limits. Never live." },
] as const;

/** `ROUND_DECISION_STOP_NO_EDGE` → `stop no edge` (display only). */
export function label(value: string | undefined | null, prefix: string): string {
  if (!value) return "—";
  return value.startsWith(prefix) ? value.slice(prefix.length).toLowerCase().replaceAll("_", " ") : value.toLowerCase();
}

export function decisionTone(decision: string): "ok" | "warn" | "danger" | "neutral" {
  if (decision.endsWith("CONTINUE")) return "ok";
  if (decision.endsWith("AWAITING_APPROVAL") || decision.endsWith("PLATEAU")) return "warn";
  if (decision.includes("STOP") || decision.endsWith("ESCALATE_TO_OWNER")) return "danger";
  return "neutral";
}

/** Protojson doubles are numbers, or the strings "NaN" / "Infinity" / "-Infinity". */
export function fmt(x: number | string | null | undefined, digits = 3): string {
  return typeof x === "number" && Number.isFinite(x) ? x.toFixed(digits) : "—";
}

/** Pretty-print a JSON merge patch for review; invalid JSON is shown as is. */
export function prettyDiff(json: string): string {
  if (!json) return "";
  try {
    return JSON.stringify(JSON.parse(json), null, 2);
  } catch {
    return json;
  }
}

export const analystApi = {
  lineages: () => api<LineagesResponse>("/api/v1/analyst/lineages"),
  status: (id: string) => api<LoopStatusResponse>(`/api/v1/analyst/lineages/${encodeURIComponent(id)}`),
  journal: (id: string) => api<JournalResponse>(`/api/v1/analyst/lineages/${encodeURIComponent(id)}/journal?limit=50`),
  trials: (id: string) => api<TrialLedger>(`/api/v1/analyst/lineages/${encodeURIComponent(id)}/trials?limit=500`),
  proposals: (id: string) => api<ProposalsResponse>(`/api/v1/analyst/proposals?lineage=${encodeURIComponent(id)}`),
  // The optimizer owns the level; the research route asks for a passkey when it rises (AN-061).
  setAutonomy: (id: string, level: number) =>
    api(`/api/v1/research/lineages/${encodeURIComponent(id)}/autonomy`, { method: "PUT", body: { level } }),
  pause: (id: string, paused: boolean, reason: string) =>
    api(`/api/v1/analyst/lineages/${encodeURIComponent(id)}/pause`, { method: "POST", body: { paused, reason } }),
  analyze: (id: string, question: string) =>
    api<{ roundId: string }>(`/api/v1/analyst/lineages/${encodeURIComponent(id)}/analyze`, { method: "POST", body: { question } }),
  review: (roundId: string, actionIds: string[], approve: boolean, reason: string) =>
    api<ReviewResponse>(`/api/v1/analyst/rounds/${encodeURIComponent(roundId)}/review`, { method: "POST", body: { actionIds, approve, reason } }),
  reviewProposal: (id: string, approve: boolean, note: string) =>
    api(`/api/v1/analyst/proposals/${encodeURIComponent(id)}/review`, { method: "POST", body: { approve, note } }),
  conversations: () => api<ConversationsResponse>("/api/v1/assistant/conversations"),
  conversation: (id: string) => api<{ conversation: Conversation }>(`/api/v1/assistant/conversations/${encodeURIComponent(id)}`),
  createConversation: (title: string, lineageId: string) =>
    api<{ conversation: Conversation }>("/api/v1/assistant/conversations", { method: "POST", body: { title, lineageId } }),
  post: (id: string, content: string) =>
    api<PostMessageResponse>(`/api/v1/assistant/conversations/${encodeURIComponent(id)}/messages`, { method: "POST", body: { content } }),
};
