import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { type ReactNode, useState } from "react";

import { EngageKillDialog } from "@/components/KillSwitch";
import { LimitsEditor } from "@/components/LimitsEditor";
import { ModeBadge, Num, QueryState, Signed } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError, type TradingStatus } from "@/lib/api";
import { useStrategies } from "@/lib/queries";
import { dec, enumLabel, type EquityCurve, type FillsResponse, type StrategyPortfolio } from "@/lib/trading";
import { formatNs } from "@/lib/utils";

/** Strategy registry (UI-014): mode, horizon, account, health from execution's trading status. */
export function StrategiesPage() {
  const strategies = useStrategies();
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });
  const health = new Map((status.data?.trading?.strategies ?? []).map((s) => [s.strategyId, s]));
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Strategies</h1>
      <Card>
        <CardBody className="overflow-x-auto">
          <QueryState q={strategies} empty="No strategies are registered." isEmpty={(d) => d.strategies.length === 0}>
            {(d) => (
              <table className="data-table">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-2 pr-4 font-medium">Strategy</th>
                    <th className="py-2 pr-4 font-medium">Mode</th>
                    <th className="py-2 pr-4 font-medium">Horizon</th>
                    <th className="py-2 pr-4 font-medium">Account</th>
                    <th className="py-2 pr-4 font-medium">Health</th>
                  </tr>
                </thead>
                <tbody>
                  {d.strategies.map((s) => {
                    const h = health.get(s.strategyId);
                    return (
                      <tr key={s.strategyId} className="border-t border-border">
                        <td className="py-2 pr-4">
                          <Link to="/strategies/$strategyId" params={{ strategyId: s.strategyId }} className="font-medium underline-offset-2 hover:underline">
                            {s.name || s.strategyId}
                          </Link>
                          <span className="ml-2 font-mono text-xs text-muted">{s.shortId}</span>
                        </td>
                        <td className="py-2 pr-4">
                          <ModeBadge mode={s.mode} />
                        </td>
                        <td className="py-2 pr-4">{enumLabel(s.horizon, "HORIZON_")}</td>
                        <td className="py-2 pr-4">{s.brokerAccountId}</td>
                        <td className="py-2 pr-4">
                          {!h ? (
                            <Badge tone="neutral">unknown</Badge>
                          ) : h.acceptingIntents ? (
                            <Badge tone="ok">accepting</Badge>
                          ) : (
                            <span title={h.blockedReasons.join("; ")}>
                              <Badge tone="warn">blocked</Badge>
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </QueryState>
        </CardBody>
      </Card>
    </div>
  );
}

const MODES = ["disabled", "shadow", "paper"] as const;

/**
 * Strategy detail (UI-014): params, mode controls (live only through the go-live flow, SM-004),
 * per-strategy kill switch, allocation and limit editors (increases need a passkey, RM-007),
 * positions, equity curve, and recent fills.
 */
export function StrategyDetailPage() {
  const { strategyId } = useParams({ from: "/app/strategies/$strategyId" });
  const queryClient = useQueryClient();
  const strategies = useStrategies();
  const strategy = strategies.data?.strategies.find((s) => s.strategyId === strategyId);
  const portfolio = useQuery({
    queryKey: ["strategy-portfolio", strategyId],
    queryFn: () => api<StrategyPortfolio>(`/api/v1/strategies/${encodeURIComponent(strategyId)}/portfolio`),
    refetchInterval: 15_000,
  });
  const account = portfolio.data?.brokerAccountId ?? strategy?.brokerAccountId ?? "";
  const equity = useQuery({
    queryKey: ["strategy-equity", strategyId, account],
    enabled: account !== "",
    queryFn: () => api<EquityCurve>(`/api/v1/strategies/${encodeURIComponent(strategyId)}/equity?account=${encodeURIComponent(account)}`),
  });
  const fills = useQuery({
    queryKey: ["fills", { strategyId }],
    queryFn: () => api<FillsResponse>(`/api/v1/fills?strategy_id=${encodeURIComponent(strategyId)}&limit=50`),
    refetchInterval: 15_000,
  });
  const [killOpen, setKillOpen] = useState(false);
  const [reason, setReason] = useState("");
  const setMode = useMutation({
    mutationFn: (mode: string) => api(`/api/v1/strategies/${encodeURIComponent(strategyId)}/mode`, { method: "PUT", body: { mode, reason } }),
    onSuccess: () => {
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["strategies"] });
    },
  });

  if (strategies.isPending) return <p className="text-sm text-muted">Loading…</p>;
  if (!strategy) return <Alert>Strategy {strategyId} is not registered.</Alert>;
  const current = enumLabel(strategy.mode, "STRATEGY_MODE_");

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{strategy.name || strategy.strategyId}</h1>
        <ModeBadge mode={strategy.mode} />
        <span className="font-mono text-xs text-muted">{strategy.shortId}</span>
        <Button variant="danger" size="sm" className="ml-auto" onClick={() => setKillOpen(true)}>
          Kill this strategy
        </Button>
      </div>
      <EngageKillDialog open={killOpen} onClose={() => setKillOpen(false)} target={{ scope: "strategy", target: strategy.strategyId, label: strategy.strategyId }} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Registry" />
          <CardBody className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <span className="text-muted">Account</span>
            <span>{strategy.brokerAccountId}</span>
            <span className="text-muted">Horizon</span>
            <span>{enumLabel(strategy.horizon, "HORIZON_")}</span>
            <span className="text-muted">Version</span>
            <span className="font-mono text-xs">{strategy.version || "—"}</span>
            <span className="text-muted">Code commit</span>
            <span className="font-mono text-xs">{strategy.codeCommit || "—"}</span>
            <span className="text-muted">Params hash</span>
            <span className="truncate font-mono text-xs">{strategy.paramsHash || "—"}</span>
            <span className="text-muted">Shorting</span>
            <span>{strategy.allowShort ? "allowed (dedicated account)" : "not allowed"}</span>
            <span className="text-muted">Mode reason</span>
            <span>{strategy.modeReason || "—"}</span>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Mode" description="Live promotion happens only through the go-live flow (section 15)." />
          <CardBody className="space-y-3">
            <div className="flex flex-wrap gap-2" role="group" aria-label="Strategy mode">
              {MODES.map((m) => (
                <Button
                  key={m}
                  size="sm"
                  variant={current === m ? "secondary" : "ghost"}
                  aria-pressed={current === m}
                  disabled={current === m}
                  busy={setMode.isPending && setMode.variables === m}
                  onClick={() => setMode.mutate(m)}
                >
                  {m}
                </Button>
              ))}
            </div>
            <Field label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
            {setMode.isError && <Alert>{setMode.error instanceof ApiError ? setMode.error.message : "Mode change failed"}</Alert>}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Virtual portfolio" description={account ? `Inside ${account}` : undefined} />
        <CardBody className="space-y-4">
          <QueryState q={portfolio} empty="No portfolio yet." isEmpty={(d) => d.portfolio === null}>
            {({ portfolio: pf }) =>
              pf && (
              <>
                <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3 lg:grid-cols-6">
                  <Metric label="Allocation" v={<Num d={pf.allocation} />} />
                  <Metric label="Cash available" v={<Num d={pf.cashAvailable} />} />
                  <Metric label="Equity" v={<Num d={pf.equity} />} />
                  <Metric label="Day P&L" v={<Signed d={pf.dayPnl} />} />
                  <Metric label="Realized" v={<Signed d={pf.realizedPnl} />} />
                  <Metric label="Gross exposure" v={<Num d={pf.grossExposure} />} />
                </div>
                <AllocationEditor strategyId={strategyId} current={dec(pf.allocation)} />
                {pf.positions.length === 0 ? (
                  <p className="text-sm text-muted">Flat.</p>
                ) : (
                  <table className="data-table">
                    <thead className="text-xs text-muted">
                      <tr>
                        <th className="py-2 pr-4 font-medium">Symbol</th>
                        <th className="py-2 pr-4 font-medium">Qty</th>
                        <th className="py-2 pr-4 font-medium">Avg cost</th>
                        <th className="py-2 pr-4 font-medium">Mark</th>
                        <th className="py-2 pr-4 font-medium">Unrealized</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pf.positions.map((p) => (
                        <tr key={p.instrumentId} className="border-t border-border">
                          <td className="py-2 pr-4 font-medium">
                            {p.symbol || p.instrumentId} {p.halted && <Badge tone="danger">halted</Badge>}
                          </td>
                          <td className="py-2 pr-4">
                            <Num d={p.qty} />
                          </td>
                          <td className="py-2 pr-4">
                            <Num d={p.avgCost} />
                          </td>
                          <td className="py-2 pr-4">
                            <Num d={p.markPrice} />
                          </td>
                          <td className="py-2 pr-4">
                            <Signed d={p.unrealizedPnl} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </QueryState>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Risk limits" description="Lowering applies at once; loosening any limit asks for a passkey." />
        <CardBody>
          <LimitsEditor scope={`strategy:${strategyId}`} />
        </CardBody>
      </Card>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Equity curve" />
          <CardBody>
            <QueryState q={equity} empty="No equity points yet." isEmpty={(d) => d.points.length === 0}>
              {(d) => (
                <ol className="max-h-64 space-y-1 overflow-y-auto text-xs">
                  {[...d.points].reverse().map((p) => (
                    <li key={p.ts} className="flex justify-between gap-3 border-b border-border py-1">
                      <span className="text-muted">{formatNs(p.ts)}</span>
                      <span className="tabular-nums">{dec(p.equity)}</span>
                    </li>
                  ))}
                </ol>
              )}
            </QueryState>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recent fills" />
          <CardBody>
            <QueryState q={fills} empty="No fills yet." isEmpty={(d) => d.fills.length === 0}>
              {(d) => (
                <ol className="max-h-64 space-y-1 overflow-y-auto text-xs">
                  {d.fills.map((f) => (
                    <li key={f.brokerExecId} className="flex justify-between gap-3 border-b border-border py-1">
                      <span className="text-muted">{formatNs(f.eventTime)}</span>
                      <span>
                        {enumLabel(f.side, "SIDE_")} {dec(f.qty)} @ {dec(f.price)}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </QueryState>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function Metric({ label, v }: { label: string; v: ReactNode }) {
  return (
    <div>
      <p className="text-xs text-muted">{label}</p>
      <p className="font-medium">{v}</p>
    </div>
  );
}

function AllocationEditor({ strategyId, current }: { strategyId: string; current: string }) {
  const queryClient = useQueryClient();
  const [amount, setAmount] = useState("");
  const save = useMutation({
    mutationFn: () => api(`/api/v1/strategies/${encodeURIComponent(strategyId)}/allocation`, { method: "PUT", body: { amount } }),
    onSuccess: () => {
      setAmount("");
      void queryClient.invalidateQueries({ queryKey: ["strategy-portfolio", strategyId] });
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="w-48">
        <Field label={`New allocation (now ${current})`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value.trim())} />
      </div>
      <Button type="submit" size="md" variant="secondary" disabled={!/^\d+(\.\d+)?$/.test(amount)} busy={save.isPending}>
        Set allocation
      </Button>
      {save.isError && <Alert>{save.error instanceof ApiError ? save.error.message : "Allocation change failed"}</Alert>}
    </form>
  );
}
