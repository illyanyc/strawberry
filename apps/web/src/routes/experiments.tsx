import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import { BarList, LineChart, ParallelCoordinates } from "@/components/ResearchCharts";
import { Select } from "@/components/SchemaForm";
import { Num, QueryState, Signed } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import {
  type Candidate,
  type CandidateResponse,
  type CandidatesResponse,
  type CheckResponse,
  dateToNs,
  evidenceUrl,
  fmt,
  type GateReportResponse,
  type HoldoutEvalResponse,
  type HoldoutResponse,
  isTerminalStudy,
  kv,
  type LedgerResponse,
  type Lineage,
  type LineageBody,
  type LineageResponse,
  nsToDate,
  numericParams,
  type OpenCycleResponse,
  pct,
  promotionTargets,
  R,
  seg,
  type Stage,
  stageOf,
  stageTone,
  type StatsResponse,
  type StudiesResponse,
  type StudyDiagnosticsResponse,
  type StudyProgress,
  type StudyResponse,
  type StudySpecBody,
  tearsheetUrl,
  type Trial,
  type TrialsResponse,
  useProgressStream,
} from "@/lib/research";
import { enumLabel, type StrategyPortfolio } from "@/lib/trading";
import { formatNs } from "@/lib/utils";
import { useFamilies, useLineages } from "@/routes/training";

const errMsg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function Metric({ label, v, title }: { label: string; v: ReactNode; title?: string }) {
  return (
    <div title={title}>
      <p className="text-xs text-muted">{label}</p>
      <p className="font-medium tabular-nums">{v}</p>
    </div>
  );
}

export function StageBadge({ stage }: { stage: string }) {
  const s = stageOf(stage);
  return <Badge tone={stageTone(s)}>{s.replace("_", " ")}</Badge>;
}

function StudyStatusBadge({ status }: { status: string }) {
  const tone = status === "STUDY_STATUS_COMPLETED" ? "ok" : status === "STUDY_STATUS_FAILED" ? "danger" : status === "STUDY_STATUS_RUNNING" ? "paper" : "neutral";
  return <Badge tone={tone}>{enumLabel(status, "STUDY_STATUS_")}</Badge>;
}

const AUTONOMY = ["0 · suggest only", "1 · auto-run on paper", "2 · auto-promote within limits"];

