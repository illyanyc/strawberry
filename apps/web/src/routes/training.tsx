import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type ReactNode, useMemo, useState } from "react";

import { BarList, LineChart } from "@/components/ResearchCharts";
import { SchemaForm, Select } from "@/components/SchemaForm";
import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field, Switch } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import {
  curveSeries,
  dateToNs,
  draftDefaults,
  type DiagnosticsResponse,
  type EstimateResponse,
  type FamiliesResponse,
  type FeatureSetsResponse,
  fmt,
  type HyperparamDraft,
  hyperparamErrors,
  hyperparamValues,
  type ImportanceResponse,
  isTerminalJob,
  kv,
  pct,
  type JobProgress,
  type JobResponse,
  type JobsResponse,
  type LabelPresetsResponse,
  type LineagesResponse,
  type ModelCardResponse,
  type ModelFamily,
  R,
  type RegisterResponse,
  type RunResponse,
  type RunsResponse,
  schemaFields,
  seg,
  severityTone,
  sortFindings,
  type SubmitJobResponse,
  tearsheetUrl,
  type TrainingConfigBody,
  useProgressStream,
} from "@/lib/research";
import { enumLabel } from "@/lib/trading";
import { formatNs } from "@/lib/utils";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function useFamilies() {
  return useQuery({ queryKey: ["research", "families"], queryFn: () => api<FamiliesResponse>(`${R}/families`), staleTime: 300_000 });
}

export function useLineages() {
  return useQuery({ queryKey: ["research", "lineages"], queryFn: () => api<LineagesResponse>(`${R}/lineages`) });
}

const SPLITTERS = [
  { value: "walk_forward", label: "Walk-forward (anchored)" },
  { value: "purged_kfold", label: "Purged k-fold" },
  { value: "cpcv", label: "Combinatorial purged CV" },
];

const PREFERRED_METRICS = ["oos_ic", "oos_rank_ic", "oos_auc", "oos_ndcg", "bt_sharpe", "bt_net_return"];

function headline(metrics: Record<string, number | string>): [string, number | string | undefined][] {
  const keys = PREFERRED_METRICS.filter((k) => k in metrics);
  return (keys.length ? keys : Object.keys(metrics)).slice(0, 3).map((k) => [k, metrics[k]]);
}

function defaultTimeframe(f: ModelFamily | undefined): string {
  return f?.horizon === "swing" ? "1d" : "15m";
}

/**
 * Training (section 14, UI-023): family picker with a schema-generated hyperparameter form, feature
 * set and label pickers, universe/timeframe/period, the cost estimate before launch (ML-031), and the
 * job and run lists. Launching needs no step-up; training never trades (EX-030).
 */
