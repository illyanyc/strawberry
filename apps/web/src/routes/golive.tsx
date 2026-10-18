import { useQuery } from "@tanstack/react-query";

import { QueryState } from "@/components/trading";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { api } from "@/lib/api";

/**
 * Go live (UI-052, GL-010..GL-020). The checklist is execution's report. Promote and enable stay
 * disabled until every gate passes; this page has no switch that turns an account live.
 */

export interface LiveCriterion {
  name?: string;
  required?: string;
  actual?: string;
  passed?: boolean;
}

export interface GraduationReport {
  strategyId?: string;
  overall?: boolean;
  criteria?: LiveCriterion[];
  paperSessions?: number;
  paperTrades?: number;
  registryStage?: string;
}

export interface LiveAccount {
  brokerAccountId?: string;
  environment?: string;
  enabled?: boolean;
  ordersEnabled?: boolean;
  readinessComplete?: boolean;
  sessionState?: string;
  readOnlySessions?: number;
}

export interface LiveStatus {
  flagEnabled?: boolean;
  accounts?: LiveAccount[];
  candidates?: GraduationReport[];
  liveStrategies?: { strategyId?: string; liveCapitalCap?: { value?: string } }[];
}

export interface Readiness {
  complete?: boolean;
  items?: { id?: string; title?: string; status?: string; automated?: boolean; detail?: string }[];
}

/** Reasons the promote/enable actions must stay refused (UI-052). Empty means the server may be asked. */
export function liveRefusals(status: LiveStatus | undefined, readiness: Readiness | undefined): string[] {
  if (!status) return ["Live status has not loaded"];
  const out: string[] = [];
  if (!status.flagEnabled) out.push("The deploy flag TB_TRADING_LIVE_ENABLED is off");
  const live = (status.accounts ?? []).filter((a) => a.environment === "live");
  if (live.length === 0) out.push("No live-environment account is connected");
  if (!live.some((a) => a.ordersEnabled)) out.push("No live account is enabled for trading");
  if (readiness && !readiness.complete) out.push("The readiness checklist is not complete");
  const ready = (status.candidates ?? []).filter((c) => c.overall);
  if (ready.length === 0) out.push("No paper strategy has a passing graduation report");
  if ((status.liveStrategies ?? []).length > 0) out.push("A strategy is already live");
  return out;
}

export function GoLivePage() {
  const status = useQuery({ queryKey: ["live"], queryFn: () => api<{ status: LiveStatus }>("/api/v1/live"), refetchInterval: 15_000 });
  const liveAcct = status.data?.status.accounts?.find((a) => a.environment === "live")?.brokerAccountId;
  const readiness = useQuery({
    queryKey: ["live-readiness", liveAcct],
    queryFn: () => api<{ readiness: Readiness }>(`/api/v1/live/accounts/${liveAcct}/readiness`),
    enabled: Boolean(liveAcct),
  });
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Go live</h1>
      <p className="text-sm text-muted-foreground">
        Paper stays the default. Enabling a live account and promoting a strategy need a passkey step-up, the evidence pack, and a typed confirmation. The analyst cannot do either.
      </p>
      <QueryState q={status} empty="Live status is unavailable">
        {(data) => {
          const refusals = liveRefusals(data.status, readiness.data?.readiness);
          return (
          <>
            {refusals.length > 0 ? (
              <Alert tone="warn">Live trading stays off until every gate passes.</Alert>
            ) : (
              <Alert tone="ok">Every gate on this screen has passed. Promotion still requires your passkey and a typed confirmation.</Alert>
            )}
            <Card>
              <CardHeader title="Gates" description="Computed by execution from the monitor's paper evidence and the optimizer's combined DSR." />
              <CardBody>
                <ul className="space-y-2 text-sm">
                  {refusals.map((r) => (
                    <li key={r} className="flex gap-2">
                      <Badge tone="danger">refused</Badge>
                      <span>{r}</span>
                    </li>
                  ))}
                  {refusals.length === 0 && (
                    <li className="flex gap-2">
                      <Badge tone="ok">passed</Badge>
                      <span>Checklist, graduation, flag, and account stage</span>
                    </li>
                  )}
                </ul>
                <button type="button" className="mt-4 rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-40" disabled={refusals.length > 0}>
                  Promote to live
                </button>
              </CardBody>
            </Card>
            {readiness.data && (
              <Card>
                <CardHeader title="Readiness checklist" />
                <CardBody>
                  <ul className="space-y-1 text-sm">
                    {readiness.data.readiness.items?.map((it) => (
                      <li key={it.id} className="flex justify-between gap-3">
                        <span>{it.title}</span>
                        <Badge tone={it.status === "passed" ? "ok" : "danger"}>{it.status}</Badge>
                      </li>
                    ))}
                  </ul>
                </CardBody>
              </Card>
            )}
            <Card>
              <CardHeader title="Graduation" />
              <CardBody>
                {(data.status.candidates ?? []).length === 0 && <p className="text-sm text-muted-foreground">No paper strategy yet.</p>}
                {(data.status.candidates ?? []).map((c) => (
                  <div key={c.strategyId} className="mb-3">
                    <div className="mb-1 flex items-center gap-2 text-sm font-medium">
                      {c.strategyId}
                      <Badge tone={c.overall ? "ok" : "danger"}>{c.overall ? "passed" : "not passed"}</Badge>
                    </div>
                    <ul className="text-xs text-muted-foreground">
                      {c.criteria?.map((x) => (
                        <li key={x.name}>
                          {x.name}: {x.actual} (need {x.required})
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </CardBody>
            </Card>
          </>
          );
        }}
      </QueryState>
    </div>
  );
}
