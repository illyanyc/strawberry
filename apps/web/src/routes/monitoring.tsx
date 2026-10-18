import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useId, useState } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Badge, Field } from "@/components/ui/form";
import { Page, PageHeader } from "@/components/ui/layout";
import { api, ApiError } from "@/lib/api";
import {
  ACTIONS,
  actionLabel,
  componentLabel,
  type AlertRecordJson,
  type ContainmentPolicyJson,
  type DailyReportJson,
  type DriftReportJson,
  type ExecutionQualityReportJson,
  type GetContainmentPolicyResponseJson,
  type GetDivergenceReportResponseJson,
  type GraduationEvidenceJson,
  type ListAlertsResponseJson,
  type ListContainmentActionsResponseJson,
  type ListStrategyHealthResponseJson,
  M,
  num,
  type PerformanceReportJson,
  POLICY_FIELDS,
  statusView,
  type StrategyHealthJson,
  toNum,
} from "@/lib/monitoring";
import { cn, formatNs } from "@/lib/utils";

/**
 * Monitoring (section 14 page 5, UI-028..UI-031; section 13 Phase 5): strategy health, performance
 * against the backtest's bands, drift, divergence, execution quality, graduation evidence, containment
 * policies and actions, the alert inbox with acknowledge and silence, and daily reports.
 */
export function MonitoringPage() {
  const health = useQuery({
    queryKey: ["monitoring", "strategies"],
    queryFn: () => api<ListStrategyHealthResponseJson>(`${M}/strategies`),
    refetchInterval: 15_000,
  });
  const [picked, setSelected] = useState<string | null>(null);
  const strategies = health.data?.strategies ?? [];
  const selected = picked ?? strategies[0]?.strategyId ?? null;

  return (
    <Page wide>
      <PageHeader
        eyebrow="Operations"
        title="Monitoring"
        description="Live results against the backtest, drift, divergence, and execution quality per strategy. Containment only reduces risk; re-promotion is yours."
      />
      <Card>
        <CardHeader title="Strategy health" description="Worst component decides the status; insufficient data never acts. Breaches demote automatically." />
        <CardBody>
          <QueryState q={health} empty="No monitored strategies." isEmpty={(d) => (d.strategies ?? []).length === 0}>
            {(d) => (
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Strategy health">
                {(d.strategies ?? []).map((h) => (
                  <HealthCard key={h.strategyId} h={h} active={h.strategyId === selected} onSelect={() => setSelected(h.strategyId ?? null)} />
                ))}
              </ul>
            )}
          </QueryState>
        </CardBody>
      </Card>
      {selected && <StrategyDetail id={selected} health={strategies.find((s) => s.strategyId === selected)} />}
      <AlertsCard />
      <DailyReportCard />
    </Page>
  );
}

export function HealthCard({ h, active, onSelect }: { h: StrategyHealthJson; active: boolean; onSelect: () => void }) {
  const st = statusView(h.status);
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={active}
        className={cn("w-full space-y-2 rounded-lg border p-3 text-left text-sm", active ? "border-accent" : "border-border")}
      >
        <span className="flex items-center justify-between gap-2">
          <span className="font-mono text-xs">{h.strategyId}</span>
          <Badge tone={st.tone}>{st.label}</Badge>
        </span>
        <span className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold" aria-label="health score">{num(h.score, 0)}</span>
          <span className="text-xs text-muted">
            {h.mode ?? "—"} · {h.horizon ?? "—"} · {h.sessions ?? 0} sessions · {h.trades ?? 0} trades
          </span>
        </span>
        <span className="block space-y-1">
          {(h.components ?? []).map((c) => (
            <span key={c.name} className="flex items-center gap-2 text-xs">
              <span className="w-40 truncate text-muted">{componentLabel(c)}</span>
              <meter className="h-2 flex-1" min={0} max={100} low={21} high={61} optimum={100} value={toNum(c.score)} aria-label={`${componentLabel(c)} score`} />
              <span className="w-24 text-right">{statusView(c.status).label}</span>
            </span>
          ))}
        </span>
      </button>
    </li>
  );
}

type Tab = "performance" | "drift" | "divergence" | "execution" | "graduation" | "containment";
const TABS: readonly Tab[] = ["performance", "drift", "divergence", "execution", "graduation", "containment"];