/** Research lineages (UI-024): each groups every trial for one family on one data domain. */
export function ExperimentsPage() {
  const lineages = useLineages();
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Experiments</h1>
      <Card>
        <CardHeader title="Research lineages" description="Every trial in a lineage counts toward its effective number of trials, whoever ran it." />
        <CardBody className="overflow-x-auto">
          <QueryState q={lineages} empty="No lineages yet. Create one to run studies." isEmpty={(d) => d.lineages.length === 0}>
            {(d) => (
              <table className="data-table">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-2 pr-3 font-medium">Lineage</th>
                    <th className="py-2 pr-3 font-medium">Family</th>
                    <th className="py-2 pr-3 font-medium">Data</th>
                    <th className="py-2 pr-3 font-medium">Holdout from</th>
                    <th className="py-2 pr-3 font-medium">Analyst</th>
                  </tr>
                </thead>
                <tbody>
                  {d.lineages.map((l) => (
                    <tr key={l.lineageId} className="border-t border-border">
                      <td className="py-2 pr-3">
                        <Link to="/experiments/lineages/$lineageId" params={{ lineageId: l.lineageId }} className="font-medium underline-offset-2 hover:underline">
                          {l.name || l.lineageId}
                        </Link>
                      </td>
                      <td className="py-2 pr-3">{l.family}</td>
                      <td className="py-2 pr-3 text-xs">
                        {l.timeframe} · {l.universe || "—"}
                      </td>
                      <td className="py-2 pr-3 text-xs">{nsToDate(l.holdoutBoundary)}</td>
                      <td className="py-2 pr-3 text-xs">level {l.autonomyLevel}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryState>
        </CardBody>
      </Card>
      <NewLineage />
    </div>
  );
}

function NewLineage() {
  const families = useFamilies();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [family, setFamily] = useState("lgbm_regression");
  const [timeframe, setTimeframe] = useState("15m");
  const [universe, setUniverse] = useState("");
  const [boundary, setBoundary] = useState("");
  const create = useMutation({
    mutationFn: () => {
      const body: LineageBody = { name, family, timeframe, universe, holdoutBoundary: dateToNs(boundary) };
      return api<LineageResponse>(`${R}/lineages`, { method: "POST", body });
    },
    onSuccess: (r) => {
      void queryClient.invalidateQueries({ queryKey: ["research", "lineages"] });
      if (r.lineage) void navigate({ to: "/experiments/lineages/$lineageId", params: { lineageId: r.lineage.lineageId } });
    },
  });
  return (
    <Card>
      <CardHeader title="New lineage" description="Starts at analyst level 0 (suggest only). The holdout boundary is fixed for its studies." />
      <CardBody>
        <form
          className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          <Select label="Family" value={family} onChange={setFamily} options={(families.data?.families ?? [{ family }]).map((f) => ({ value: f.family, label: f.family }))} />
          <Select
            label="Timeframe"
            value={timeframe}
            onChange={setTimeframe}
            options={[
              { value: "15m", label: "15m (intraday)" },
              { value: "1d", label: "1d (swing)" },
            ]}
          />
          <Field label="Universe" value={universe} onChange={(e) => setUniverse(e.target.value.trim())} hint="Named universe, or synthetic:<preset> for development." />
          <Field label="Holdout boundary (UTC)" type="date" value={boundary} onChange={(e) => setBoundary(e.target.value)} hint="Empty: the configured default." />
          <div className="flex items-end">
            <Button type="submit" disabled={name.trim() === ""} busy={create.isPending}>
              Create lineage
            </Button>
          </div>
          {create.isError && (
            <div className="sm:col-span-2 lg:col-span-5">
              <Alert>{errMsg(create.error, "Create failed")}</Alert>
            </div>
          )}
        </form>
      </CardBody>
    </Card>
  );
}

/**
 * Lineage detail (UI-024): trial ledger and lineage-level overfitting statistics (effective N, DSR,
 * PBO, MinTRL/MinBTL), the research cycle budget, the holdout counter, analyst autonomy, studies,
 * and candidates. Every number is the optimizer's; the UI never recomputes it.
 */
export function LineagePage() {
  const { lineageId } = useParams({ from: "/app/experiments/lineages/$lineageId" });
  const lin = useQuery({ queryKey: ["research", "lineage", lineageId], queryFn: () => api<LineageResponse>(`${R}/lineages/${seg(lineageId)}`) });
  const ledger = useQuery({ queryKey: ["research", "ledger", lineageId], queryFn: () => api<LedgerResponse>(`${R}/lineages/${seg(lineageId)}/ledger`), refetchInterval: 15_000 });
  const stats = useQuery({ queryKey: ["research", "stats", lineageId], queryFn: () => api<StatsResponse>(`${R}/lineages/${seg(lineageId)}/stats`), refetchInterval: 30_000 });
  const studies = useQuery({ queryKey: ["research", "studies", lineageId], queryFn: () => api<StudiesResponse>(`${R}/studies?lineage_id=${seg(lineageId)}`), refetchInterval: 10_000 });
  const cands = useQuery({ queryKey: ["research", "candidates", lineageId], queryFn: () => api<CandidatesResponse>(`${R}/candidates?lineage_id=${seg(lineageId)}`), refetchInterval: 15_000 });

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <QueryState q={lin} empty="Lineage not found." isEmpty={(d) => d.lineage === null}>
        {({ lineage: l }) =>
          l && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-semibold tracking-tight">{l.name || l.lineageId}</h1>
                <span className="text-sm text-muted">
                  {l.family} · {l.timeframe} · {l.universe || "—"}
                </span>
                {l.universe.startsWith("synthetic:") && <Badge tone="neutral">synthetic data</Badge>}
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Trial ledger" description="Abandoned and failed trials count too; each holdout look adds trials." />
                  <CardBody className="space-y-3 text-sm">
                    <QueryState q={ledger} empty="No ledger." isEmpty={(d) => d.ledger === null}>
                      {({ ledger: g }) =>
                        g && (
                          <>
                            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                              <Metric label="Trials (total)" v={g.trialsTotal} />
                              <Metric label="Holdout evaluations" v={g.holdoutEvaluations} />
                            </div>
                            <Counts label="By source" m={kv(g.bySource)} />
                            <Counts label="By status" m={kv(g.byStatus)} />
                            <Counts label="By dimension" m={kv(g.byDimension)} />
                          </>
                        )
                      }
                    </QueryState>
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="Lineage statistics" description="Deflated for every trial in the lineage (effective N)." />
                  <CardBody>
                    <QueryState q={stats} empty="No statistics yet." isEmpty={(d) => d.stats === null}>
                      {({ stats: s }) =>
                        s && (
                          <div className="space-y-3 text-sm">
                            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                              <Metric label="Trials / effective N" v={`${s.nTrials} / ${fmt(s.nEffective, 1)}`} />
                              <Metric label="Best Sharpe (per session)" v={fmt(s.bestSharpe)} />
                              <Metric label="Deflated Sharpe prob." v={`${fmt(s.deflatedSharpeProb)} (gate ${fmt(s.dsrThreshold, 2)})`} />
                              <Metric label="PBO" v={fmt(s.pbo)} />
                              <Metric label="Min track record (sessions)" v={fmt(s.minTrackRecordLength, 0)} />
                              <Metric label="MinBTL / available (years)" v={`${fmt(s.minBacktestLengthYears, 1)} / ${fmt(s.availableYears, 1)}`} />
                            </div>
                            {s.significanceReachable ? (
                              <Alert tone="ok">More trials can still reach significance.</Alert>
                            ) : (
                              <Alert tone="warn">Significance is no longer reachable with more trials in this lineage: stop searching or open a new cycle with more data.</Alert>
                            )}
                          </div>
                        )
                      }
                    </QueryState>
                  </CardBody>
                </Card>
                <CycleCard lineage={l} ledger={ledger.data?.ledger ?? null} />
                <HoldoutCard lineageId={l.lineageId} />
                <AutonomyCard lineage={l} />
                <RetrainCard lineageId={l.lineageId} />
              </div>
            </>
          )
        }
      </QueryState>
      <Card>
        <CardHeader title="Studies" />
        <CardBody className="overflow-x-auto">
          <QueryState q={studies} empty="No studies yet." isEmpty={(d) => d.studies.length === 0}>
            {(d) => (
              <table className="data-table">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-2 pr-3 font-medium">Study</th>
                    <th className="py-2 pr-3 font-medium">Status</th>
                    <th className="py-2 pr-3 font-medium">Trials</th>
                    <th className="py-2 pr-3 font-medium">Outer Sharpe (ann.)</th>
                    <th className="py-2 pr-3 font-medium">By</th>
                  </tr>
                </thead>
                <tbody>
                  {d.studies.map((s) => (
                    <tr key={s.studyId} className="border-t border-border">
                      <td className="py-2 pr-3">
                        <Link to="/experiments/studies/$studyId" params={{ studyId: s.studyId }} className="font-medium underline-offset-2 hover:underline">
                          {s.spec?.name || s.studyId.slice(-10)}
                        </Link>
                      </td>
                      <td className="py-2 pr-3">
                        <StudyStatusBadge status={s.status} />
                      </td>
                      <td className="py-2 pr-3 tabular-nums">
                        {s.trialsCompleted} done · {s.trialsPruned} pruned · {s.trialsFailed} failed
                      </td>
                      <td className="py-2 pr-3 tabular-nums">{s.outerEstimate ? fmt(s.outerEstimate.sharpeAnnualized, 2) : "—"}</td>
                      <td className="py-2 pr-3 text-xs">{enumLabel(s.createdBy?.actor, "ACTOR_")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryState>
        </CardBody>
      </Card>
      {lin.data?.lineage && <NewStudy lineage={lin.data.lineage} />}
      <Card>
        <CardHeader title="Candidates" description="Registry stages; live is reached only through the go-live flow." />
        <CardBody className="overflow-x-auto">
          <QueryState q={cands} empty="No candidates yet." isEmpty={(d) => d.candidates.length === 0}>
            {(d) => <CandidateTable candidates={d.candidates} champion={lin.data?.lineage?.championCandidateId ?? ""} />}
          </QueryState>
        </CardBody>
      </Card>
    </div>
  );
}

function Counts({ label, m }: { label: string; m: Record<string, number> }) {
  const entries = Object.entries(m).sort(([a], [b]) => a.localeCompare(b));
  return (
    <p className="text-xs">
      <span className="text-muted">{label}: </span>
      {entries.length === 0 ? "—" : entries.map(([k, v]) => `${k} ${v}`).join(" · ")}
    </p>
  );
}

function CandidateTable({ candidates, champion }: { candidates: Candidate[]; champion: string }) {
  return (
    <table className="data-table">
      <thead className="text-xs text-muted">
        <tr>
          <th className="py-2 pr-3 font-medium">Candidate</th>
          <th className="py-2 pr-3 font-medium">Stage</th>
          <th className="py-2 pr-3 font-medium">Origin</th>
          <th className="py-2 pr-3 font-medium">Shadow sessions</th>
          <th className="py-2 pr-3 font-medium">Created</th>
        </tr>
      </thead>
      <tbody>
        {candidates.map((c) => (
          <tr key={c.candidateId} className="border-t border-border">
            <td className="py-2 pr-3">
              <Link to="/experiments/candidates/$candidateId" params={{ candidateId: c.candidateId }} className="font-mono text-xs underline-offset-2 hover:underline">
                {c.candidateId.slice(-10)}
              </Link>
              {c.candidateId === champion && (
                <span className="ml-2">
                  <Badge tone="ok">champion</Badge>
                </span>
              )}
              {c.syntheticData && (
                <span className="ml-2">
                  <Badge tone="neutral">synthetic</Badge>
                </span>
              )}
            </td>
            <td className="py-2 pr-3">
              <StageBadge stage={c.stage} />
              {c.demoted && <span className="ml-2 text-xs text-warn">demoted</span>}
            </td>
            <td className="py-2 pr-3 text-xs">{c.origin || "training"}</td>
            <td className="py-2 pr-3 tabular-nums">{c.shadowSessions}</td>
            <td className="py-2 pr-3 text-xs">{formatNs(c.createdAt)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CycleCard({ lineage, ledger }: { lineage: Lineage; ledger: LedgerResponse["ledger"] }) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState("1.0");
  const cycle = ledger?.cycles.find((c) => c.cycleId === lineage.currentCycleId);
  const open = useMutation({
    mutationFn: () => api<OpenCycleResponse>(`${R}/lineages/${seg(lineage.lineageId)}/cycles`, { method: "POST", body: { targetSharpe: Number(target) } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["research", "ledger", lineage.lineageId] });
      void queryClient.invalidateQueries({ queryKey: ["research", "lineage", lineage.lineageId] });
    },
  });
  return (
    <Card>
      <CardHeader title="Research cycle" description="The effective-trial budget comes from the minimum backtest length for the data available." />
      <CardBody className="space-y-3 text-sm">
        {cycle ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Metric label="Status" v={enumLabel(cycle.status, "CYCLE_STATUS_")} />
            <Metric label="Effective trials used / budget" v={`${fmt(cycle.effectiveTrialsUsed, 1)} / ${fmt(cycle.effectiveTrialBudget, 1)}`} />
            <Metric label="Target Sharpe (ann.) · years" v={`${fmt(cycle.targetSharpe, 2)} · ${fmt(cycle.availableYears, 1)}`} />
          </div>
        ) : (
          <p className="text-muted">No open cycle: studies are not budgeted until one is opened.</p>
        )}
        {cycle?.status === "CYCLE_STATUS_SPENT" && <Alert tone="warn">This cycle's budget is spent. Only you can open a new one.</Alert>}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            open.mutate();
          }}
        >
          <div className="w-40">
            <Field label="Target annual Sharpe" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value.trim())} />
          </div>
          <Button type="submit" variant="secondary" busy={open.isPending} disabled={!(Number(target) > 0)}>
            Open new cycle
          </Button>
        </form>
        <p className="text-xs text-muted">Needs a passkey.</p>
        {open.isError && <Alert>{errMsg(open.error, "Could not open a cycle")}</Alert>}
      </CardBody>
    </Card>
  );
}

function useHoldout(lineageId: string) {
  return useQuery({ queryKey: ["research", "holdout", lineageId], queryFn: () => api<HoldoutResponse>(`${R}/lineages/${seg(lineageId)}/holdout`) });
}

/** Holdout access counter (UI-026): touches this quarter, remaining, trial inflation per look. */
function HoldoutCard({ lineageId }: { lineageId: string }) {
  const queryClient = useQueryClient();
  const holdout = useHoldout(lineageId);
  const [date, setDate] = useState("");
  const roll = useMutation({
    mutationFn: () => api<HoldoutResponse>(`${R}/lineages/${seg(lineageId)}/holdout/boundary`, { method: "POST", body: { boundary: dateToNs(date) } }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["research", "holdout", lineageId] }),
  });
  return (
    <Card>
      <CardHeader title="Sealed holdout" description="Pass/fail only, at most one look per family per quarter." />
      <CardBody className="space-y-3 text-sm">
        <QueryState q={holdout} empty="No holdout status." isEmpty={(d) => d.status === null}>
          {({ status: h }) =>
            h && (
              <>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="holdout-counter">
                  <Metric label="Boundary" v={nsToDate(h.boundary)} />
                  <Metric label="Looks this quarter" v={h.touchesThisQuarter} />
                  <Metric label="Remaining" v={h.remainingThisQuarter} />
                  <Metric label="Trials added per look" v={h.trialInflationPerTouch} />
                </div>
                {h.touches.length > 0 && (
                  <ul className="space-y-1 text-xs">
                    {h.touches.map((t) => (
                      <li key={`${t.candidateId}-${t.at}`}>
                        {t.quarter} · {formatNs(t.at)} · {t.candidateId.slice(-10)} · <span className="text-muted">{t.reason}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )
          }
        </QueryState>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            roll.mutate();
          }}
        >
          <div className="w-44">
            <Field label="Roll boundary forward to" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <Button type="submit" variant="secondary" disabled={!dateToNs(date)} busy={roll.isPending}>
            Roll forward
          </Button>
        </form>
        <p className="text-xs text-muted">Needs a passkey; a spent holdout is never reopened.</p>
        {roll.isError && <Alert>{errMsg(roll.error, "Boundary change failed")}</Alert>}
      </CardBody>
    </Card>
  );
}

function AutonomyCard({ lineage }: { lineage: Lineage }) {
  const queryClient = useQueryClient();
  const [level, setLevel] = useState(String(lineage.autonomyLevel));
  const set = useMutation({
    mutationFn: () => api<LineageResponse>(`${R}/lineages/${seg(lineage.lineageId)}/autonomy`, { method: "PUT", body: { level: Number(level) } }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["research", "lineage", lineage.lineageId] }),
  });
  const raising = Number(level) > lineage.autonomyLevel;
  const b = lineage.analystBudget;
  const u = lineage.analystUsed;
  return (
    <Card>
      <CardHeader title="AI analyst autonomy" description="Raising needs a passkey; lowering is immediate. The analyst never touches the holdout or live." />
      <CardBody className="space-y-3 text-sm">
        <div className="grid grid-cols-2 gap-3">
          <Metric label="Current" v={AUTONOMY[lineage.autonomyLevel] ?? lineage.autonomyLevel} />
          <Metric label="Analyst trials used / budget" v={`${u?.trials ?? 0} / ${b?.maxTrials || "—"}`} />
        </div>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            set.mutate();
          }}
        >
          <div className="w-64">
            <Select label="Level" value={level} onChange={setLevel} options={AUTONOMY.map((label, i) => ({ value: String(i), label }))} />
          </div>
          <Button type="submit" variant={raising ? "danger" : "secondary"} disabled={Number(level) === lineage.autonomyLevel} busy={set.isPending}>
            {raising ? "Raise (passkey)" : "Lower"}
          </Button>
        </form>
        {set.isError && <Alert>{errMsg(set.error, "Autonomy change failed")}</Alert>}
      </CardBody>
    </Card>
  );
}

function RetrainCard({ lineageId }: { lineageId: string }) {
  const queryClient = useQueryClient();
  const retrain = useMutation({
    mutationFn: () => api<StudyResponse>(`${R}/lineages/${seg(lineageId)}/retrain`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["research", "studies", lineageId] }),
  });
  return (
    <Card>
      <CardHeader title="Retraining" description="A retrain is a new study whose candidate is a challenger; it never replaces the champion without gates." />
      <CardBody className="space-y-3 text-sm">
        <Button variant="secondary" busy={retrain.isPending} onClick={() => retrain.mutate()}>
          Retrain now
        </Button>
        {retrain.isSuccess && retrain.data.study && (
          <Alert tone="ok">
            Retrain study{" "}
            <Link to="/experiments/studies/$studyId" params={{ studyId: retrain.data.study.studyId }} className="underline">
              {retrain.data.study.studyId.slice(-10)}
            </Link>{" "}
            created.
          </Alert>
        )}
        {retrain.isError && <Alert>{errMsg(retrain.error, "Retrain failed")}</Alert>}
      </CardBody>
    </Card>
  );
}

interface SpaceRow {
  name: string;
  kind: string;
  low: string;
  high: string;
  choices: string;
}

const METRICS = ["sharpe", "ic", "rank_ic", "net_return"];

/** Study creation with pre-registration (UI-024, EX-026): hypothesis, metric, and stop rule are required. */
function NewStudy({ lineage }: { lineage: Lineage }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("sharpe");
  const [hypothesis, setHypothesis] = useState("");
  const [stopRule, setStopRule] = useState("budget");
  const [trials, setTrials] = useState("12");
  const [sampler, setSampler] = useState("tpe");
  const [pruner, setPruner] = useState("median");
  const [topK, setTopK] = useState("1");
  const [space, setSpace] = useState<SpaceRow[]>([]);
  const [startNow, setStartNow] = useState(true);

  const spaceErrors = space.map((r) => {
    if (!/^[A-Za-z0-9_]{1,64}$/.test(r.name)) return "name";
    if (r.kind === "categorical") return r.choices.trim() === "" ? "choices" : "";
    const lo = Number(r.low);
    const hi = Number(r.high);
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo >= hi) return "range";
    if (r.kind === "log_float" && lo <= 0) return "log range";
    return "";
  });
  const valid = name.trim() !== "" && hypothesis.trim() !== "" && stopRule.trim() !== "" && /^\d+$/.test(trials) && Number(trials) > 0 && spaceErrors.every((e) => e === "");

  const create = useMutation({
    mutationFn: async () => {
      const spec: StudySpecBody = {
        name,
        lineageId: lineage.lineageId,
        families: [lineage.family],
        timeframe: lineage.timeframe,
        objective: { metric, direction: metric === "max_drawdown" ? "minimize" : "maximize" },
        sampler,
        pruner,
        budget: { maxTrials: Number(trials) },
        evaluation: { eventDrivenTopK: Number(topK) || 1 },
        preregistration: { hypothesis, metric, universe: lineage.universe, stopRule },
        searchSpace: space.map((r) =>
          r.kind === "categorical"
            ? { name: r.name, kind: r.kind, choices: r.choices.split(",").map((c) => c.trim()).filter(Boolean) }
            : { name: r.name, kind: r.kind, low: Number(r.low), high: Number(r.high) },
        ),
      };
      const res = await api<StudyResponse>(`${R}/studies`, { method: "POST", body: spec });
      if (startNow && res.study) await api<StudyResponse>(`${R}/studies/${seg(res.study.studyId)}/start`, { method: "POST" });
      return res;
    },
    onSuccess: (r) => {
      void queryClient.invalidateQueries({ queryKey: ["research", "studies", lineage.lineageId] });
      if (r.study) void navigate({ to: "/experiments/studies/$studyId", params: { studyId: r.study.studyId } });
    },
  });

  return (
    <Card>
      <CardHeader title="New study" description="Nested evaluation: the inner search selects, the outer walk-forward estimates. The metric cannot change after launch." />
      <CardBody>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) create.mutate();
          }}
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
            <Select label="Objective metric" value={metric} onChange={setMetric} options={METRICS.map((m) => ({ value: m, label: m }))} />
            <Field label="Trial budget" inputMode="numeric" value={trials} onChange={(e) => setTrials(e.target.value.trim())} />
            <Field label="Event-driven top-k" inputMode="numeric" value={topK} onChange={(e) => setTopK(e.target.value.trim())} hint="Rerun event-driven before any gate." />
            <Select
              label="Sampler"
              value={sampler}
              onChange={setSampler}
              options={[
                { value: "tpe", label: "TPE" },
                { value: "random", label: "Random" },
              ]}
            />
            <Select
              label="Pruner"
              value={pruner}
              onChange={setPruner}
              options={[
                { value: "median", label: "Median" },
                { value: "asha", label: "ASHA" },
                { value: "none", label: "None" },
              ]}
            />
            <Field label="Stop rule" value={stopRule} onChange={(e) => setStopRule(e.target.value)} hint="e.g. budget, or plateau after 20 trials" />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="study-hypothesis" className="block text-xs font-medium text-muted">
              Hypothesis (pre-registered)
            </label>
            <textarea
              id="study-hypothesis"
              className="min-h-20 w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm"
              value={hypothesis}
              onChange={(e) => setHypothesis(e.target.value)}
              maxLength={2000}
            />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Search space</h3>
              <Button size="sm" variant="ghost" onClick={() => setSpace([...space, { name: "", kind: "float", low: "", high: "", choices: "" }])}>
                Add parameter
              </Button>
            </div>
            {space.length === 0 && <p className="text-xs text-muted">Empty: the family's default space.</p>}
            {space.map((r, i) => (
              <div key={i} className="grid items-end gap-2 sm:grid-cols-6">
                <Field label="Parameter" value={r.name} onChange={(e) => setSpace(space.map((x, j) => (j === i ? { ...x, name: e.target.value.trim() } : x)))} error={spaceErrors[i] === "name" ? "Letters, digits, _" : null} />
                <Select
                  label="Kind"
                  value={r.kind}
                  onChange={(v) => setSpace(space.map((x, j) => (j === i ? { ...x, kind: v } : x)))}
                  options={["float", "log_float", "int", "categorical"].map((k) => ({ value: k, label: k }))}
                />
                {r.kind === "categorical" ? (
                  <div className="sm:col-span-2">
                    <Field label="Choices (comma separated)" value={r.choices} onChange={(e) => setSpace(space.map((x, j) => (j === i ? { ...x, choices: e.target.value } : x)))} error={spaceErrors[i] === "choices" ? "Required" : null} />
                  </div>
                ) : (
                  <>
                    <Field label="Low" inputMode="decimal" value={r.low} onChange={(e) => setSpace(space.map((x, j) => (j === i ? { ...x, low: e.target.value.trim() } : x)))} error={spaceErrors[i]?.includes("range") ? "low < high (> 0 for log)" : null} />
                    <Field label="High" inputMode="decimal" value={r.high} onChange={(e) => setSpace(space.map((x, j) => (j === i ? { ...x, high: e.target.value.trim() } : x)))} />
                  </>
                )}
                <Button size="sm" variant="ghost" onClick={() => setSpace(space.filter((_, j) => j !== i))}>
                  Remove
                </Button>
              </div>
            ))}
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={startNow} onChange={(e) => setStartNow(e.target.checked)} />
            Start immediately
          </label>
          {create.isError && <Alert>{errMsg(create.error, "Study launch failed")}</Alert>}
          <Button type="submit" disabled={!valid} busy={create.isPending}>
            {startNow ? "Create and start study" : "Create study"}
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

/**
 * Study detail (UI-024): controls, pre-registration, live progress over SSE, the honest outer
 * estimate (inner metrics are labelled selection-only), trials with parallel coordinates, parameter
 * importance and stability, convergence (raw vs deflated), and the Pareto front.
 */
export function StudyPage() {
  const { studyId } = useParams({ from: "/app/experiments/studies/$studyId" });
  const queryClient = useQueryClient();
  const study = useQuery({
    queryKey: ["research", "study", studyId],
    queryFn: () => api<StudyResponse>(`${R}/studies/${seg(studyId)}`),
    refetchInterval: (q) => (isTerminalStudy(q.state.data?.study?.status) ? false : 3_000),
  });
  const status = study.data?.study?.status;
  const trials = useQuery({
    queryKey: ["research", "trials", studyId],
    queryFn: () => api<TrialsResponse>(`${R}/studies/${seg(studyId)}/trials?limit=2000`),
    refetchInterval: isTerminalStudy(status) ? false : 5_000,
  });
  const diag = useQuery({ queryKey: ["research", "study-diag", studyId, status], queryFn: () => api<StudyDiagnosticsResponse>(`${R}/studies/${seg(studyId)}/diagnostics`) });
  const stream = useProgressStream<StudyProgress>(`${R}/studies/${seg(studyId)}/stream`);
  const act = useMutation({
    mutationFn: (action: "start" | "pause" | "resume" | "stop") => api<StudyResponse>(`${R}/studies/${seg(studyId)}/${action}`, { method: "POST" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["research", "study", studyId] }),
  });
  const last = stream.events.at(-1);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <QueryState q={study} empty="Study not found." isEmpty={(d) => d.study === null}>
        {({ study: s }) =>
          s && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="text-2xl font-semibold tracking-tight">{s.spec?.name || s.studyId}</h1>
                <StudyStatusBadge status={s.status} />
                {s.spec?.lineageId && (
                  <Link to="/experiments/lineages/$lineageId" params={{ lineageId: s.spec.lineageId }} className="text-sm text-muted underline-offset-2 hover:underline">
                    lineage
                  </Link>
                )}
                <div className="ml-auto flex flex-wrap gap-2">
                  {s.status === "STUDY_STATUS_CREATED" && (
                    <Button size="sm" busy={act.isPending} onClick={() => act.mutate("start")}>
                      Start
                    </Button>
                  )}
                  {s.status === "STUDY_STATUS_RUNNING" && (
                    <Button size="sm" variant="secondary" busy={act.isPending} onClick={() => act.mutate("pause")}>
                      Pause
                    </Button>
                  )}
                  {s.status === "STUDY_STATUS_PAUSED" && (
                    <Button size="sm" busy={act.isPending} onClick={() => act.mutate("resume")}>
                      Resume
                    </Button>
                  )}
                  {(s.status === "STUDY_STATUS_RUNNING" || s.status === "STUDY_STATUS_PAUSED") && (
                    <Button size="sm" variant="danger" busy={act.isPending} onClick={() => act.mutate("stop")}>
                      Stop
                    </Button>
                  )}
                </div>
              </div>
              {act.isError && <Alert>{errMsg(act.error, "Study action failed")}</Alert>}
              {s.message && <Alert tone={s.status === "STUDY_STATUS_FAILED" ? "danger" : "warn"}>{s.message}</Alert>}
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Progress" description={stream.done ? "Stream ended." : stream.connected ? "Live." : "Connecting…"} />
                  <CardBody className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
                    <Metric label="Trials completed" v={last?.trialsCompleted ?? s.trialsCompleted} />
                    <Metric label="Pruned / failed" v={`${s.trialsPruned} / ${s.trialsFailed}`} />
                    <Metric label="Lineage trials" v={s.trialCountTotal} />
                    <Metric label="Outer fold" v={last ? (last.outerFold < 0 ? "final selection" : last.outerFold + 1) : "—"} />
                    <Metric label="Best inner (selection only)" v={fmt(last?.bestInner ?? s.bestTrials[0]?.innerMetric)} />
                    <Metric label="Budget used" v={`${s.budgetUsed?.trials ?? 0} trials · ${fmt(s.budgetUsed?.cpuHours, 2)} CPU h`} />
                    {last?.message && (
                      <p className="col-span-full text-xs text-muted" aria-live="polite">
                        {last.message}
                      </p>
                    )}
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="Pre-registration" description={`Spec ${s.specHash}`} />
                  <CardBody className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                    <span className="text-muted">Hypothesis</span>
                    <span className="whitespace-pre-wrap">{s.spec?.preregistration?.hypothesis || "—"}</span>
                    <span className="text-muted">Metric</span>
                    <span>{s.spec?.preregistration?.metric || "—"}</span>
                    <span className="text-muted">Stop rule</span>
                    <span>{s.spec?.preregistration?.stopRule || "—"}</span>
                    <span className="text-muted">Period</span>
                    <span>
                      {nsToDate(s.spec?.period?.start)} → {nsToDate(s.spec?.period?.end)}
                    </span>
                    <span className="text-muted">Families</span>
                    <span>{s.spec?.families.join(", ")}</span>
                  </CardBody>
                </Card>
              </div>
              <Card>
                <CardHeader title="Outer estimate" description="The estimate of the whole selection procedure on outer walk-forward folds, after costs. Inner metrics are for selection only." />
                <CardBody className="space-y-3 text-sm">
                  {s.outerEstimate ? (
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <Metric label="Sharpe (annualized)" v={fmt(s.outerEstimate.sharpeAnnualized, 2)} />
                      <Metric label="95% interval" v={`${fmt(s.outerEstimate.ciLow, 2)} … ${fmt(s.outerEstimate.ciHigh, 2)}`} />
                      <Metric label="Net return" v={pct(s.outerEstimate.netReturn)} />
                      <Metric label="Max drawdown" v={pct(s.outerEstimate.maxDrawdown)} />
                      <Metric label="Positive folds" v={pct(s.outerEstimate.positiveFoldFraction, 0)} />
                      <Metric label="Trades" v={s.outerEstimate.trades} />
                      <Metric label="Deflated Sharpe prob." v={fmt(s.deflatedSharpe)} />
                      <Metric label="PBO" v={fmt(s.pbo)} />
                    </div>
                  ) : (
                    <p className="text-muted">Available when the study finishes.</p>
                  )}
                  {s.candidateId && (
                    <Link to="/experiments/candidates/$candidateId" params={{ candidateId: s.candidateId }} className="inline-block font-medium text-paper underline-offset-2 hover:underline">
                      Candidate and gate report →
                    </Link>
                  )}
                </CardBody>
              </Card>
            </>
          )
        }
      </QueryState>
      <Card>
        <CardHeader title="Trials" description="Parallel coordinates of numeric parameters; the best 10% by the inner metric are highlighted." />
        <CardBody className="space-y-4">
          <QueryState q={trials} empty="No trials yet." isEmpty={(d) => d.trials.length === 0}>
            {(d) => <TrialsView trials={d.trials} />}
          </QueryState>
        </CardBody>
      </Card>
      <QueryState q={diag} empty="" isEmpty={(d) => d.diagnostics === null}>
        {({ diagnostics: g }) =>
          g && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Convergence" description={`Best so far, raw vs deflated for the trial count. Plateau near trial ${g.plateauEstimate}.`} />
                <CardBody>
                  <LineChart
                    label="Best trial so far, raw and deflated"
                    series={[
                      { name: "best raw", points: g.convergence.map((p) => [p.trial, Number(p.bestRaw)] as [number, number]).filter(([, y]) => Number.isFinite(y)) },
                      { name: "best deflated", points: g.convergence.map((p) => [p.trial, Number(p.bestDeflated)] as [number, number]).filter(([, y]) => Number.isFinite(y)) },
                    ]}
                  />
                  <p className="text-xs text-muted">
                    Pruned {pct(g.prunedRate, 0)} · failed {pct(g.failureRate, 0)}
                  </p>
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Parameter importance" />
                <CardBody className="space-y-3">
                  <BarList
                    label="Parameter importance"
                    items={Object.entries(kv(g.paramImportance))
                      .map(([k, v]) => ({ label: k, value: Number(v) || 0, text: fmt(v) }))
                      .sort((a, b) => b.value - a.value)}
                  />
                  {g.paramStability.length > 0 && (
                    <table className="data-table text-xs">
                      <thead className="text-muted">
                        <tr>
                          <th className="py-1 pr-3 font-medium">Parameter</th>
                          <th className="py-1 pr-3 font-medium">Best</th>
                          <th className="py-1 pr-3 font-medium">Neighbours mean ± std</th>
                        </tr>
                      </thead>
                      <tbody>
                        {g.paramStability.map((p) => (
                          <tr key={p.param} className="border-t border-border tabular-nums">
                            <td className="py-1 pr-3">{p.param}</td>
                            <td className="py-1 pr-3">{fmt(p.bestValue)}</td>
                            <td className="py-1 pr-3">
                              {fmt(p.neighborMean)} ± {fmt(p.neighborStd)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardBody>
              </Card>
              {g.paretoFront.length > 0 && (
                <Card className="lg:col-span-2">
                  <CardHeader title="Pareto front" />
                  <CardBody className="overflow-x-auto">
                    <ParetoTable points={g.paretoFront} />
                  </CardBody>
                </Card>
              )}
            </div>
          )
        }
      </QueryState>
    </div>
  );
}

function ParetoTable({ points }: { points: NonNullable<StudyDiagnosticsResponse["diagnostics"]>["paretoFront"] }) {
  const keys = [...new Set(points.flatMap((p) => Object.keys(kv(p.values))))].sort();
  return (
    <table className="data-table text-xs">
      <thead className="text-muted">
        <tr>
          <th className="py-1 pr-3 font-medium">Trial</th>
          {keys.map((k) => (
            <th key={k} className="py-1 pr-3 font-medium">
              {k}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {points.map((p) => (
          <tr key={p.trialId} className="border-t border-border tabular-nums">
            <td className="py-1 pr-3 font-mono">{p.trialId.slice(-10)}</td>
            {keys.map((k) => (
              <td key={k} className="py-1 pr-3">
                {fmt(kv(p.values)[k])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function paramText(params: Trial["params"]): string {
  return Object.entries(params ?? {})
    .map(([k, v]) => `${k}=${typeof v === "number" ? fmt(v) : String(v)}`)
    .join(" ");
}

function TrialsView({ trials }: { trials: Trial[] }) {
  const done = trials.filter((t) => t.status === "TRIAL_STATUS_COMPLETE");
  const axes = numericParams(done).slice(0, 8);
  const rows = done.map((t) => {
    const r: Record<string, number> = { inner: Number(t.innerMetric) };
    for (const a of axes) {
      const v = (t.params ?? {})[a];
      r[a] = typeof v === "number" ? v : NaN;
    }
    return r;
  });
  return (
    <>
      <ParallelCoordinates rows={rows} axes={axes} metric="inner" label="Parallel coordinates of trial parameters and the inner metric" />
      <div className="max-h-96 overflow-auto">
        <table className="data-table text-xs">
          <thead className="sticky top-0 bg-panel text-muted">
            <tr>
              <th className="py-1 pr-3 font-medium">#</th>
              <th className="py-1 pr-3 font-medium">Outer fold</th>
              <th className="py-1 pr-3 font-medium">Status</th>
              <th className="py-1 pr-3 font-medium">Source</th>
              <th className="py-1 pr-3 font-medium">Inner</th>
              <th className="py-1 pr-3 font-medium">OOF Sharpe</th>
              <th className="py-1 pr-3 font-medium">Parameters</th>
            </tr>
          </thead>
          <tbody>
            {trials.map((t) => (
              <tr key={t.trialId} className="border-t border-border tabular-nums">
                <td className="py-1 pr-3">{t.number}</td>
                <td className="py-1 pr-3">{t.outerFold < 0 ? "final" : t.outerFold + 1}</td>
                <td className="py-1 pr-3">{enumLabel(t.status, "TRIAL_STATUS_")}</td>
                <td className="py-1 pr-3">{enumLabel(t.source, "ACTOR_")}</td>
                <td className="py-1 pr-3">{fmt(t.innerMetric)}</td>
                <td className="py-1 pr-3">{fmt(t.sharpe)}</td>
                <td className="max-w-md truncate py-1 pr-3 font-mono" title={paramText(t.params)}>
                  {paramText(t.params)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/**
 * Candidate (UI-025, UI-026): the gate report with every reason, the tearsheet and evidence pack,
 * permission dry runs, promotion (paper and live_eligible need a passkey; live is never offered),
 * demotion, the holdout evaluation (passkey, reason required, counter shown), and champion vs
 * challenger from paper results.
 */
export function CandidatePage() {
  const { candidateId } = useParams({ from: "/app/experiments/candidates/$candidateId" });
  const cand = useQuery({ queryKey: ["research", "candidate", candidateId], queryFn: () => api<CandidateResponse>(`${R}/candidates/${seg(candidateId)}`) });
  const gate = useQuery({ queryKey: ["research", "gate", candidateId], queryFn: () => api<GateReportResponse>(`${R}/candidates/${seg(candidateId)}/gate-report`) });
  const c = cand.data?.candidate;
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <QueryState q={cand} empty="Candidate not found." isEmpty={(d) => d.candidate === null}>
        {() =>
          c && (
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight">Candidate {c.candidateId.slice(-10)}</h1>
              <StageBadge stage={c.stage} />
              {c.syntheticData && <Badge tone="neutral">synthetic data · never live-eligible</Badge>}
              {c.demoted && <Badge tone="warn">demoted</Badge>}
              <span className="text-sm text-muted">{c.config?.family}</span>
              <div className="ml-auto flex gap-4 text-sm font-medium text-paper">
                {c.runId && (
                  <a href={tearsheetUrl(c.runId)} target="_blank" rel="noopener noreferrer" className="underline-offset-2 hover:underline">
                    Tearsheet ↗
                  </a>
                )}
                <a href={evidenceUrl(c.candidateId)} target="_blank" rel="noopener noreferrer" className="underline-offset-2 hover:underline">
                  Evidence pack ↗
                </a>
              </div>
            </div>
          )
        }
      </QueryState>
      <Card>
        <CardHeader title="Gate report" description="Every gate and reason as the scorer decided; only event-driven evidence can pass." />
        <CardBody>
          <QueryState q={gate} empty="No gate report yet." isEmpty={(d) => d.report === null}>
            {({ report }) => report && <GateReportView report={report} />}
          </QueryState>
        </CardBody>
      </Card>
      {c && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <PromotionCard candidate={c} />
          <HoldoutEvaluateCard candidate={c} />
        </div>
      )}
      {c && <ChampionChallenger candidate={c} />}
    </div>
  );
}

export function GateReportView({ report }: { report: NonNullable<GateReportResponse["report"]> }) {
  const o = report.outer;
  const th = kv(report.thresholds);
  const costNet = kv(report.costSensitivity?.netReturn);
  const costSharpe = kv(report.costSensitivity?.sharpe);
  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={report.passed ? "ok" : "danger"}>{report.passed ? "passed" : "failed"}</Badge>
        <span className="text-xs text-muted">
          engine {report.engine || "—"} · {formatNs(report.createdAt)}
        </span>
      </div>
      {report.reasons.length > 0 && (
        <Alert tone={report.passed ? "warn" : "danger"}>
          <ul className="list-disc pl-4" data-testid="gate-reasons">
            {report.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </Alert>
      )}
      {report.warnings.length > 0 && (
        <Alert tone="warn">
          <ul className="list-disc pl-4">
            {report.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Alert>
      )}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Deflated Sharpe prob." v={`${fmt(report.dsr)}${th.dsr !== undefined ? ` (≥ ${fmt(th.dsr, 2)})` : ""}`} />
        <Metric label="Effective trials" v={fmt(report.nEffective, 1)} />
        <Metric label="PBO" v={`${fmt(report.pbo)}${th.pbo !== undefined ? ` (≤ ${fmt(th.pbo, 2)})` : ""}`} />
        <Metric label="SPA p / Romano-Wolf p" v={`${fmt(report.spaP)} / ${fmt(report.romanoWolfP)}`} />
        <Metric label="Leakage check" v={report.leakagePassed ? "passed" : "failed"} />
        <Metric label="Survivorship" v={report.survivorshipStatus || "—"} />
        {o && <Metric label="Outer Sharpe (ann.)" v={`${fmt(o.sharpeAnnualized, 2)} [${fmt(o.ciLow, 2)}, ${fmt(o.ciHigh, 2)}]`} />}
        {o && <Metric label="Net return · max DD" v={`${pct(o.netReturn)} · ${pct(o.maxDrawdown)}`} />}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <MiniTable
          title="Walk-forward"
          head={["Fold", "Sharpe", "Net", "Trades"]}
          rows={report.walkForward.map((f) => [String(f.fold + 1), fmt(f.sharpe), pct(f.netReturn), String(f.trades)])}
        />
        <MiniTable title="Regimes" head={["Regime", "n", "Sharpe", "P&L share"]} rows={report.regimes.map((r) => [r.regime, String(r.n), fmt(r.sharpe), pct(r.pnlShare, 0)])} />
        <MiniTable
          title="Cost sensitivity"
          head={["Costs", "Net", "Sharpe"]}
          rows={Object.keys(costNet)
            .sort()
            .map((k) => [k, pct(costNet[k]), fmt(costSharpe[k])])}
        />
      </div>
    </div>
  );
}

function MiniTable({ title, head, rows }: { title: string; head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-muted">—</p>
      ) : (
        <table className="data-table text-xs">
          <thead className="text-muted">
            <tr>
              {head.map((h) => (
                <th key={h} className="py-1 pr-3 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-border tabular-nums">
                {r.map((v, j) => (
                  <td key={j} className="py-1 pr-3">
                    {v}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function PromotionCard({ candidate }: { candidate: Candidate }) {
  const queryClient = useQueryClient();
  const stage = stageOf(candidate.stage);
  const targets = promotionTargets(stage, candidate.syntheticData);
  const [reason, setReason] = useState("");
  const check = useMutation({
    mutationFn: (to: Stage) => api<CheckResponse>(`${R}/candidates/${seg(candidate.candidateId)}/check`, { method: "POST", body: { toStage: to } }),
  });
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["research", "candidate", candidate.candidateId] });
    void queryClient.invalidateQueries({ queryKey: ["research", "candidates", candidate.lineageId] });
  };
  const promote = useMutation({
    mutationFn: (to: Stage) => api(`${R}/candidates/${seg(candidate.candidateId)}/promote`, { method: "POST", body: { toStage: to } }),
    onSuccess: invalidate,
  });
  const demote = useMutation({
    mutationFn: (to: Stage) => api(`${R}/candidates/${seg(candidate.candidateId)}/demote`, { method: "POST", body: { toStage: to, reason } }),
    onSuccess: invalidate,
  });
  const decision = check.data?.decision;
  return (
    <Card>
      <CardHeader title="Registry stage" description="Every promotion runs the permission table and the gates; paper and live-eligible need a passkey. Going live is the separate go-live flow." />
      <CardBody className="space-y-3 text-sm">
        {targets.length === 0 ? (
          <p className="text-muted">No promotion from {stage.replace("_", " ")} here.</p>
        ) : (
          targets.map((t) => (
            <div key={t} className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="ghost" busy={check.isPending && check.variables === t} onClick={() => check.mutate(t)}>
                Check {t.replace("_", " ")}
              </Button>
              <Button size="sm" variant={t === "paper" || t === "live_eligible" ? "danger" : "primary"} busy={promote.isPending && promote.variables === t} onClick={() => promote.mutate(t)}>
                Promote to {t.replace("_", " ")}
                {(t === "paper" || t === "live_eligible") && " (passkey)"}
              </Button>
            </div>
          ))
        )}
        {decision && (
          <Alert tone={decision.allowed ? "ok" : "warn"}>
            {decision.allowed ? "Allowed" : "Not allowed"}
            {decision.reason && `: ${decision.reason}`}
            {decision.requiresStepUp && " · needs a passkey"}
            {decision.failedGates.length > 0 && ` · failed gates: ${decision.failedGates.join(", ")}`}
          </Alert>
        )}
        {promote.isError && <Alert>{errMsg(promote.error, "Promotion refused")}</Alert>}
        {promote.isSuccess && <Alert tone="ok">Stage changed.</Alert>}
        {(stage === "shadow" || stage === "paper" || stage === "live_eligible") && (
          <form
            className="flex flex-wrap items-end gap-2 border-t border-border pt-3"
            onSubmit={(e) => {
              e.preventDefault();
              demote.mutate(stage === "live_eligible" ? "paper" : "retired");
            }}
          >
            <div className="w-64">
              <Field label="Demotion reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
            </div>
            <Button type="submit" size="md" variant="secondary" disabled={reason.trim() === ""} busy={demote.isPending}>
              {stage === "live_eligible" ? "Demote to paper" : "Retire"}
            </Button>
            {demote.isError && <Alert>{errMsg(demote.error, "Demotion failed")}</Alert>}
          </form>
        )}
      </CardBody>
    </Card>
  );
}

/** Holdout evaluation (UI-026, EX-011): owner only, passkey, reason required; pass/fail and DSR only. */
function HoldoutEvaluateCard({ candidate }: { candidate: Candidate }) {
  const queryClient = useQueryClient();
  const holdout = useHoldout(candidate.lineageId);
  const [reason, setReason] = useState("");
  const evaluate = useMutation({
    mutationFn: () => api<HoldoutEvalResponse>(`${R}/candidates/${seg(candidate.candidateId)}/holdout`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["research", "holdout", candidate.lineageId] });
      void queryClient.invalidateQueries({ queryKey: ["research", "ledger", candidate.lineageId] });
    },
  });
  const h = holdout.data?.status;
  const remaining = h?.remainingThisQuarter ?? 0;
  const result = evaluate.data?.result;
  return (
    <Card>
      <CardHeader title="Sealed holdout" description="The final check, not a tuning tool. Each look adds trials to the lineage and is audited." />
      <CardBody className="space-y-3 text-sm">
        {h && (
          <p data-testid="holdout-remaining">
            {h.touchesThisQuarter} look{h.touchesThisQuarter === 1 ? "" : "s"} this quarter for {h.family}; {remaining} remaining. Each look adds {h.trialInflationPerTouch} trials.
          </p>
        )}
        {result ? (
          <Alert tone={result.passed ? "ok" : "danger"}>
            Holdout {result.passed ? "passed" : "failed"} · DSR {fmt(result.dsr)} · {result.quarter}
          </Alert>
        ) : (
          <form
            className="space-y-2"
            onSubmit={(e) => {
              e.preventDefault();
              evaluate.mutate();
            }}
          >
            <Field label="Reason (required, audited)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
            <Button type="submit" variant="danger" disabled={reason.trim() === "" || remaining <= 0} busy={evaluate.isPending}>
              Evaluate on holdout (passkey)
            </Button>
          </form>
        )}
        {evaluate.isError && <Alert>{errMsg(evaluate.error, "Holdout evaluation refused")}</Alert>}
      </CardBody>
    </Card>
  );
}

function usePaper(strategyId: string) {
  return useQuery({
    queryKey: ["strategy-portfolio", strategyId],
    enabled: strategyId !== "",
    queryFn: () => api<StrategyPortfolio>(`/api/v1/strategies/${seg(strategyId)}/portfolio`),
    refetchInterval: 30_000,
  });
}

/** Champion vs challenger (UI-027): gate estimates side by side and each one's paper/shadow ledger results. */
function ChampionChallenger({ candidate }: { candidate: Candidate }) {
  const lineage = useQuery({ queryKey: ["research", "lineage", candidate.lineageId], queryFn: () => api<LineageResponse>(`${R}/lineages/${seg(candidate.lineageId)}`) });
  const championId = lineage.data?.lineage?.championCandidateId ?? "";
  const champ = useQuery({
    queryKey: ["research", "candidate", championId],
    enabled: championId !== "" && championId !== candidate.candidateId,
    queryFn: () => api<CandidateResponse>(`${R}/candidates/${seg(championId)}`),
  });
  const champGate = useQuery({
    queryKey: ["research", "gate", championId],
    enabled: championId !== "" && championId !== candidate.candidateId,
    queryFn: () => api<GateReportResponse>(`${R}/candidates/${seg(championId)}/gate-report`),
  });
  const ownGate = useQuery({ queryKey: ["research", "gate", candidate.candidateId], queryFn: () => api<GateReportResponse>(`${R}/candidates/${seg(candidate.candidateId)}/gate-report`) });
  const champion = champ.data?.candidate ?? null;
  const champPaper = usePaper(champion?.strategyId ?? "");
  const ownPaper = usePaper(candidate.strategyId);

  if (championId === "") return null;
  if (championId === candidate.candidateId) {
    return (
      <Card>
        <CardHeader title="Champion" description="This candidate is the lineage's champion; challengers are compared against it." />
      </Card>
    );
  }
  const cols: { label: string; c: Candidate | null; gate: GateReportResponse["report"] | undefined; paper: StrategyPortfolio | undefined }[] = [
    { label: "Champion", c: champion, gate: champGate.data?.report, paper: champPaper.data },
    { label: "This challenger", c: candidate, gate: ownGate.data?.report, paper: ownPaper.data },
  ];
  return (
    <Card>
      <CardHeader title="Champion vs challenger" description="Backtest estimates from the gate reports and results so far from each one's paper or shadow strategy (ledger values)." />
      <CardBody className="overflow-x-auto">
        <table className="data-table">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-2 pr-3 font-medium" />
              {cols.map((x) => (
                <th key={x.label} className="py-2 pr-3 font-medium">
                  {x.label}
                  {x.c && <span className="ml-2 font-mono text-[11px]">{x.c.candidateId.slice(-10)}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="tabular-nums">
            <CompareRow label="Stage" cells={cols.map((x) => (x.c ? <StageBadge stage={x.c.stage} /> : "—"))} />
            <CompareRow label="Outer Sharpe (ann.)" cells={cols.map((x) => fmt(x.gate?.outer?.sharpeAnnualized, 2))} />
            <CompareRow label="Deflated Sharpe prob." cells={cols.map((x) => fmt(x.gate?.dsr))} />
            <CompareRow label="Shadow sessions" cells={cols.map((x) => x.c?.shadowSessions ?? "—")} />
            <CompareRow label="Strategy" cells={cols.map((x) => x.c?.strategyId || "—")} />
            <CompareRow label="Equity" cells={cols.map((x) => (x.paper?.portfolio ? <Num d={x.paper.portfolio.equity} /> : "—"))} />
            <CompareRow label="Realized P&L" cells={cols.map((x) => (x.paper?.portfolio ? <Signed d={x.paper.portfolio.realizedPnl} /> : "—"))} />
            <CompareRow label="Day P&L" cells={cols.map((x) => (x.paper?.portfolio ? <Signed d={x.paper.portfolio.dayPnl} /> : "—"))} />
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

function CompareRow({ label, cells }: { label: string; cells: ReactNode[] }) {
  return (
    <tr className="border-t border-border">
      <td className="py-2 pr-3 text-xs text-muted">{label}</td>
      {cells.map((c, i) => (
        <td key={i} className="py-2 pr-3">
          {c}
        </td>
      ))}
    </tr>
  );
}
