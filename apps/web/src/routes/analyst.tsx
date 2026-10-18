import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Badge, Field } from "@/components/ui/form";
import {
  analystApi,
  decisionTone,
  fmt,
  label,
  LEVELS,
  prettyDiff,
  type CodeProposal,
  type LoopStatus,
  type PlanAction,
  type Round,
  type TrialLedger,
} from "@/lib/analyst";
import { ApiError } from "@/lib/api";
import { formatNs } from "@/lib/utils";

/**
 * AI Research Analyst (section 14 Phase 4 slice; UI-018..UI-022): per-lineage research journal with
 * diagnoses, ranked plans and config diffs, proposal approvals, the autonomy selector (raising needs a
 * passkey step-up, handled by the gateway's 403 and the step-up dialog), loop pause, the lineage trial
 * ledger with deflated statistics, code proposals awaiting review, and the assistant chat. The analyst
 * never trades, never sees the holdout, and never promotes to live; nothing here can change that.
 */
export function AnalystPage() {
  const lineages = useQuery({ queryKey: ["analyst", "lineages"], queryFn: analystApi.lineages, refetchInterval: 15_000 });
  const [chosen, setSelected] = useState<string>("");
  const ids = lineages.data?.lineages.map((l) => l.lineageId) ?? [];
  // The first lineage is shown until the owner picks another.
  const selected = chosen || ids[0] || "";
  const current = lineages.data?.lineages.find((l) => l.lineageId === selected) ?? null;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI research analyst</h1>
          <p className="text-sm text-muted">Optuna searches; the analyst diagnoses and plans. It cannot trade, see the holdout, or promote to live.</p>
        </div>
        {ids.length > 0 && (
          <label className="flex items-center gap-2 text-sm">
            <span className="text-muted">Lineage</span>
            <select
              aria-label="Lineage"
              className="h-10 rounded-lg border border-border bg-bg px-3 text-sm"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              {ids.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <QueryState q={lineages} empty="No research lineage yet. Lineages are created in Experiments (section 11)." isEmpty={(d) => d.lineages.length === 0}>
        {() =>
          current ? (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <div className="space-y-6 lg:col-span-1">
                <LoopCard status={current} />
                <LedgerCard lineage={selected} />
              </div>
              <div className="space-y-6 lg:col-span-2">
                <JournalCard lineage={selected} />
                <ProposalsCard lineage={selected} />
                <ChatCard lineage={selected} />
              </div>
            </div>
          ) : null
        }
      </QueryState>
    </div>
  );
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message || e.code : "Request failed";
}

/** Loop status, autonomy selector, budgets, and pause (UI-019). */
export function LoopCard({ status }: { status: LoopStatus }) {
  const qc = useQueryClient();
  const id = status.lineageId;
  const level = status.autonomy?.level ?? 0;
  const paused = status.autonomy?.paused ?? false;
  const [target, setTarget] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const done = () => {
    setTarget(null);
    setConfirming(false);
    void qc.invalidateQueries({ queryKey: ["analyst"] });
  };
  const autonomy = useMutation({ mutationFn: () => analystApi.setAutonomy(id, target ?? level), onSuccess: done });
  const pause = useMutation({ mutationFn: () => analystApi.pause(id, !paused, paused ? "resumed from UI" : "paused from UI"), onSuccess: done });
  const raising = target !== null && target > level;

  return (
    <Card>
      <CardHeader
        title="Loop and autonomy"
        description="Raising the level asks for your passkey; lowering is instant. Budgets are on the lineage page."
        action={<Badge tone={paused ? "warn" : "neutral"}>{paused ? "paused" : label(status.state, "LOOP_STATE_")}</Badge>}
      />
      <CardBody className="space-y-4 text-sm">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
          <dt className="text-muted">Rounds this loop</dt>
          <dd className="tabular-nums">{status.roundsCompleted}</dd>
          <dt className="text-muted">Without improvement</dt>
          <dd className="tabular-nums">{status.roundsWithoutImprovement} / 3</dd>
          <dt className="text-muted">Last decision</dt>
          <dd>{label(status.lastDecision, "ROUND_DECISION_")}</dd>
          <dt className="text-muted">Trials this round</dt>
          <dd className="tabular-nums">
            {status.budgetUsed?.trials ?? 0} / {status.autonomy?.budget?.maxTrialsPerRound ?? "—"}
          </dd>
          <dt className="text-muted">LLM spend today / month</dt>
          <dd className="tabular-nums">
            {fmt(status.llmUsdToday, 2)} / {fmt(status.llmUsdMonth, 2)} USD
          </dd>
        </dl>
        <fieldset className="space-y-2" aria-label="Autonomy level">
          <legend className="text-xs font-medium text-muted">Autonomy level</legend>
          {LEVELS.map((l) => (
            <label key={l.level} className="flex cursor-pointer items-start gap-2 rounded-lg border border-border p-2">
              <input
                type="radio"
                name={`level-${id}`}
                checked={(target ?? level) === l.level}
                onChange={() => setTarget(l.level === level ? null : l.level)}
              />
              <span>
                <span className="font-medium">
                  {l.level} · {l.name}
                </span>
                {l.level === level && <Badge tone="ok">current</Badge>}
                <span className="block text-xs text-muted">{l.detail}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={target === null} onClick={() => setConfirming(true)}>
            Apply…
          </Button>
          <Button size="sm" variant="secondary" busy={pause.isPending} onClick={() => pause.mutate()}>
            {paused ? "Resume loop" : "Pause loop"}
          </Button>
        </div>
        {pause.isError && <Alert>{errorText(pause.error)}</Alert>}
      </CardBody>
      <Dialog open={confirming} onClose={() => setConfirming(false)} title="Change analyst autonomy">
        <div className="space-y-3 text-sm">
          <p>
            Level {level} → <strong>{target ?? level}</strong>
          </p>
          {raising && <Alert tone="warn">Raising autonomy lets the analyst act without asking. You will confirm with your passkey.</Alert>}
          {autonomy.isError && <Alert>{errorText(autonomy.error)}</Alert>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button busy={autonomy.isPending} onClick={() => autonomy.mutate()}>
              Confirm
            </Button>
          </div>
        </div>
      </Dialog>
    </Card>
  );
}

/** Lineage trial ledger with the deflated outcome (UI-021). */
export function LedgerCard({ lineage }: { lineage: string }) {
  const q = useQuery({ queryKey: ["analyst", "trials", lineage], queryFn: () => analystApi.trials(lineage), refetchInterval: 30_000 });
  return (
    <Card>
      <CardHeader title="Trial ledger" description="Every trial in the lineage counts, whoever ran it. Decisions use the deflated numbers." />
      <CardBody>
        <QueryState q={q} empty="No trials yet." isEmpty={(d: TrialLedger) => d.trials.length === 0}>
          {(d) => (
            <div className="space-y-3 text-sm">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                <dt className="text-muted">Lineage trials</dt>
                <dd className="tabular-nums">{d.outcome?.lineageTrials ?? d.trials.length}</dd>
                <dt className="text-muted">Effective trials</dt>
                <dd className="tabular-nums">{fmt(d.outcome?.effectiveTrials, 1)}</dd>
                <dt className="text-muted">Best deflated Sharpe prob.</dt>
                <dd className="tabular-nums">{fmt(d.outcome?.bestDsr)}</dd>
                <dt className="text-muted">PBO</dt>
                <dd className="tabular-nums">{fmt(d.outcome?.pbo, 2)}</dd>
                <dt className="text-muted">Cycle budget used</dt>
                <dd className="tabular-nums">
                  {d.cycleUsed} / {d.cycleBudget}
                </dd>
              </dl>
              <div className="max-h-72 overflow-auto">
                <table className="data-table text-xs" aria-label="Trials">
                  <thead className="text-muted">
                    <tr>
                      <th className="py-1">Trial</th>
                      <th>Source</th>
                      <th>Status</th>
                      <th className="text-right" title="Raw out-of-fold Sharpe: shown, never used for decisions">
                        Raw SR
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.trials.map((t) => (
                      <tr key={t.trialId} className="border-t border-border">
                        <td className="py-1 font-mono">{t.trialId}</td>
                        <td>
                          <Badge tone={t.source === "analyst" ? "warn" : "neutral"}>{t.source}</Badge>
                        </td>
                        <td>{t.status}</td>
                        <td className="text-right tabular-nums">{fmt(t.sharpe)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}

/** The research journal (UI-018) and proposal approvals (UI-020). */
export function JournalCard({ lineage }: { lineage: string }) {
  const q = useQuery({ queryKey: ["analyst", "journal", lineage], queryFn: () => analystApi.journal(lineage), refetchInterval: 15_000 });
  const [question, setQuestion] = useState("");
  const qc = useQueryClient();
  const ask = useMutation({
    mutationFn: () => analystApi.analyze(lineage, question),
    onSuccess: () => {
      setQuestion("");
      void qc.invalidateQueries({ queryKey: ["analyst"] });
    },
  });
  return (
    <Card>
      <CardHeader title="Research journal" description="Each round: evidence-cited diagnosis, ranked plan, actions, and decision." />
      <CardBody className="space-y-4">
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            ask.mutate();
          }}
        >
          <div className="min-w-64 flex-1">
            <Field label="Ask for an analysis round" placeholder="e.g. why did the last study plateau?" value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} />
          </div>
          <Button type="submit" size="sm" busy={ask.isPending}>
            Request analysis
          </Button>
        </form>
        {ask.isError && <Alert>{errorText(ask.error)}</Alert>}
        <QueryState q={q} empty="No rounds yet. A round opens after a training run, study, backtest, or live-feedback trigger." isEmpty={(d) => d.rounds.length === 0}>
          {(d) => (
            <ol className="space-y-3" aria-label="Journal">
              {d.rounds.map((r) => (
                <RoundItem key={r.roundId} round={r} />
              ))}
            </ol>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}

export function RoundItem({ round }: { round: Round }) {
  const [open, setOpen] = useState(round.decision === "ROUND_DECISION_AWAITING_APPROVAL");
  const qc = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const proposed = round.plan?.actions.filter((a) => a.status === "ACTION_STATUS_PROPOSED") ?? [];
  const review = useMutation({
    mutationFn: (approve: boolean) => analystApi.review(round.roundId, selected, approve, reason),
    onSuccess: () => {
      setSelected([]);
      setReason("");
      void qc.invalidateQueries({ queryKey: ["analyst"] });
    },
  });
  return (
    <li className="rounded-lg border border-border">
      <button type="button" className="flex w-full flex-wrap items-center justify-between gap-2 p-3 text-left text-sm" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="flex items-center gap-2">
          <span className="font-medium">Round {round.seq}</span>
          <span className="text-muted">{label(round.trigger, "ROUND_TRIGGER_")}</span>
          <Badge tone={decisionTone(round.decision)}>{label(round.decision, "ROUND_DECISION_")}</Badge>
          <span className="text-xs text-muted">level {round.autonomyLevel}</span>
        </span>
        <span className="text-xs tabular-nums text-muted">
          DSR {fmt(round.outcome?.bestDsr)} · PBO {fmt(round.outcome?.pbo, 2)} · trials {round.outcome?.lineageTrials ?? 0} · {formatNs(round.startedAt)}
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-border p-3 text-sm">
          {round.summary && <Alert tone="ok">{round.summary}</Alert>}
          {round.diagnosis?.summary && <p>{round.diagnosis.summary}</p>}
          <section aria-label="Findings">
            <h3 className="text-xs font-semibold uppercase text-muted">Findings</h3>
            {(round.diagnosis?.findings.length ?? 0) === 0 && <p className="text-xs text-muted">No findings.</p>}
            <ul className="space-y-2">
              {round.diagnosis?.findings.map((f) => (
                <li key={f.findingId} className="rounded border border-border p-2">
                  <p className="flex items-center gap-2">
                    <Badge tone={f.severity === "SEVERITY_HIGH" || f.severity === "SEVERITY_CRITICAL" ? "danger" : "warn"}>{label(f.severity, "SEVERITY_")}</Badge>
                    <span className="font-medium">{label(f.category, "FINDING_CATEGORY_")}</span>
                  </p>
                  <p className="text-muted">{f.explanation}</p>
                  <p className="mt-1 font-mono text-xs">
                    {f.evidence.map((e) => `${e.metric}=${fmt(e.value, 4)} (${e.reportId})`).join(" · ")}
                  </p>
                </li>
              ))}
            </ul>
          </section>
          <section aria-label="Plan">
            <h3 className="text-xs font-semibold uppercase text-muted">Ranked plan</h3>
            <ul className="space-y-2">
              {round.plan?.actions.map((a) => (
                <ActionRow
                  key={a.actionId}
                  action={a}
                  selectable={a.status === "ACTION_STATUS_PROPOSED"}
                  checked={selected.includes(a.actionId)}
                  onCheck={(c) => setSelected(c ? [...selected, a.actionId] : selected.filter((x) => x !== a.actionId))}
                />
              ))}
            </ul>
          </section>
          {proposed.length > 0 && (
            <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
              <div className="min-w-48 flex-1">
                <Field label="Reason (required to reject)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
              </div>
              <Button size="sm" disabled={!selected.length} busy={review.isPending} onClick={() => review.mutate(true)}>
                Approve selected
              </Button>
              <Button size="sm" variant="secondary" disabled={!selected.length || !reason} busy={review.isPending} onClick={() => review.mutate(false)}>
                Reject selected
              </Button>
            </div>
          )}
          {review.isError && <Alert>{errorText(review.error)}</Alert>}
          {round.reasoning && <p className="text-xs text-muted">Reasoning: {round.reasoning}</p>}
          <p className="text-xs text-muted">
            {round.provider}/{round.model} · prompt {round.promptHashes[0]?.slice(0, 12) ?? "—"} · trials added {round.trialsAdded}
          </p>
        </div>
      )}
    </li>
  );
}

function ActionRow({ action, selectable, checked, onCheck }: { action: PlanAction; selectable: boolean; checked: boolean; onCheck: (c: boolean) => void }) {
  return (
    <li className="rounded border border-border p-2">
      <label className="flex items-start gap-2">
        {selectable && <input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} aria-label={`Select action ${action.rank}`} />}
        <span className="flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">#{action.rank || "·"}</span>
            <span>{action.description}</span>
            <Badge tone="neutral">{action.tool}</Badge>
            <Badge tone={action.status === "ACTION_STATUS_FAILED" ? "danger" : action.status === "ACTION_STATUS_SKIPPED" ? "warn" : "neutral"}>
              {label(action.status, "ACTION_STATUS_")}
            </Badge>
          </span>
          <span className="block text-xs text-muted">
            {action.trialCost} trial(s) · ~{fmt(action.estComputeHours, 1)} CPU h · overfit risk {label(action.overfitRisk, "OVERFIT_RISK_")}
            {action.statusReason && ` · ${action.statusReason}`}
          </span>
          {action.configDiffJson && action.configDiffJson !== "{}" && (
            <pre className="mt-1 max-h-40 overflow-auto rounded bg-bg p-2 font-mono text-xs">{prettyDiff(action.configDiffJson)}</pre>
          )}
        </span>
      </label>
    </li>
  );
}

/** Candidate features awaiting review (AN-073): checks and the review-only patch. */
export function ProposalsCard({ lineage }: { lineage: string }) {
  const q = useQuery({ queryKey: ["analyst", "proposals", lineage], queryFn: () => analystApi.proposals(lineage), refetchInterval: 30_000 });
  const qc = useQueryClient();
  const review = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) => analystApi.reviewProposal(id, approve, approve ? "approved for merge" : "rejected"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["analyst", "proposals"] }),
  });
  return (
    <Card>
      <CardHeader title="Code proposals" description="Approval only marks a patch ready; you merge it yourself. Nothing runs until merged." />
      <CardBody>
        <QueryState q={q} empty="No code proposals." isEmpty={(d) => d.proposals.length === 0}>
          {(d) => (
            <ul className="space-y-3" aria-label="Code proposals">
              {d.proposals.map((p: CodeProposal) => (
                <li key={p.proposalId} className="space-y-2 rounded-lg border border-border p-3 text-sm">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{p.name}</span>
                    <Badge tone={p.status === "CODE_PROPOSAL_STATUS_AWAITING_REVIEW" ? "warn" : p.status === "CODE_PROPOSAL_STATUS_CHECKS_FAILED" ? "danger" : "neutral"}>
                      {label(p.status, "CODE_PROPOSAL_STATUS_")}
                    </Badge>
                  </p>
                  <p className="text-muted">{p.description}</p>
                  <ul className="flex flex-wrap gap-2 text-xs">
                    {p.checks.map((c) => (
                      <li key={c.name}>
                        <Badge tone={c.passed ? "ok" : "danger"}>
                          {c.name}: {c.passed ? "pass" : `fail ${c.messages.join(", ")}`}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                  <pre className="max-h-48 overflow-auto rounded bg-bg p-2 font-mono text-xs">{p.patch}</pre>
                  {p.status === "CODE_PROPOSAL_STATUS_AWAITING_REVIEW" && (
                    <div className="flex gap-2">
                      <Button size="sm" busy={review.isPending} onClick={() => review.mutate({ id: p.proposalId, approve: true })}>
                        Approve for merge
                      </Button>
                      <Button size="sm" variant="secondary" busy={review.isPending} onClick={() => review.mutate({ id: p.proposalId, approve: false })}>
                        Reject
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </QueryState>
        {review.isError && <Alert>{errorText(review.error)}</Alert>}
      </CardBody>
    </Card>
  );
}

/** Assistant chat (AN-080): answers cite the IDs they rely on. */
export function ChatCard({ lineage }: { lineage: string }) {
  const qc = useQueryClient();
  const [convId, setConvId] = useState<string>("");
  const [text, setText] = useState("");
  const conv = useQuery({ queryKey: ["analyst", "chat", convId], queryFn: () => analystApi.conversation(convId), enabled: !!convId });
  const create = useMutation({
    mutationFn: () => analystApi.createConversation(`Lineage ${lineage}`, lineage),
    onSuccess: (r) => setConvId(r.conversation.conversationId),
  });
  const send = useMutation({
    mutationFn: async () => {
      let id = convId;
      if (!id) {
        id = (await analystApi.createConversation(`Lineage ${lineage}`, lineage)).conversation.conversationId;
        setConvId(id);
      }
      return analystApi.post(id, text);
    },
    onSuccess: () => {
      setText("");
      void qc.invalidateQueries({ queryKey: ["analyst", "chat"] });
    },
  });
  const messages = conv.data?.conversation.messages ?? [];
  return (
    <Card>
      <CardHeader
        title="Assistant"
        description="Ask about runs, studies, and rounds. Read-only; answers cite their sources."
        action={
          <Button size="sm" variant="ghost" busy={create.isPending} onClick={() => create.mutate()}>
            New conversation
          </Button>
        }
      />
      <CardBody className="space-y-3">
        <ul className="max-h-80 space-y-2 overflow-auto text-sm" aria-label="Conversation" aria-live="polite">
          {messages.length === 0 && <li className="text-muted">No messages yet.</li>}
          {messages.map((m) => (
            <li key={m.messageId} className={m.role === "owner" ? "text-right" : ""}>
              <span className={`inline-block max-w-[85%] rounded-lg px-3 py-2 ${m.role === "owner" ? "bg-accent/15" : "bg-panel-2"}`}>{m.content}</span>
              {m.citedIds.length > 0 && <span className="block text-xs text-muted">sources: {m.citedIds.join(", ")}</span>}
            </li>
          ))}
        </ul>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) send.mutate();
          }}
        >
          <input
            aria-label="Message"
            className="h-10 flex-1 rounded-lg border border-border bg-bg px-3 text-sm"
            value={text}
            maxLength={4000}
            onChange={(e) => setText(e.target.value)}
            placeholder="Ask the assistant…"
          />
          <Button type="submit" busy={send.isPending} disabled={!text.trim()}>
            Send
          </Button>
        </form>
        {send.isError && <Alert>{errorText(send.error)}</Alert>}
      </CardBody>
    </Card>
  );
}