export function TrainingPage() {
  const families = useFamilies();
  const jobs = useQuery({ queryKey: ["research", "jobs"], queryFn: () => api<JobsResponse>(`${R}/training/jobs?limit=25`), refetchInterval: 5_000 });
  const runs = useQuery({ queryKey: ["research", "runs"], queryFn: () => api<RunsResponse>(`${R}/runs?limit=25`), refetchInterval: 15_000 });
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Training</h1>
      <Card>
        <CardHeader title="New training job" description="Validated and estimated before launch; diagnostics are written after every run." />
        <CardBody>
          <QueryState q={families} empty="No model families are available." isEmpty={(d) => d.families.length === 0}>
            {(d) => <TrainingForm families={d.families} />}
          </QueryState>
        </CardBody>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Jobs" />
          <CardBody className="overflow-x-auto">
            <QueryState q={jobs} empty="No jobs yet." isEmpty={(d) => d.jobs.length === 0}>
              {(d) => (
                <table className="w-full text-left text-sm">
                  <thead className="text-xs text-muted">
                    <tr>
                      <th className="py-2 pr-3 font-medium">Job</th>
                      <th className="py-2 pr-3 font-medium">Family</th>
                      <th className="py-2 pr-3 font-medium">Status</th>
                      <th className="py-2 pr-3 font-medium">By</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.jobs.map((j) => (
                      <tr key={j.jobId} className="border-t border-border">
                        <td className="py-2 pr-3">
                          <Link to="/training/jobs/$jobId" params={{ jobId: j.jobId }} className="font-mono text-xs underline-offset-2 hover:underline">
                            {j.jobId.slice(-10)}
                          </Link>
                          {j.kind !== "training" && <span className="ml-2 text-xs text-muted">{j.kind}</span>}
                        </td>
                        <td className="py-2 pr-3">{j.config?.family}</td>
                        <td className="py-2 pr-3">
                          <JobStatusBadge status={j.status} />
                          {!isTerminalJob(j.status) && <span className="ml-2 text-xs text-muted">{pct(j.progress, 0)}</span>}
                        </td>
                        <td className="py-2 pr-3 text-xs">{enumLabel(j.launchedBy?.actor, "ACTOR_")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </QueryState>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Runs" />
          <CardBody className="overflow-x-auto">
            <QueryState q={runs} empty="No runs yet." isEmpty={(d) => d.runs.length === 0}>
              {(d) => (
                <table className="w-full text-left text-sm">
                  <thead className="text-xs text-muted">
                    <tr>
                      <th className="py-2 pr-3 font-medium">Run</th>
                      <th className="py-2 pr-3 font-medium">Family</th>
                      <th className="py-2 pr-3 font-medium">Out-of-sample</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.runs.map((r) => (
                      <tr key={r.runId} className="border-t border-border">
                        <td className="py-2 pr-3">
                          <Link to="/training/runs/$runId" params={{ runId: r.runId }} className="font-mono text-xs underline-offset-2 hover:underline">
                            {r.runId.slice(-10)}
                          </Link>
                        </td>
                        <td className="py-2 pr-3">
                          {r.config?.family}
                          {r.config?.dataSource.startsWith("synthetic:") && (
                            <span className="ml-2">
                              <Badge tone="neutral">synthetic</Badge>
                            </span>
                          )}
                        </td>
                        <td className="py-2 pr-3 text-xs">
                          {headline(kv(r.oosMetrics)).map(([k, v]) => (
                            <span key={k} className="mr-3 whitespace-nowrap">
                              <span className="text-muted">{k}</span> {fmt(v)}
                            </span>
                          ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </QueryState>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

export function JobStatusBadge({ status }: { status: string }) {
  const label = enumLabel(status, "JOB_STATUS_");
  const tone = status === "JOB_STATUS_SUCCEEDED" ? "ok" : status === "JOB_STATUS_FAILED" ? "danger" : status === "JOB_STATUS_CANCELED" ? "neutral" : "paper";
  return <Badge tone={tone}>{label}</Badge>;
}

function TrainingForm({ families }: { families: ModelFamily[] }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const featureSets = useQuery({ queryKey: ["research", "feature-sets"], queryFn: () => api<FeatureSetsResponse>(`${R}/feature-sets`), staleTime: 300_000 });
  const presets = useQuery({ queryKey: ["research", "label-presets"], queryFn: () => api<LabelPresetsResponse>(`${R}/label-presets`), staleTime: 300_000 });
  const lineages = useLineages();

  const [familyName, setFamilyName] = useState(families.find((f) => f.family === "lgbm_regression")?.family ?? families[0]?.family ?? "");
  const family = families.find((f) => f.family === familyName);
  const { fields, unsupported } = useMemo(() => schemaFields(family?.hyperparamSchemaJson ?? ""), [family]);
  const [draft, setDraft] = useState<HyperparamDraft>(() => draftDefaults(fields));
  const [timeframe, setTimeframe] = useState(defaultTimeframe(family));
  const [featureSet, setFeatureSet] = useState("");
  const [preset, setPreset] = useState("");
  const [lineageId, setLineageId] = useState("");
  const [dataSource, setDataSource] = useState("lake");
  const [universe, setUniverse] = useState("");
  const [benchmark, setBenchmark] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [splitter, setSplitter] = useState("walk_forward");
  const [device, setDevice] = useState("auto");
  const [seed, setSeed] = useState("7");
  const [threads, setThreads] = useState("");
  const [eventBacktest, setEventBacktest] = useState(false);
  const [threshold, setThreshold] = useState("0.55");
  const [confirmOver, setConfirmOver] = useState(false);

  const pickFamily = (name: string) => {
    const f = families.find((x) => x.family === name);
    setFamilyName(name);
    setDraft(draftDefaults(schemaFields(f?.hyperparamSchemaJson ?? "").fields));
    setTimeframe(defaultTimeframe(f));
    setPreset("");
  };

  const hpErrors = hyperparamErrors(fields, draft);
  const lineage = lineages.data?.lineages.find((l) => l.lineageId === lineageId);
  const presetList = (presets.data?.presets ?? []).filter((p) => !p.timeframe || p.timeframe === timeframe);
  const chosenPreset = presetList.find((p) => p.name === preset);
  const isRule = family?.task === "rule_filter";
  const fs = featureSets.data?.featureSets.find((f) => `${f.name}@${f.version}` === featureSet);

  const config: TrainingConfigBody = {
    family: familyName,
    hyperparams: hyperparamValues(fields, draft),
    timeframe,
    featureSet: fs ? { name: fs.name, version: fs.version } : undefined,
    label: chosenPreset?.spec ? { ...chosenPreset.spec } : undefined,
    universe: universe || benchmark ? { name: universe, benchmark } : undefined,
    period: start || end ? { start: dateToNs(start), end: dateToNs(end) } : undefined,
    splitter: { kind: splitter },
    seed: /^\d+$/.test(seed) ? seed : undefined,
    resources: { device, cpuThreads: /^\d+$/.test(threads) ? Number(threads) : undefined },
    lineageId: lineageId || undefined,
    dataSource: lineage?.universe.startsWith("synthetic:") ? lineage.universe : dataSource,
    evaluation: { eventBacktest, longOnly: true },
    primary: isRule ? { threshold: Number(threshold) } : undefined,
  };
  const configKey = JSON.stringify(config);

  const estimate = useMutation({
    mutationFn: (body: TrainingConfigBody) => api<EstimateResponse>(`${R}/training/estimate`, { method: "POST", body }),
  });
  const estimatedFor = estimate.variables ? JSON.stringify(estimate.variables) : null;
  const fresh = estimate.isSuccess && estimatedFor === configKey;
  const est = fresh ? estimate.data : undefined;
  const overBudget = est?.estimate?.overBudget === true;
  const canSubmit = fresh && est !== undefined && est.errors.length === 0 && Object.keys(hpErrors).length === 0 && (!overBudget || confirmOver);

  const submit = useMutation({
    mutationFn: () => api<SubmitJobResponse>(`${R}/training/jobs`, { method: "POST", body: { config, confirmOverBudget: overBudget && confirmOver } }),
    onSuccess: (r) => {
      void queryClient.invalidateQueries({ queryKey: ["research", "jobs"] });
      if (r.job) void navigate({ to: "/training/jobs/$jobId", params: { jobId: r.job.jobId } });
    },
  });

  return (
    <form
      className="space-y-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit) submit.mutate();
        else estimate.mutate(config);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          label="Model family"
          value={familyName}
          onChange={pickFamily}
          options={families.map((f) => ({ value: f.family, label: `${f.family} (tier ${f.tier}, ${f.task}${f.experimental ? ", experimental" : ""})` }))}
          hint={family?.description}
        />
        <Select
          label="Timeframe"
          value={timeframe}
          onChange={setTimeframe}
          options={[
            { value: "15m", label: "15m (intraday)", disabled: family?.horizon === "swing" },
            { value: "1d", label: "1d (swing)", disabled: family?.horizon === "intraday" },
          ].filter((o) => !o.disabled)}
        />
        <Select
          label="Feature set"
          value={featureSet}
          onChange={setFeatureSet}
          options={[{ value: "", label: "Default for the timeframe" }, ...(featureSets.data?.featureSets ?? []).map((f) => ({ value: `${f.name}@${f.version}`, label: `${f.name} v${f.version} (${f.features.length} features)` }))]}
          hint={fs?.description}
        />
        <Select
          label="Label"
          value={preset}
          onChange={setPreset}
          options={[{ value: "", label: "Default for the family" }, ...presetList.map((p) => ({ value: p.name, label: p.name }))]}
          hint={chosenPreset?.description}
        />
      </div>
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Hyperparameters</h3>
        <SchemaForm fields={fields} unsupported={unsupported} draft={draft} errors={hpErrors} onChange={setDraft} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          label="Lineage (optional)"
          value={lineageId}
          onChange={setLineageId}
          options={[{ value: "", label: "None (ad-hoc run)" }, ...(lineages.data?.lineages ?? []).map((l) => ({ value: l.lineageId, label: `${l.name} (${l.family})` }))]}
          hint="Runs in a lineage are trials in its ledger and stop at its holdout boundary."
        />
        {lineage?.universe.startsWith("synthetic:") ? (
          <Field label="Data source" value={lineage.universe} readOnly hint="Set by the lineage." />
        ) : (
          <Field label="Data source" value={dataSource} onChange={(e) => setDataSource(e.target.value.trim())} hint="lake, or synthetic:<preset> for development (never live-eligible)." />
        )}
        <Field label="Universe" value={universe} onChange={(e) => setUniverse(e.target.value.trim())} hint="Named point-in-time universe; empty uses the source's." />
        <Field label="Benchmark instrument" value={benchmark} onChange={(e) => setBenchmark(e.target.value.trim())} hint="For residual labels; empty uses the universe's." />
        <Field label="Period start (UTC)" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        <Field label="Period end (UTC)" type="date" value={end} onChange={(e) => setEnd(e.target.value)} hint="Empty ends at the holdout boundary." />
        <Select label="Validation" value={splitter} onChange={setSplitter} options={SPLITTERS} hint="Embargo is raised to at least the label horizon." />
        <Select label="Device" value={device} onChange={setDevice} options={["auto", ...(family?.devices ?? [])].map((d) => ({ value: d, label: d }))} />
        <Field label="Seed" inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.trim())} />
        <Field label="CPU threads" inputMode="numeric" value={threads} onChange={(e) => setThreads(e.target.value.trim())} placeholder={String(family?.cpuThreadsHint ?? "")} />
        {isRule && <Field label="Filter threshold" inputMode="decimal" value={threshold} onChange={(e) => setThreshold(e.target.value.trim())} hint="Minimum filter probability to take a rule signal." />}
        <div className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
          <span className="text-xs text-muted">Event-driven tearsheet</span>
          <Switch checked={eventBacktest} onChange={setEventBacktest} label="Event-driven tearsheet" />
        </div>
      </div>

      {est && (
        <div className="space-y-2 rounded-lg border border-border bg-panel-2 p-3 text-sm" aria-live="polite">
          <p className="font-medium">Estimate</p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-5">
            <Metric label="Rows" v={est.estimate?.rows ?? "—"} />
            <Metric label="Fits" v={est.estimate?.fits ?? "—"} />
            <Metric label="CPU seconds" v={fmt(est.estimate?.cpuSeconds, 0)} />
            <Metric label="Memory (MB)" v={est.estimate?.memoryMb ?? "—"} />
            <Metric label="Device" v={est.estimate?.device ?? "—"} />
          </div>
          {est.errors.length > 0 && (
            <Alert>
              <ul className="list-disc pl-4">
                {est.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </Alert>
          )}
          {overBudget && (
            <label className="flex items-center gap-2 text-sm text-warn">
              <input type="checkbox" checked={confirmOver} onChange={(e) => setConfirmOver(e.target.checked)} />
              Over the compute budget: launch anyway
            </label>
          )}
        </div>
      )}
      {estimate.isError && <Alert>{errMsg(estimate.error, "Estimate failed")}</Alert>}
      {submit.isError && <Alert>{errMsg(submit.error, "Launch failed")}</Alert>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant={canSubmit ? "primary" : "secondary"} busy={estimate.isPending || submit.isPending} disabled={Object.keys(hpErrors).length > 0}>
          {canSubmit ? "Launch training" : "Estimate"}
        </Button>
        {canSubmit && (
          <Button variant="ghost" onClick={() => estimate.mutate(config)}>
            Re-estimate
          </Button>
        )}
      </div>
    </form>
  );
}

function Metric({ label, v }: { label: string; v: ReactNode }) {
  return (
    <div>
      <p className="text-xs text-muted">{label}</p>
      <p className="font-medium tabular-nums">{v}</p>
    </div>
  );
}

/** Live job progress (UI-023): phase, fold, boosting-step curves, metrics, and the log over SSE. */
export function TrainingJobPage() {
  const { jobId } = useParams({ from: "/app/training/jobs/$jobId" });
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: ["research", "job", jobId],
    queryFn: () => api<JobResponse>(`${R}/training/jobs/${seg(jobId)}`),
    refetchInterval: (q) => (isTerminalJob(q.state.data?.job?.status) ? false : 3_000),
  });
  const stream = useProgressStream<JobProgress>(`${R}/training/jobs/${seg(jobId)}/stream`);
  const cancel = useMutation({
    mutationFn: () => api(`${R}/training/jobs/${seg(jobId)}/cancel`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["research", "job", jobId] }),
  });
  const last = stream.events.at(-1);
  const folds = [...new Set(stream.events.filter((e) => e.step > 0).map((e) => e.fold))].sort((a, b) => a - b);
  const [fold, setFold] = useState<number | null>(null);
  const shownFold = fold ?? folds.at(-1) ?? 0;
  const logs = stream.events.filter((e) => e.log);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <QueryState q={job} empty="Job not found." isEmpty={(d) => d.job === null}>
        {({ job: j }) =>
          j && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-semibold tracking-tight">Job {j.jobId.slice(-10)}</h1>
                <JobStatusBadge status={j.status} />
                <span className="text-sm text-muted">{j.config?.family}</span>
                {!isTerminalJob(j.status) && (
                  <Button variant="danger" size="sm" className="ml-auto" busy={cancel.isPending} onClick={() => cancel.mutate()}>
                    Cancel job
                  </Button>
                )}
                {j.status === "JOB_STATUS_SUCCEEDED" && (
                  <Link to="/training/runs/$runId" params={{ runId: j.runId }} className="ml-auto text-sm font-medium text-paper underline-offset-2 hover:underline">
                    Open run →
                  </Link>
                )}
              </div>
              {cancel.isError && <Alert>{errMsg(cancel.error, "Cancel failed")}</Alert>}
              {j.error && <Alert>{j.error}</Alert>}
              <Card>
                <CardBody className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
                  <Metric label="Phase" v={last?.phase || j.phase || "—"} />
                  <Metric label="Progress" v={`${pct(last?.progress ?? j.progress, 0)}`} />
                  <Metric label="Fold" v={last && last.nFolds ? `${last.fold + 1} / ${last.nFolds}` : "—"} />
                  <Metric label="Submitted" v={formatNs(j.submittedAt)} />
                  <Metric label="Stream" v={stream.done ? "ended" : stream.connected ? "live" : "reconnecting…"} />
                </CardBody>
              </Card>
            </>
          )
        }
      </QueryState>
      <Card>
        <CardHeader
          title="Learning curve"
          description="Train and validation metric per boosting step for the selected fold."
          action={
            folds.length > 1 && (
              <Select label="Fold" value={String(shownFold)} onChange={(v) => setFold(Number(v))} options={folds.map((f) => ({ value: String(f), label: `Fold ${f + 1}` }))} />
            )
          }
        />
        <CardBody>
          <LineChart series={curveSeries(stream.events, shownFold)} label={`Learning curve for fold ${shownFold + 1}`} />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Log" />
        <CardBody>
          {logs.length === 0 ? (
            <p className="text-sm text-muted">No log lines yet.</p>
          ) : (
            <ol className="max-h-80 space-y-0.5 overflow-y-auto font-mono text-xs">
              {logs.map((e) => (
                <li key={e.seq}>
                  <span className="text-muted">{e.phase}</span> {e.log}
                  {Object.keys(kv(e.metrics)).length > 0 && (
                    <span className="text-muted">
                      {" "}
                      {Object.entries(kv(e.metrics))
                        .map(([k, v]) => `${k}=${fmt(v)}`)
                        .join(" ")}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

const IMPORTANCE = ["gain", "permutation", "shap"] as const;

/**
 * Run detail (UI-023, UI-025): out-of-sample and per-fold metrics, the DiagnosticsReport findings,
 * learning curves, importance with stability, error slices, the model card, registration, and the
 * tearsheet link (opened as its own sandboxed document).
 */
export function RunPage() {
  const { runId } = useParams({ from: "/app/training/runs/$runId" });
  const run = useQuery({ queryKey: ["research", "run", runId], queryFn: () => api<RunResponse>(`${R}/runs/${seg(runId)}`) });
  const diag = useQuery({ queryKey: ["research", "diag", runId], queryFn: () => api<DiagnosticsResponse>(`${R}/runs/${seg(runId)}/diagnostics`) });
  const card = useQuery({ queryKey: ["research", "card", runId], queryFn: () => api<ModelCardResponse>(`${R}/runs/${seg(runId)}/model-card`) });
  const [method, setMethod] = useState<(typeof IMPORTANCE)[number]>("gain");
  const imp = useQuery({ queryKey: ["research", "importance", runId, method], queryFn: () => api<ImportanceResponse>(`${R}/runs/${seg(runId)}/importance?method=${method}`) });

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <QueryState q={run} empty="Run not found." isEmpty={(d) => d.run === null}>
        {({ run: r }) =>
          r && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-semibold tracking-tight">Run {r.runId.slice(-10)}</h1>
                <span className="text-sm text-muted">{r.config?.family}</span>
                {r.config?.dataSource.startsWith("synthetic:") && <Badge tone="neutral">synthetic data</Badge>}
                <a href={tearsheetUrl(r.runId)} target="_blank" rel="noopener noreferrer" className="ml-auto text-sm font-medium text-paper underline-offset-2 hover:underline">
                  Tearsheet ↗
                </a>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Out-of-sample" description="Stitched over every test fold, after costs." />
                  <CardBody className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
                    {Object.entries(kv(r.oosMetrics))
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([k, v]) => (
                        <Metric key={k} label={k} v={fmt(v)} />
                      ))}
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="Inputs" />
                  <CardBody className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    <span className="text-muted">Dataset</span>
                    <span className="truncate font-mono text-xs">{r.dataset?.snapshotId ?? "—"}</span>
                    <span className="text-muted">Rows / instruments / eras</span>
                    <span>
                      {r.dataset?.rows ?? "—"} / {r.dataset?.instruments ?? "—"} / {r.dataset?.eras ?? "—"}
                    </span>
                    <span className="text-muted">Feature set</span>
                    <span>
                      {r.config?.featureSet?.name} v{r.config?.featureSet?.version}
                    </span>
                    <span className="text-muted">Label</span>
                    <span>
                      {r.config?.label?.kind} h={r.config?.label?.horizon}
                    </span>
                    <span className="text-muted">Validation</span>
                    <span>{r.config?.splitter?.kind}</span>
                    <span className="text-muted">Config hash</span>
                    <span className="truncate font-mono text-xs">{r.repro?.configHash ?? "—"}</span>
                    <span className="text-muted">Code commit</span>
                    <span className="truncate font-mono text-xs">{r.repro?.codeCommit || "—"}</span>
                    <span className="text-muted">Lineage / trial</span>
                    <span className="truncate font-mono text-xs">
                      {r.lineageId ? (
                        <Link to="/experiments/lineages/$lineageId" params={{ lineageId: r.lineageId }} className="underline-offset-2 hover:underline">
                          {r.lineageId.slice(-10)}
                        </Link>
                      ) : (
                        "—"
                      )}
                      {r.trialId && ` / ${r.trialId.slice(-10)}`}
                    </span>
                  </CardBody>
                </Card>
              </div>
              <Card>
                <CardHeader title="Folds" />
                <CardBody className="overflow-x-auto">
                  <FoldTable folds={r.folds} />
                </CardBody>
              </Card>
              <RegisterModel runId={r.runId} />
            </>
          )
        }
      </QueryState>
      <Card>
        <CardHeader title="Diagnostics" description="Training and validation folds only; the sealed holdout is never read." />
        <CardBody className="space-y-4">
          <QueryState q={diag} empty="No diagnostics report." isEmpty={(d) => d.report === null}>
            {({ report }) => report && <DiagnosticsView report={report} />}
          </QueryState>
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          title="Feature importance"
          description="Mean across folds with the fold-to-fold spread."
          action={<Select label="Method" value={method} onChange={(v) => setMethod(v as (typeof IMPORTANCE)[number])} options={IMPORTANCE.map((m) => ({ value: m, label: m }))} />}
        />
        <CardBody>
          <QueryState q={imp} empty="No importance computed." isEmpty={(d) => !d.importance || d.importance.stability.length === 0}>
            {({ importance }) =>
              importance && (
                <BarList
                  label={`${method} importance`}
                  items={[...importance.stability]
                    .sort((a, b) => Math.abs(Number(b.mean)) - Math.abs(Number(a.mean)))
                    .slice(0, 20)
                    .map((s) => ({
                      label: s.feature,
                      value: Number(s.mean) || 0,
                      spread: Number(s.std) || 0,
                      text: `${fmt(s.mean)} ± ${fmt(s.std)}${Number(s.signFlipRate) > 0 ? ` · flips ${fmt(s.signFlipRate, 2)}` : ""}`,
                    }))}
                />
              )
            }
          </QueryState>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Model card" />
        <CardBody>
          <QueryState q={card} empty="No model card." isEmpty={(d) => !d.card?.markdown}>
            {({ card: c }) => <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{c?.markdown}</pre>}
          </QueryState>
        </CardBody>
      </Card>
    </div>
  );
}

function FoldTable({ folds }: { folds: NonNullable<RunResponse["run"]>["folds"] }) {
  if (folds.length === 0) return <p className="text-sm text-muted">No folds.</p>;
  const mk = [...new Set(folds.flatMap((f) => Object.keys(kv(f.metrics))).filter((k) => k.startsWith("val_")))].sort();
  const bk = [...new Set(folds.flatMap((f) => Object.keys(kv(f.backtest))))].sort();
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-muted">
        <tr>
          <th className="py-1 pr-3 font-medium">Fold</th>
          <th className="py-1 pr-3 font-medium">Test window</th>
          <th className="py-1 pr-3 font-medium">Train/test rows</th>
          {mk.map((k) => (
            <th key={k} className="py-1 pr-3 font-medium">
              {k}
            </th>
          ))}
          {bk.map((k) => (
            <th key={k} className="py-1 pr-3 font-medium">
              bt {k}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {folds.map((f) => (
          <tr key={f.fold} className="border-t border-border tabular-nums">
            <td className="py-1 pr-3">{f.fold + 1}</td>
            <td className="py-1 pr-3 whitespace-nowrap">
              {formatNs(f.test?.start)} → {formatNs(f.test?.end)}
            </td>
            <td className="py-1 pr-3">
              {f.trainRows}/{f.testRows}
            </td>
            {mk.map((k) => (
              <td key={k} className="py-1 pr-3">
                {fmt(kv(f.metrics)[k])}
              </td>
            ))}
            {bk.map((k) => (
              <td key={k} className="py-1 pr-3">
                {fmt(kv(f.backtest)[k])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The DiagnosticsReport's rule-based findings with evidence, then curves and error slices (ML-023). */
export function DiagnosticsView({ report }: { report: NonNullable<DiagnosticsResponse["report"]> }) {
  const findings = sortFindings(report.findings);
  const curves = report.learningCurves?.perFold ?? [];
  return (
    <>
      {report.truncated && <Alert tone="warn">Lists were cut to the size limits.</Alert>}
      {findings.length === 0 ? (
        <Alert tone="ok">No findings.</Alert>
      ) : (
        <ul className="space-y-2">
          {findings.map((f, i) => (
            <li key={`${f.code}-${i}`} className="rounded-lg border border-border p-3 text-sm" data-finding={f.code}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={severityTone(f.severity)}>{enumLabel(f.severity, "SEVERITY_")}</Badge>
                <span className="font-mono text-xs">{f.code}</span>
              </div>
              <p className="mt-1">{f.message}</p>
              {f.evidence.length > 0 && (
                <p className="mt-1 text-xs text-muted">
                  {f.evidence.map((e) => `${e.name} ${fmt(e.value)} (threshold ${fmt(e.threshold)})`).join(" · ")}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
      {curves.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Learning curves ({curves[0]?.metric})</h3>
          <LineChart
            label="Validation metric per step, every fold"
            series={curves.map((c) => ({ name: `fold ${c.fold + 1}`, points: c.points.map((p) => [p.step, Number(p.valMetric)] as [number, number]).filter(([, y]) => Number.isFinite(y)) }))}
          />
          <p className="text-xs text-muted">Overfitting gap (train − validation at the chosen step): {fmt(report.learningCurves?.overfittingGap)}</p>
        </div>
      )}
      {report.errorSlices.length > 0 && (
        <div className="overflow-x-auto">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Error slices</h3>
          <table className="w-full text-left text-xs">
            <thead className="text-muted">
              <tr>
                <th className="py-1 pr-3 font-medium">By</th>
                <th className="py-1 pr-3 font-medium">Slice</th>
                <th className="py-1 pr-3 font-medium">n</th>
                <th className="py-1 pr-3 font-medium">Metric</th>
                <th className="py-1 pr-3 font-medium">vs overall</th>
              </tr>
            </thead>
            <tbody>
              {report.errorSlices.map((s) => (
                <tr key={`${s.sliceBy}-${s.slice}`} className="border-t border-border tabular-nums">
                  <td className="py-1 pr-3">{s.sliceBy}</td>
                  <td className="py-1 pr-3">{s.slice}</td>
                  <td className="py-1 pr-3">{s.n}</td>
                  <td className="py-1 pr-3">
                    {s.metricName} {fmt(s.metric)}
                  </td>
                  <td className="py-1 pr-3">{fmt(s.vsOverall)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function RegisterModel({ runId }: { runId: string }) {
  const [name, setName] = useState("");
  const reg = useMutation({ mutationFn: () => api<RegisterResponse>(`${R}/runs/${seg(runId)}/register`, { method: "POST", body: { name } }) });
  return (
    <Card>
      <CardHeader title="Register model" description="Creates a model version at stage candidate; promotion goes through a study's gates." />
      <CardBody>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            reg.mutate();
          }}
        >
          <div className="w-64">
            <Field label="Model name" value={name} onChange={(e) => setName(e.target.value.trim())} hint="Letters, digits, - and _" />
          </div>
          <Button type="submit" variant="secondary" disabled={!/^[A-Za-z0-9_-]{1,64}$/.test(name)} busy={reg.isPending}>
            Register
          </Button>
          {reg.isSuccess && reg.data.modelVersion && (
            <Alert tone="ok">
              Registered {reg.data.modelVersion.name} {reg.data.modelVersion.version}
            </Alert>
          )}
          {reg.isError && <Alert>{errMsg(reg.error, "Registration failed")}</Alert>}
        </form>
      </CardBody>
    </Card>
  );
}