function StrategyDetail({ id, health }: { id: string; health?: StrategyHealthJson }) {
  const [tab, setTab] = useState<Tab>("performance");
  return (
    <Card>
      <CardHeader
        title={`Strategy ${id}`}
        description={health ? (health.components ?? []).filter((c) => c.status === "HEALTH_STATUS_BREACH" || c.status === "HEALTH_STATUS_WARNING").map((c) => c.detail).join(" · ") || "All components within thresholds." : undefined}
      />
      <CardBody className="space-y-4">
        <div role="tablist" aria-label="Reports" className="flex flex-wrap gap-1">
          {TABS.map((t) => (
            <Button key={t} role="tab" aria-selected={tab === t} variant={tab === t ? "primary" : "secondary"} size="sm" onClick={() => setTab(t)}>
              {t}
            </Button>
          ))}
        </div>
        {tab === "performance" && <PerformanceView id={id} />}
        {tab === "drift" && <DriftView id={id} />}
        {tab === "divergence" && <DivergenceView id={id} />}
        {tab === "execution" && <ExecutionView id={id} />}
        {tab === "graduation" && <GraduationView id={id} />}
        {tab === "containment" && <ContainmentView id={id} />}
      </CardBody>
    </Card>
  );
}

function useReport<T>(id: string, kind: string) {
  return useQuery({ queryKey: ["monitoring", id, kind], queryFn: () => api<T>(`${M}/strategies/${encodeURIComponent(id)}/${kind}`) });
}

