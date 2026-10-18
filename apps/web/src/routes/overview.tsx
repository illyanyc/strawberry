import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CalendarClock, PieChart, Receipt, ShieldAlert, Users } from "lucide-react";
import { useMemo, useState } from "react";

import { Sparkline } from "@/components/Sparkline";
import { ModeBadge, Num, OwnershipTable, QueryState, Signed } from "@/components/trading";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/form";
import { EmptyState, Page, PageHeader, Segmented } from "@/components/ui/layout";
import { api } from "@/lib/api";
import { useAccounts, useEquityCurve, useStrategies } from "@/lib/queries";
import { dec, enumLabel, type AccountView, type FillsResponse, type IntentsResponse, type OwnerView, type Strategy } from "@/lib/trading";
import { cn, formatNs } from "@/lib/utils";

/** Owner swatches: `manual` is always gray; strategies take palette colors in registry order. */
const PALETTE = ["bg-chart-1", "bg-chart-2", "bg-chart-3", "bg-chart-4", "bg-ok", "bg-warn"] as const;

export function ownerColor(owner: string, strategies: string[]): string {
  if (owner === "manual") return "bg-subtle";
  const i = strategies.indexOf(owner);
  return PALETTE[(i < 0 ? strategies.length : i) % PALETTE.length]!;
}

/**
 * Midnight in New York for the given instant, as UTC nanoseconds: the start of the trading day the
 * end-of-day view filters fills and intents on (a display boundary, not a business rule).
 */
export function nyDayStartNs(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" })
    .formatToParts(now)
    .reduce<Record<string, string>>((acc, p) => ({ ...acc, [p.type]: p.value }), {});
  const sinceMidnight = (Number(parts.hour) * 3600 + Number(parts.minute) * 60 + Number(parts.second)) * 1000 + now.getMilliseconds();
  return (BigInt(now.getTime() - sinceMidnight) * 1_000_000n).toString();
}

/**
 * Portfolio overview and end of day (UI-046): per broker account, the `manual` bucket next to each
 * strategy's virtual portfolio (allocation, equity, exposure, realized/unrealized/day P&L, fees) as
 * the ledger reports them, positions by owner per instrument, equity curves by owner, and today's
 * activity (fills per owner, rejected intents with the first failed check, reconciliation, breaks).
 * The page adds nothing up: totals are the ledger's (UI-I2).
 */
export function OverviewPage() {
  const accounts = useAccounts();
  const strategies = useStrategies();
  const [tab, setTab] = useState<"portfolio" | "eod">("portfolio");
  const strategyById = useMemo(() => new Map((strategies.data?.strategies ?? []).map((s) => [s.strategyId, s])), [strategies.data]);

  return (
    <Page wide>
      <PageHeader
        eyebrow="Overview"
        title="Portfolio overview"
        description="Manual holdings and every strategy's virtual portfolio, side by side, as the ledger reports them."
        actions={
          <Segmented
            label="View"
            value={tab}
            onChange={setTab}
            options={[
              { value: "portfolio", label: "Portfolio" },
              { value: "eod", label: "End of day" },
            ]}
          />
        }
      />
      <QueryState q={accounts} empty="No broker account has reported yet." isEmpty={(d) => d.accounts.length === 0}>
        {(d) => (
          <div className="space-y-6">
            {d.accounts.map((a) =>
              tab === "portfolio" ? (
                <AccountOverview key={a.brokerAccountId} a={a} strategyById={strategyById} />
              ) : (
                <EndOfDay key={a.brokerAccountId} a={a} />
              ),
            )}
          </div>
        )}
      </QueryState>
    </Page>
  );
}

function owners(a: AccountView): OwnerView[] {
  return [...(a.manual ? [a.manual] : []), ...a.owners];
}

