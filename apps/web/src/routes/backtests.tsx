import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Columns2, ExternalLink, FileBarChart2, FlaskConical } from "lucide-react";
import { useMemo, useState } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/form";
import { EmptyState, Page, PageHeader } from "@/components/ui/layout";
import { api } from "@/lib/api";
import { fmt, kv, R, seg, tearsheetEmbedUrl, tearsheetUrl, type RunResponse, type RunsResponse } from "@/lib/research";
import { enumLabel } from "@/lib/trading";
import { cn, formatNs } from "@/lib/utils";

/** Headline out-of-sample metrics shown in the run list, in this order when present. */
export const HEADLINE = ["sharpe", "net_return", "max_drawdown", "ic", "rank_ic", "turnover", "trades"] as const;

type Run = NonNullable<RunResponse["run"]>;

function runLabel(r: Run): string {
  return `${r.config?.family ?? "run"} · ${r.runId.slice(-8)}`;
}

/**
 * Backtests and tearsheets (UI-047): every training run's walk-forward backtest with its stitched
 * out-of-sample metrics, an in-app tearsheet viewer (sandboxed frame), and a side-by-side comparison
 * of two runs. Metrics are the ml service's; the page computes no statistic (UI-027).
 */
export function BacktestsPage() {
  const navigate = useNavigate();
  const runs = useQuery({ queryKey: ["research", "runs", 100], queryFn: () => api<RunsResponse>(`${R}/runs?limit=100`), refetchInterval: 30_000 });
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p.slice(-1), id]));

  return (
    <Page wide>
      <PageHeader
        eyebrow="Research"
        title="Backtests & tearsheets"
        description="Walk-forward backtests of every training run, after costs. Pick two runs to compare them side by side."
        actions={
          <Button
            variant="secondary"
            size="sm"
            disabled={picked.length !== 2}
            onClick={() => void navigate({ to: "/backtests/compare", search: { a: picked[0], b: picked[1] } })}
          >
            <Columns2 />
            Compare {picked.length}/2
          </Button>
        }
      />
      <Card>
        <CardHeader icon={<FileBarChart2 />} title="Runs" description="Newest first. Inner (selection) metrics are on each run's page; this list shows the stitched out-of-sample results." />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          <QueryState q={runs} empty="" isEmpty={() => false}>
            {(d) =>
              d.runs.length === 0 ? (
                <EmptyState
                  icon={<FlaskConical />}
                  title="No runs yet"
                  action={
                    <Link to="/training" className="text-xs font-medium text-info hover:underline">
                      Train a model
                    </Link>
                  }
                >
                  Every training run produces a walk-forward backtest and a tearsheet.
                </EmptyState>
              ) : (
                <table className="data-table" aria-label="Training runs">
                  <thead>
                    <tr>
                      <th className="pl-4 sm:pl-5">
                        <span className="sr-only">Select</span>
                      </th>
                      <th>Run</th>
                      <th>Status</th>
                      <th>Data</th>
                      {HEADLINE.map((k) => (
                        <th key={k} className="num">
                          {k.replace("_", " ")}
                        </th>
                      ))}
                      <th>Created</th>
                      <th className="pr-4 sm:pr-5">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.runs.map((r) => {
                      const m = kv(r.oosMetrics);
                      const on = picked.includes(r.runId);
                      return (
                        <tr key={r.runId} className={cn(on && "bg-panel-2/60")}>
                          <td className="pl-4 sm:pl-5">
                            <input type="checkbox" checked={on} onChange={() => toggle(r.runId)} aria-label={`Select ${runLabel(r)} for comparison`} className="size-4 accent-[var(--color-info)]" />
                          </td>
                          <td>
                            <Link to="/training/runs/$runId" params={{ runId: r.runId }} className="font-medium hover:underline">
                              {r.config?.family ?? "—"}
                            </Link>
                            <span className="ml-2 font-mono text-[11px] text-muted">{r.runId.slice(-8)}</span>
                          </td>
                          <td>
                            <Badge tone={r.status === "JOB_STATUS_SUCCEEDED" ? "ok" : r.status === "JOB_STATUS_FAILED" ? "danger" : "neutral"}>{enumLabel(r.status, "JOB_STATUS_")}</Badge>
                          </td>
                          <td className="max-w-[12rem] truncate text-xs text-muted" title={r.config?.dataSource}>
                            {r.config?.dataSource.startsWith("synthetic:") ? <Badge tone="neutral">synthetic</Badge> : (r.config?.timeframe ?? "—")}
                          </td>
                          {HEADLINE.map((k) => (
                            <td key={k} className="num font-mono">
                              {k in m ? fmt(m[k]) : "—"}
                            </td>
                          ))}
                          <td className="text-xs text-muted">{formatNs(r.createdAt)}</td>
                          <td className="pr-4 sm:pr-5">
                            <Link to="/backtests/runs/$runId" params={{ runId: r.runId }} className="text-xs font-medium text-info hover:underline">
                              Tearsheet
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )
            }
          </QueryState>
        </CardBody>
      </Card>
    </Page>
  );
}

/** Sandboxed tearsheet frame: no scripts, no same-origin access, no forms, no top navigation. */
export function TearsheetFrame({ runId, title, className }: { runId: string; title: string; className?: string }) {
  return (
    <iframe
      src={tearsheetEmbedUrl(runId)}
      title={title}
      sandbox=""
      referrerPolicy="no-referrer"
      loading="lazy"
      className={cn("w-full rounded-lg border border-border bg-white", className)}
    />
  );
}

export function TearsheetPage() {
  const { runId } = useParams({ from: "/app/backtests/runs/$runId" });
  const run = useQuery({ queryKey: ["research", "run", runId], queryFn: () => api<RunResponse>(`${R}/runs/${seg(runId)}`) });
  const r = run.data?.run ?? null;
  return (
    <Page wide>
      <PageHeader
        eyebrow={
          <Link to="/backtests" className="hover:text-fg">
            Backtests
          </Link>
        }
        title={r ? `Tearsheet · ${runLabel(r)}` : "Tearsheet"}
        description="Equity and drawdown, monthly returns, regimes, costs, and overfitting statistics as the backtester wrote them."
        actions={
          <>
            <Link to="/training/runs/$runId" params={{ runId }} className="inline-flex h-8 items-center rounded-lg border border-border px-3 text-xs font-medium hover:bg-panel-2">
              Run details
            </Link>
            <a href={tearsheetUrl(runId)} target="_blank" rel="noopener noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium hover:bg-panel-2">
              <ExternalLink className="size-3.5" aria-hidden />
              Open in new tab
            </a>
          </>
        }
      />
      {r && <RunMetricStrip r={r} />}
      <TearsheetFrame runId={runId} title={`Tearsheet for run ${runId}`} className="h-[calc(100dvh-16rem)] min-h-[480px]" />
    </Page>
  );
}

function RunMetricStrip({ r }: { r: Run }) {
  const m = kv(r.oosMetrics);
  const keys = HEADLINE.filter((k) => k in m);
  if (keys.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
      {keys.map((k) => (
        <div key={k} className="rounded-lg border border-border bg-panel px-3 py-2">
          <dt className="text-[11px] uppercase tracking-wider text-muted">{k.replace("_", " ")}</dt>
          <dd className="font-mono text-sm font-semibold">{fmt(m[k])}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Two runs side by side: every out-of-sample metric of either run, then both tearsheets. */
export function ComparePage() {
  const { a, b } = useSearch({ from: "/app/backtests/compare" });
  const ra = useQuery({ queryKey: ["research", "run", a], enabled: !!a, queryFn: () => api<RunResponse>(`${R}/runs/${seg(a!)}`) });
  const rb = useQuery({ queryKey: ["research", "run", b], enabled: !!b, queryFn: () => api<RunResponse>(`${R}/runs/${seg(b!)}`) });
  const A = ra.data?.run ?? null;
  const B = rb.data?.run ?? null;
  const keys = useMemo(() => [...new Set([...Object.keys(kv(A?.oosMetrics)), ...Object.keys(kv(B?.oosMetrics))])].sort(), [A, B]);

  if (!a || !b) {
    return (
      <Page>
        <PageHeader title="Compare runs" />
        <EmptyState title="Pick two runs on the Backtests page to compare them." />
      </Page>
    );
  }
  return (
    <Page wide>
      <PageHeader
        eyebrow={
          <Link to="/backtests" className="hover:text-fg">
            Backtests
          </Link>
        }
        title="Compare runs"
        description="Side by side as the ml service reports them; no difference statistic is computed here."
      />
      <Card>
        <CardHeader title="Out-of-sample metrics" />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          <table className="data-table" aria-label="Run comparison">
            <thead>
              <tr>
                <th className="pl-4 sm:pl-5">Metric</th>
                <th className="num">{A ? runLabel(A) : a}</th>
                <th className="num pr-4 sm:pr-5">{B ? runLabel(B) : b}</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["family", A?.config?.family, B?.config?.family],
                  ["timeframe", A?.config?.timeframe, B?.config?.timeframe],
                  ["data", A?.config?.dataSource, B?.config?.dataSource],
                  ["validation", A?.config?.splitter?.kind, B?.config?.splitter?.kind],
                ] as const
              ).map(([k, x, y]) => (
                <tr key={k}>
                  <td className="pl-4 text-muted sm:pl-5">{k}</td>
                  <td className="num">{x ?? "—"}</td>
                  <td className="num pr-4 sm:pr-5">{y ?? "—"}</td>
                </tr>
              ))}
              {keys.map((k) => (
                <tr key={k}>
                  <td className="pl-4 font-mono sm:pl-5">{k}</td>
                  <td className="num font-mono">{k in kv(A?.oosMetrics) ? fmt(kv(A?.oosMetrics)[k]) : "—"}</td>
                  <td className="num pr-4 font-mono sm:pr-5">{k in kv(B?.oosMetrics) ? fmt(kv(B?.oosMetrics)[k]) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <TearsheetFrame runId={a} title={`Tearsheet for run ${a}`} className="h-[70vh] min-h-[420px]" />
        <TearsheetFrame runId={b} title={`Tearsheet for run ${b}`} className="h-[70vh] min-h-[420px]" />
      </div>
    </Page>
  );
}