export function PerformanceView({ id }: { id: string }) {
  const q = useReport<{ report?: PerformanceReportJson }>(id, "performance");
  return (
    <QueryState q={q} empty="No performance report yet." isEmpty={(d) => !d.report}>
      {(d) => (
        <div className="space-y-3">
          <p className="text-sm">
            Drawdown {num(d.report?.maxDrawdownPct)}% since deployment · backtest p95 {num(d.report?.drawdownP95Pct)}% / p99 {num(d.report?.drawdownP99Pct)}% over{" "}
            {d.report?.sessions ?? 0} sessions (demotion at 1.5× p95 by default)
          </p>
          <table className="data-table" aria-label="Performance bands">
            <thead className="text-left text-xs text-muted">
              <tr><th>Metric</th><th>Live</th><th>p05</th><th>p50</th><th>p95</th><th>Percentile</th></tr>
            </thead>
            <tbody>
              {(d.report?.bands ?? []).map((b) => (
                <tr key={b.metric} className="border-t border-border">
                  <td>{b.metric}</td><td>{num(b.live)}</td><td>{num(b.p05)}</td><td>{num(b.p50)}</td><td>{num(b.p95)}</td><td>{num(b.percentile, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryState>
  );
}

export function DriftView({ id }: { id: string }) {
  const q = useReport<{ report?: DriftReportJson }>(id, "drift");
  return (
    <QueryState q={q} empty="No drift report yet." isEmpty={(d) => !d.report}>
      {(d) => (
        <div className="space-y-3">
          <p className="text-sm">
            {d.report?.liveSamples ?? 0} live feature vectors vs {d.report?.referenceSamples ?? 0} training references · prediction PSI {num(d.report?.predictionDrift?.psi, 3)}
            {" "}· signals/session live {num(d.report?.signalRateLive)} vs backtest {num(d.report?.signalRateReference)}
          </p>
          {(d.report?.features ?? []).length === 0 ? (
            <p className="text-sm text-muted">No feature reference for this strategy (rule-based strategies have none).</p>
          ) : (
            <table className="data-table" aria-label="Feature drift">
              <thead className="text-left text-xs text-muted"><tr><th>Feature</th><th>PSI</th><th>KS</th><th>Importance rank</th></tr></thead>
              <tbody>
                {(d.report?.features ?? []).slice(0, 20).map((f) => (
                  <tr key={f.feature} className="border-t border-border">
                    <td className="font-mono text-xs">{f.feature}</td>
                    <td className={cn(toNum(f.psi) >= 0.5 ? "text-live" : toNum(f.psi) >= 0.25 ? "text-warn" : undefined)}>{num(f.psi, 3)}</td>
                    <td>{num(f.ks, 3)}</td><td>{f.importanceRank || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </QueryState>
  );
}

export function DivergenceView({ id }: { id: string }) {
  const q = useReport<GetDivergenceReportResponseJson>(id, "divergence");
  return (
    <QueryState q={q} empty="No daily parity yet (first report after the next close)." isEmpty={(d) => (d.history ?? []).length === 0}>
      {(d) => (
        <table className="data-table" aria-label="Divergence history">
          <thead className="text-left text-xs text-muted">
            <tr><th>Session</th><th>Match rate</th><th>Matched</th><th>Missed</th><th>Extra</th><th>Slippage gap (bps)</th><th>P&amp;L gap (bps)</th><th>Recording</th></tr>
          </thead>
          <tbody>
            {(d.history ?? []).map((r) => (
              <tr key={r.reportId} className="border-t border-border">
                <td>{r.sessionDate}</td>
                <td>{num(r.parity?.tradeMatchRate, 3)}</td>
                <td>{r.matchedIntents ?? 0}</td><td>{r.missedIntents ?? 0}</td><td>{r.extraIntents ?? 0}</td>
                <td>{num(toNum(r.parity?.slippageBpsLive) - toNum(r.parity?.slippageBpsModeled))}</td>
                <td>{num(r.parity?.pnlGapBps)}</td>
                <td><Badge tone={r.recordingComplete ? "ok" : "warn"}>{r.recordingComplete ? "complete" : "incomplete"}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </QueryState>
  );
}

export function ExecutionView({ id }: { id: string }) {
  const q = useReport<{ report?: ExecutionQualityReportJson }>(id, "execution");
  return (
    <QueryState q={q} empty="No fills yet." isEmpty={(d) => !d.report?.fills}>
      {(d) => {
        const r = d.report!;
        return (
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4" aria-label="Execution quality">
            <dt className="text-muted">Shortfall</dt><dd>{num(r.shortfallBps)} bps (p90 {num(r.shortfallP90Bps)})</dd>
            <dt className="text-muted">Modeled</dt><dd>{num(r.modeledShortfallBps)} bps · ratio {num(r.shortfallRatio)}</dd>
            <dt className="text-muted">Limit fill rate</dt><dd>{num(toNum(r.limitFillRate) * 100, 0)}% of {r.limitOrders ?? 0}</dd>
            <dt className="text-muted">Fills</dt><dd>{r.fills ?? 0}</dd>
            <dt className="text-muted">Decision → submit p99</dt><dd>{num(r.decisionToSubmit?.p99Ms, 0)} ms</dd>
            <dt className="text-muted">Submit → fill p99</dt><dd>{num(r.submitToFill?.p99Ms, 0)} ms</dd>
          </dl>
        );
      }}
    </QueryState>
  );
}

export function GraduationView({ id }: { id: string }) {
  const q = useReport<{ evidence?: GraduationEvidenceJson }>(id, "graduation");
  return (
    <QueryState q={q} empty="No graduation evidence." isEmpty={(d) => !d.evidence}>
      {(d) => (
        <div className="space-y-2">
          <p className="text-sm text-muted">Evidence for the section 15 graduation report, computed only from recorded paper and shadow results. It never promotes.</p>
          <ul className="space-y-1 text-sm" aria-label="Graduation criteria">
            {(d.evidence?.criteria ?? []).map((c) => (
              <li key={c.name} className="flex items-center gap-2">
                <Badge tone={c.evidenceOnly ? "neutral" : c.passed ? "ok" : "warn"}>{c.evidenceOnly ? "section 15" : c.passed ? "met" : "not met"}</Badge>
                <span className="font-medium">{c.name}</span>
                <span className="text-muted">required {c.required} · actual {c.actual}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </QueryState>
  );
}

export function ContainmentView({ id }: { id: string }) {
  const policy = useQuery({ queryKey: ["monitoring", id, "policy"], queryFn: () => api<GetContainmentPolicyResponseJson>(`${M}/containment/policies/${encodeURIComponent(id)}`) });
  const actions = useReport<ListContainmentActionsResponseJson>(id, "actions");
  return (
    <div className="space-y-4">
      <QueryState q={policy} empty="No policy." isEmpty={(d) => !d.policy}>
        {(d) => <PolicyForm key={`${id}:${d.policy?.version ?? 0}:${d.inherited ? "default" : "own"}`} id={id} initial={d.policy!} inherited={d.inherited ?? false} />}
      </QueryState>
      <QueryState q={actions} empty="No containment actions." isEmpty={(d) => (d.actions ?? []).length === 0}>
        {(d) => (
          <ul className="space-y-2 text-sm" aria-label="Containment actions">
            {(d.actions ?? []).map((a) => (
              <li key={a.actionId} className="rounded-lg border border-border p-2">
                <span className="flex flex-wrap items-center gap-2">
                  <Badge tone={a.executed ? "danger" : "warn"}>{a.executed ? actionLabel(a.kind) : `${actionLabel(a.kind)} pending`}</Badge>
                  <span>{a.fromMode} → {a.toMode || a.fromMode}</span>
                  <span className="text-xs text-muted">{formatNs(a.decidedAt)}</span>
                </span>
                <span className="block text-muted">{a.reason}</span>
                {a.error && <span className="block text-live">{a.error}</span>}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </div>
  );
}

function PolicyForm({ id, initial, inherited }: { id: string; initial: ContainmentPolicyJson; inherited: boolean }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<ContainmentPolicyJson>(initial);
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: (p: ContainmentPolicyJson) =>
      api(`${M}/containment/policies/${encodeURIComponent(id)}`, { method: "PUT", body: { policy: p, reason } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["monitoring", id] }),
  });
  return (
    <form
      aria-label="Containment policy"
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(draft);
      }}
    >
      <p className="text-sm text-muted">
        {inherited ? "Inherits the default policy. Saving creates this strategy's own policy." : `Version ${initial.version ?? 0} by ${initial.updatedBy || "—"}.`}{" "}
        Loosening a threshold or weakening an action needs a passkey.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        {POLICY_FIELDS.map((f) => (
          <Field
            key={f.key}
            label={f.label}
            type="number"
            step={f.step}
            value={String(draft[f.key] ?? 0)}
            onChange={(e) => setDraft({ ...draft, [f.key]: Number(e.target.value) })}
          />
        ))}
        {(["breachAction", "severeAction"] as const).map((k) => (
          <Labeled key={k} label={k === "breachAction" ? "Breach action" : "Severe action"}>
            {(lid) => (
              <select id={lid} className="w-full rounded-md border border-border bg-bg px-2 py-1" value={draft[k] ?? ""} onChange={(e) => setDraft({ ...draft, [k]: e.target.value as (typeof ACTIONS)[number] })}>
                {ACTIONS.map((a) => (
                  <option key={a} value={a}>{actionLabel(a)}</option>
                ))}
              </select>
            )}
          </Labeled>
        ))}
        <Labeled label="Enabled">
          {(lid) => <input id={lid} type="checkbox" checked={draft.enabled ?? false} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />}
        </Labeled>
      </div>
      <Field label="Reason (audited)" value={reason} onChange={(e) => setReason(e.target.value)} />
      {save.isError && <Alert>{save.error instanceof ApiError ? save.error.message : "Saving failed"}</Alert>}
      {save.isSuccess && <Alert tone="ok">Policy saved.</Alert>}
      <Button type="submit" busy={save.isPending}>Save policy</Button>
    </form>
  );
}

function Labeled({ label, children }: { label: string; children: (id: string) => ReactNode }) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      {children(id)}
    </div>
  );
}

const SILENCES = [
  { label: "1 hour", seconds: 3600 },
  { label: "4 hours", seconds: 4 * 3600 },
  { label: "24 hours", seconds: 24 * 3600 },
];

export function AlertsCard() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("firing");
  const q = useQuery({
    queryKey: ["monitoring", "alerts", status],
    queryFn: () => api<ListAlertsResponseJson>(`${M}/alerts${status ? `?status=${status}` : ""}`),
    refetchInterval: 15_000,
  });
  const [silencing, setSilencing] = useState<AlertRecordJson | null>(null);
  const ack = useMutation({
    mutationFn: (key: string) => api(`${M}/alerts/${encodeURIComponent(key)}/ack`, { method: "POST", body: {} }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["monitoring", "alerts"] }),
  });
  return (
    <Card>
      <CardHeader
        title="Alerts"
        description="Critical alerts page through Pushover emergency priority until acknowledged in Pushover. Silencing needs a passkey and lasts at most 24 hours."
        action={
          <select aria-label="Alert status" className="rounded-md border border-border bg-bg px-2 py-1 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="firing">Firing</option>
            <option value="resolved">Resolved</option>
            <option value="">All</option>
          </select>
        }
      />
      <CardBody>
        <QueryState q={q} empty="No alerts." isEmpty={(d) => (d.alerts ?? []).length === 0}>
          {(d) => (
            <ul className="space-y-2" aria-label="Alert inbox">
              {(d.alerts ?? []).map((r) => {
                const key = r.alert?.dedupeKey ?? "";
                const sev = r.alert?.severity ?? "";
                return (
                  <li key={key} className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-border p-2 text-sm">
                    <div className="space-y-0.5">
                      <p className="flex items-center gap-2 font-medium">
                        <Badge tone={sev === "SEVERITY_CRITICAL" ? "danger" : sev === "SEVERITY_WARNING" ? "warn" : "neutral"}>{sev.replace("SEVERITY_", "").toLowerCase() || "—"}</Badge>
                        {r.alert?.subject}
                        {(r.count ?? 0) > 1 && <span className="text-xs text-muted">×{r.count}</span>}
                      </p>
                      <p className="text-muted">{r.alert?.message}</p>
                      <p className="text-xs text-muted">
                        {r.status} · last {formatNs(r.lastSeen)} · {r.origin}
                        {r.ackedBy && ` · acked by ${r.ackedBy}`}
                        {r.silenceId && ` · silenced until ${formatNs(r.silencedUntil)}`}
                        {r.alert?.runbookUrl && <> · <span className="font-mono">{r.alert.runbookUrl}</span></>}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      {!r.ackedBy && <Button size="sm" variant="secondary" busy={ack.isPending && ack.variables === key} onClick={() => ack.mutate(key)}>Acknowledge</Button>}
                      {r.status === "firing" && <Button size="sm" variant="secondary" onClick={() => setSilencing(r)}>Silence…</Button>}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </QueryState>
      </CardBody>
      {silencing && <SilenceDialog record={silencing} onClose={() => setSilencing(null)} />}
    </Card>
  );
}

function SilenceDialog({ record, onClose }: { record: AlertRecordJson; onClose: () => void }) {
  const qc = useQueryClient();
  const [seconds, setSeconds] = useState(3600);
  const [reason, setReason] = useState("");
  const key = record.alert?.dedupeKey ?? "";
  const m = useMutation({
    mutationFn: () => api(`${M}/alerts/${encodeURIComponent(key)}/silence`, { method: "POST", body: { durationSeconds: seconds, reason } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["monitoring", "alerts"] });
      onClose();
    },
  });
  return (
    <Dialog open onClose={onClose} title={`Silence: ${record.alert?.subject ?? key}`}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Labeled label="Duration">
          {(id) => (
            <select id={id} className="w-full rounded-md border border-border bg-bg px-2 py-1" value={seconds} onChange={(e) => setSeconds(Number(e.target.value))}>
              {SILENCES.map((s) => (
                <option key={s.seconds} value={s.seconds}>{s.label}</option>
              ))}
            </select>
          )}
        </Labeled>
        <Field label="Reason (audited)" required maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
        {m.isError && <Alert>{m.error instanceof ApiError ? m.error.message : "Silencing failed"}</Alert>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button type="submit" busy={m.isPending} disabled={!reason.trim()}>Silence (passkey)</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function DailyReportCard() {
  const [date, setDate] = useState("");
  const q = useQuery({
    queryKey: ["monitoring", "daily", date],
    queryFn: () => api<{ report?: DailyReportJson }>(`${M}/daily-reports${date ? `?date=${date}` : ""}`),
    retry: false,
  });
  return (
    <Card>
      <CardHeader
        title="Daily reports"
        description="Pre-market checklist and end-of-day summary."
        action={<input aria-label="Report date" type="date" className="rounded-md border border-border bg-bg px-2 py-1 text-xs" value={date} onChange={(e) => setDate(e.target.value)} />}
      />
      <CardBody>
        <QueryState q={q} empty="No report for this date." isEmpty={(d) => !d.report}>
          {(d) => (
            <div className="grid gap-4 md:grid-cols-2">
              <section aria-label="Pre-market checklist">
                <h3 className="mb-1 flex items-center gap-2 text-sm font-medium">
                  Pre-market {d.report?.date} <Badge tone={d.report?.premarketOk ? "ok" : "warn"}>{d.report?.premarketOk ? "ok" : "check"}</Badge>
                </h3>
                <pre className="whitespace-pre-wrap rounded-md bg-panel p-2 text-xs">{d.report?.premarket || "—"}</pre>
              </section>
              <section aria-label="End-of-day summary">
                <h3 className="mb-1 text-sm font-medium">End of day</h3>
                <pre className="whitespace-pre-wrap rounded-md bg-panel p-2 text-xs">{d.report?.eod || "—"}</pre>
              </section>
            </div>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}