function AccountOverview({ a, strategyById }: { a: AccountView; strategyById: Map<string, Strategy> }) {
  const strategyIds = a.owners.map((o) => o.owner);
  const recon = enumLabel(a.reconStatus, "RECONCILIATION_STATUS_");
  return (
    <section aria-labelledby={`acct-${a.brokerAccountId}`} className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id={`acct-${a.brokerAccountId}`} className="font-mono text-base font-semibold">
          {a.brokerAccountId}
        </h2>
        <Badge tone={recon === "ok" ? "ok" : "warn"}>recon {recon}</Badge>
        {a.openBreaks.length > 0 && <Badge tone="danger">{a.openBreaks.length} open break{a.openBreaks.length > 1 ? "s" : ""}</Badge>}
        <span className="text-xs text-muted">as of {formatNs(a.reconAsOf)}</span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {(
          [
            ["Broker equity", a.brokerSnapshot?.equity],
            ["Broker cash", a.brokerSnapshot?.cash],
            ["Unallocated cash", a.unallocatedCash],
            ["Reserve", a.reserve],
            ["Allocatable capital", a.allocatableCapital],
          ] as const
        ).map(([label, v]) => (
          <div key={label} className="rounded-xl border border-border bg-panel px-4 py-3 shadow-panel">
            <p className="text-[11px] uppercase tracking-wider text-muted">{label}</p>
            <p className="mt-1 font-mono text-lg font-semibold tabular-nums">
              <Num d={v} />
            </p>
          </div>
        ))}
      </div>
      <Card>
        <CardHeader icon={<Users />} title="Owners" description="manual is your own holdings; every other row is a strategy's virtual portfolio (N3)." />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          <table className="data-table" aria-label={`Owners in ${a.brokerAccountId}`}>
            <thead>
              <tr>
                <th className="pl-4 sm:pl-5">Owner</th>
                <th>Mode</th>
                <th className="num">Allocation</th>
                <th className="num">Equity</th>
                <th className="num">Cash avail.</th>
                <th className="num">Gross exp.</th>
                <th className="num">Net exp.</th>
                <th className="num">Realized</th>
                <th className="num">Unrealized</th>
                <th className="num">Fees</th>
                <th className="num">Day P&amp;L</th>
                <th className="num pr-4 sm:pr-5">Positions</th>
              </tr>
            </thead>
            <tbody>
              {owners(a).map((o) => {
                const s = strategyById.get(o.owner);
                return (
                  <tr key={o.owner} data-owner={o.owner}>
                    <td className="pl-4 sm:pl-5">
                      <span className="flex items-center gap-2">
                        <span className={cn("size-2.5 rounded-sm", ownerColor(o.owner, strategyIds))} aria-hidden />
                        {o.owner === "manual" ? (
                          <span className="font-medium">manual</span>
                        ) : (
                          <Link to="/strategies/$strategyId" params={{ strategyId: o.owner }} className="font-medium hover:underline">
                            {o.owner}
                          </Link>
                        )}
                      </span>
                    </td>
                    <td>{o.owner === "manual" ? <Badge tone="neutral">owner</Badge> : s ? <ModeBadge mode={s.mode} /> : <Badge tone="neutral">—</Badge>}</td>
                    <td className="num">{o.owner === "manual" ? "—" : <Num d={o.allocation} />}</td>
                    <td className="num">
                      <Num d={o.equity} />
                    </td>
                    <td className="num">
                      <Num d={o.cashAvailable} />
                    </td>
                    <td className="num">
                      <Num d={o.grossExposure} />
                    </td>
                    <td className="num">
                      <Num d={o.netExposure} />
                    </td>
                    <td className="num">
                      <Signed d={o.realizedPnl} />
                    </td>
                    <td className="num">
                      <Signed d={o.unrealizedPnl} />
                    </td>
                    <td className="num">
                      <Num d={o.fees} />
                    </td>
                    <td className="num">
                      <Signed d={o.dayPnl} />
                    </td>
                    <td className="num pr-4 sm:pr-5">{o.positions.length}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardBody>
      </Card>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader icon={<PieChart />} title="Positions by owner" description="Quantity per instrument split by owner, as the ledger holds it; broker vs ledger differences are flagged." />
          <CardBody className="space-y-4">
            <ExposureBars a={a} strategies={strategyIds} />
            <OwnershipTable instruments={a.instruments} strategies={strategyIds} />
          </CardBody>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Equity by owner" description="Ledger equity curve per owner (strategies and manual)." />
          <CardBody className="space-y-3">
            {owners(a).map((o) => (
              <OwnerCurve key={o.owner} owner={o.owner} account={a.brokerAccountId} color={ownerColor(o.owner, strategyIds)} equity={dec(o.equity)} />
            ))}
          </CardBody>
        </Card>
      </div>
    </section>
  );
}

function OwnerCurve({ owner, account, color, equity }: { owner: string; account: string; color: string; equity: string }) {
  const curve = useEquityCurve(owner, account);
  const values = (curve.data?.points ?? []).map((p) => Number(p.equity?.value ?? NaN));
  return (
    <div className="grid grid-cols-[8rem_1fr_auto] items-center gap-3">
      <span className="flex min-w-0 items-center gap-2 text-xs">
        <span className={cn("size-2.5 shrink-0 rounded-sm", color)} aria-hidden />
        <span className="truncate font-medium">{owner}</span>
      </span>
      {curve.isError ? <span className="text-[11px] text-subtle">unavailable</span> : <Sparkline values={values} label={`${owner} equity`} />}
      <span className="font-mono text-xs tabular-nums">{equity}</span>
    </div>
  );
}

/**
 * Stacked bars of each instrument's quantity by owner (absolute quantities, drawn proportionally;
 * the exact numbers are in the table below, so nothing is conveyed by the drawing alone).
 */
function ExposureBars({ a, strategies }: { a: AccountView; strategies: string[] }) {
  if (a.instruments.length === 0) return null;
  return (
    <ul className="space-y-2" aria-hidden>
      {a.instruments.map((row) => {
        const parts = row.owners.map((o) => ({ owner: o.owner, q: Math.abs(Number(o.qty?.value ?? 0)) })).filter((p) => p.q > 0);
        const total = parts.reduce((s, p) => s + p.q, 0) || 1;
        return (
          <li key={row.instrumentId} className="grid grid-cols-[5rem_1fr] items-center gap-3 text-xs">
            <span className="truncate font-medium">{row.symbol || row.instrumentId}</span>
            <div className="flex h-3 overflow-hidden rounded-sm bg-panel-2">
              {parts.map((p) => (
                <span key={p.owner} className={cn("h-full", ownerColor(p.owner, strategies))} style={{ width: `${(p.q / total) * 100}%` }} title={`${p.owner}`} />
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Today's activity per account (New York trading day). */
function EndOfDay({ a }: { a: AccountView }) {
  const since = useMemo(() => nyDayStartNs(), []);
  const fills = useQuery({
    queryKey: ["fills", { account: a.brokerAccountId, since }],
    queryFn: () => api<FillsResponse>(`/api/v1/fills?account=${encodeURIComponent(a.brokerAccountId)}&since=${since}&limit=2000`),
    refetchInterval: 30_000,
  });
  const intents = useQuery({
    queryKey: ["intents", { rejected: true, since }],
    queryFn: () => api<IntentsResponse>(`/api/v1/intents?rejected_only=true&since=${since}&limit=2000`),
    refetchInterval: 30_000,
  });
  const recon = enumLabel(a.reconStatus, "RECONCILIATION_STATUS_");
  const fillCount = new Map<string, number>();
  for (const f of fills.data?.fills ?? []) fillCount.set(f.strategyId || "manual", (fillCount.get(f.strategyId || "manual") ?? 0) + 1);
  const rejects = (intents.data?.intents ?? []).filter((i) => i.intent?.brokerAccountId === a.brokerAccountId);
  return (
    <section aria-labelledby={`eod-${a.brokerAccountId}`} className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 id={`eod-${a.brokerAccountId}`} className="font-mono text-base font-semibold">
          {a.brokerAccountId}
        </h2>
        <Badge tone={recon === "ok" ? "ok" : "warn"}>recon {recon}</Badge>
        <span className="flex items-center gap-1 text-xs text-muted">
          <CalendarClock className="size-3.5" aria-hidden />
          since {formatNs(since)}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader icon={<Receipt />} title="Session P&L by owner" description="The ledger's day P&L (realized + unrealized change since the open, minus fees)." />
          <CardBody className="overflow-x-auto p-0 sm:p-0">
            <table className="data-table" aria-label={`Session P&L in ${a.brokerAccountId}`}>
              <thead>
                <tr>
                  <th className="pl-4 sm:pl-5">Owner</th>
                  <th className="num">Day P&amp;L</th>
                  <th className="num">Realized</th>
                  <th className="num">Unrealized</th>
                  <th className="num pr-4 sm:pr-5">Fills today</th>
                </tr>
              </thead>
              <tbody>
                {owners(a).map((o) => (
                  <tr key={o.owner}>
                    <td className="pl-4 font-medium sm:pl-5">{o.owner}</td>
                    <td className="num">
                      <Signed d={o.dayPnl} />
                    </td>
                    <td className="num">
                      <Signed d={o.realizedPnl} />
                    </td>
                    <td className="num">
                      <Signed d={o.unrealizedPnl} />
                    </td>
                    <td className="num pr-4 sm:pr-5">{fills.isSuccess ? (fillCount.get(o.owner) ?? 0) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
        <Card>
          <CardHeader icon={<ShieldAlert />} title="Rejected intents today" description="Every rejection with its first failed check (never hidden, UI-017)." />
          <CardBody>
            <QueryState q={intents} empty="" isEmpty={() => false}>
              {() =>
                rejects.length === 0 ? (
                  <EmptyState title="No rejected intents today" />
                ) : (
                  <ul className="divide-y divide-border text-xs">
                    {rejects.slice(0, 50).map((i) => (
                      <li key={i.intent?.intentId} className="flex flex-wrap items-center gap-2 py-2">
                        <Badge tone="danger">{i.decision?.firstFailure || "rejected"}</Badge>
                        <span className="font-medium">{i.intent?.strategyId || "manual"}</span>
                        <span className="text-muted">{enumLabel(i.intent?.side, "SIDE_")}</span>
                        <span className="ml-auto text-muted">{formatNs(i.receivedAt)}</span>
                      </li>
                    ))}
                  </ul>
                )
              }
            </QueryState>
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Fills today" description={`${fills.data?.fills.length ?? 0} fills · prices and fees as the broker reported them`} />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          <QueryState q={fills} empty="No fills today." isEmpty={(d) => d.fills.length === 0}>
            {(d) => (
              <table className="data-table" aria-label={`Fills today in ${a.brokerAccountId}`}>
                <thead>
                  <tr>
                    <th className="pl-4 sm:pl-5">Time</th>
                    <th>Owner</th>
                    <th>Instrument</th>
                    <th>Side</th>
                    <th className="num">Qty</th>
                    <th className="num">Price</th>
                    <th className="num pr-4 sm:pr-5">Fees</th>
                  </tr>
                </thead>
                <tbody>
                  {d.fills.map((f) => (
                    <tr key={f.brokerExecId}>
                      <td className="pl-4 text-muted sm:pl-5">{formatNs(f.eventTime)}</td>
                      <td className="font-medium">{f.strategyId || "manual"}</td>
                      <td className="font-mono">{f.instrumentId}</td>
                      <td>{enumLabel(f.side, "SIDE_")}</td>
                      <td className="num">{dec(f.qty)}</td>
                      <td className="num">{dec(f.price)}</td>
                      <td className="num pr-4 sm:pr-5">{dec(f.fees)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </QueryState>
        </CardBody>
      </Card>
    </section>
  );
}
