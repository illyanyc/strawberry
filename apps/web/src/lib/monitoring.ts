/**
 * Monitoring resources (section 13 Phase 5, ADR-0045): the canonical protojson form of
 * `tb.monitor.v1` messages from `/api/v1/monitoring/*`, typed by the generated `*Json` types. The UI
 * only displays what the monitor computed; it never recomputes a statistic.
 */

import type {
  AlertRecordJson,
  ContainmentActionJson,
  ContainmentPolicyJson,
  DailyReportJson,
  DivergenceReportJson,
  DriftReportJson,
  ExecutionQualityReportJson,
  GetContainmentPolicyResponseJson,
  GetDivergenceReportResponseJson,
  GraduationEvidenceJson,
  HealthComponentJson,
  HealthStatusJson,
  ListAlertsResponseJson,
  ListContainmentActionsResponseJson,
  ListStrategyHealthResponseJson,
  PerformanceReportJson,
  StrategyHealthJson,
} from "@strawberry/contracts/tb/monitor/v1/monitor_pb";

export type {
  AlertRecordJson,
  ContainmentActionJson,
  ContainmentPolicyJson,
  DailyReportJson,
  DivergenceReportJson,
  DriftReportJson,
  ExecutionQualityReportJson,
  GetContainmentPolicyResponseJson,
  GetDivergenceReportResponseJson,
  GraduationEvidenceJson,
  HealthComponentJson,
  ListAlertsResponseJson,
  ListContainmentActionsResponseJson,
  ListStrategyHealthResponseJson,
  PerformanceReportJson,
  StrategyHealthJson,
};

export const M = "/api/v1/monitoring";

export type Tone = "neutral" | "ok" | "warn" | "danger";

/** Badge tone and label for a health status (MN-031). */
export function statusView(s: HealthStatusJson | undefined): { tone: Tone; label: string } {
  switch (s) {
    case "HEALTH_STATUS_OK":
      return { tone: "ok", label: "ok" };
    case "HEALTH_STATUS_WARNING":
      return { tone: "warn", label: "warning" };
    case "HEALTH_STATUS_BREACH":
      return { tone: "danger", label: "breach" };
    case "HEALTH_STATUS_SEVERE":
      return { tone: "danger", label: "severe" };
    case "HEALTH_STATUS_INSUFFICIENT_DATA":
      return { tone: "neutral", label: "insufficient data" };
    case "HEALTH_STATUS_UNSPECIFIED":
    case undefined:
      return { tone: "neutral", label: "unknown" };
    default: {
      const never: never = s;
      return { tone: "neutral", label: String(never) };
    }
  }
}

export const COMPONENT_LABELS: Record<string, string> = {
  drawdown: "Drawdown vs backtest p95",
  performance: "Return percentile",
  divergence: "Divergence",
  feature_drift: "Feature drift",
  prediction_drift: "Prediction drift",
  execution_quality: "Implementation shortfall",
  decay: "Decay (CUSUM)",
};

export function componentLabel(c: HealthComponentJson): string {
  return COMPONENT_LABELS[c.name ?? ""] ?? c.name ?? "—";
}

/** protojson doubles: numbers, or the strings "NaN" / "Infinity" / "-Infinity". */
export type JsonDouble = number | "NaN" | "Infinity" | "-Infinity";

export function toNum(x: JsonDouble | undefined): number {
  return x === undefined ? 0 : typeof x === "string" ? Number(x) : x;
}

/** Fixed-precision number for display ("—" when missing or not finite). */
export function num(x: JsonDouble | undefined, digits = 2): string {
  const v = toNum(x);
  return x === undefined || !Number.isFinite(v) ? "—" : v.toFixed(digits);
}

export function actionLabel(kind: string | undefined): string {
  return (kind ?? "").replace("CONTAINMENT_ACTION_KIND_", "").toLowerCase().replaceAll("_", " ") || "—";
}

export const ACTIONS = [
  "CONTAINMENT_ACTION_KIND_ALERT",
  "CONTAINMENT_ACTION_KIND_DEMOTE",
  "CONTAINMENT_ACTION_KIND_KILL_CANCEL_OPEN",
  "CONTAINMENT_ACTION_KIND_KILL_FLATTEN",
] as const;

/** Editable numeric policy fields with their labels (MN-034). */
export const POLICY_FIELDS: readonly { key: keyof ContainmentPolicyJson; label: string; step: number }[] = [
  { key: "drawdownWarningMultiple", label: "Drawdown warning (× p95)", step: 0.1 },
  { key: "drawdownBreachMultiple", label: "Drawdown breach (× p95)", step: 0.1 },
  { key: "matchRateWarning", label: "Match rate warning (below)", step: 0.01 },
  { key: "matchRateBreach", label: "Match rate breach (below)", step: 0.01 },
  { key: "slippageGapWarningBps", label: "Slippage gap warning (bps)", step: 1 },
  { key: "slippageGapBreachBps", label: "Slippage gap breach (bps)", step: 1 },
  { key: "featurePsiWarning", label: "Feature PSI warning", step: 0.05 },
  { key: "featurePsiBreach", label: "Feature PSI breach", step: 0.05 },
  { key: "predictionPsiWarning", label: "Prediction PSI warning", step: 0.05 },
  { key: "predictionPsiBreach", label: "Prediction PSI breach", step: 0.05 },
  { key: "shortfallWarningMultiple", label: "Shortfall warning (× modeled)", step: 0.1 },
  { key: "shortfallBreachMultiple", label: "Shortfall breach (× modeled)", step: 0.1 },
];
