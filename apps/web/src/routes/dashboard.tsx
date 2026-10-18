import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowRight, BellRing, CandlestickChart, OctagonX, Plus, ShieldCheck, Snowflake, Wallet, Workflow } from "lucide-react";

import { Sparkline } from "@/components/Sparkline";
import { ModeBadge, Num, QueryState, Signed } from "@/components/trading";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { Page, PageHeader, Skeleton } from "@/components/ui/layout";
import { api, type Me } from "@/lib/api";
import { useAccounts, useEquityCurve, useStatus, useStrategies } from "@/lib/queries";
import { enumLabel, type OwnerView, type Strategy, type TradingCore } from "@/lib/trading";
import { formatNs, formatTime } from "@/lib/utils";

/**
 * Dashboard (section 14, UI-045): per-account equity, cash, buying power, and reconciliation; the
 * trading state with active kill switches and halts; strategy cards with mode, allocation, P&L,
 * health, and an equity sparkline; notification and security status. Every value is the service's.
 */
export function DashboardPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  const status = useStatus();
  const t = status.data?.trading ?? null;

  return (
    <Page wide>
      <PageHeader
        eyebrow={new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
        title={`Welcome${me.data ? `, ${me.data.owner.display_name}` : ""}`}
        description="Accounts, strategies, and trading state at a glance. Manual holdings and each strategy's virtual portfolio are shown separately."
        actions={
          <>
            <Link to="/charts" className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border px-3 text-xs font-medium hover:bg-panel-2">
              <CandlestickChart className="size-4" aria-hidden />
              Charts
            </Link>
            <Link to="/orders/new" className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-xs font-medium text-white hover:brightness-110">
              <Plus className="size-4" aria-hidden />
              Manual order
            </Link>
          </>
        }
      />
      {me.data && me.data.session.auth_method !== "passkey" && (
        <Alert tone="warn">
          You signed in with a {me.data.session.auth_method === "totp" ? "TOTP code" : "recovery code"}. Dangerous actions need a passkey —{" "}
          <Link to="/settings/security" className="underline">
            register one now
          </Link>
          .
        </Alert>
      )}
      {me.data && me.data.passkeys.length < 2 && (
        <Alert tone="warn">Only one passkey is registered. Add a second one (e.g. your phone) under Security so a lost device can't lock you out.</Alert>
      )}
      <AccountTiles />
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <StrategyCards />
        </div>
        <div className="space-y-5">
          <Card>
            <CardHeader
              icon={t && t.globalState !== "running" ? <Snowflake /> : <OctagonX />}
              title="Trading state"
              action={<Link to="/risk" className="text-xs text-muted hover:text-fg">Risk</Link>}
            />
            <CardBody className="space-y-3 text-sm">
              {status.isPending ? (
                <Skeleton className="h-16" />
              ) : status.isError ? (
                <p className="text-live">Status unavailable</p>
              ) : !t ? (
                <Badge tone="warn">Execution: unknown</Badge>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={t.globalState === "running" ? "ok" : "danger"}>{t.globalState === "running" ? "running" : "frozen"}</Badge>
                    <Badge tone={status.data?.mode === "live" ? "live" : "paper"}>{status.data?.mode}</Badge>
                    <span className="text-xs text-muted">{t.openOrders} open orders</span>
                  </div>
                  {t.activeKillSwitches.length === 0 && t.activeHalts.length === 0 ? (
                    <p className="text-xs text-muted">No kill switch or halt is active.</p>
                  ) : (
                    <ul className="space-y-1.5 text-xs" aria-label="Active kill switches and halts">
                      {t.activeKillSwitches.map((k) => (
                        <li key={k.eventId} className="flex flex-wrap items-center gap-2">
                          <Badge tone="danger">kill · {enumLabel(k.scope, "KILL_SWITCH_SCOPE_")}</Badge>
                          <span className="text-muted">{k.target || "all"}</span>
                          <span className="truncate">{k.reason}</span>
                        </li>
                      ))}
                      {t.activeHalts.map((h) => (
                        <li key={h.haltId} className="flex flex-wrap items-center gap-2">
                          <Badge tone="warn">halt</Badge>
                          <span className="text-muted">{[h.brokerAccountId, h.strategyId, h.instrumentId].filter(Boolean).join(" · ") || "all"}</span>
                          <span className="truncate">{h.reason}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader icon={<BellRing />} title="Notifications" action={<Link to="/settings/notifications" className="text-xs text-muted hover:text-fg">Manage</Link>} />
            <CardBody className="text-sm text-muted">
              Critical alerts page your phone through Pushover emergency priority; the installed app also receives Web Push.
            </CardBody>
          </Card>
          <Card>
            <CardHeader icon={<ShieldCheck />} title="Session and security" action={<Link to="/settings/security" className="text-xs text-muted hover:text-fg">Security</Link>} />
            <CardBody className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
              {me.data ? (
                <>
                  <span className="text-muted">Signed in with</span>
                  <span>{me.data.session.auth_method}</span>
                  <span className="text-muted">Idle timeout</span>
                  <span>{formatTime(me.data.session.idle_expires_at)}</span>
                  <span className="text-muted">Passkeys</span>
                  <span>{me.data.passkeys.length}</span>
                  <span className="text-muted">Authenticator app</span>
                  <span>{me.data.totp_enabled ? "enabled" : "not set up"}</span>
                  <span className="text-muted">Recovery codes left</span>
                  <span>{me.data.recovery_codes_left}</span>
                </>
              ) : (
                <Skeleton className="col-span-2 h-16" />
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </Page>
  );
}

/** One tile per broker account: equity, cash, buying power, manual day P&L, reconciliation. */
function AccountTiles() {
  const accounts = useAccounts();
  if (accounts.isPending) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
    );
  }
  return (
    <section aria-labelledby="accounts-heading" className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 id="accounts-heading" className="flex items-center gap-2 text-sm font-semibold">
          <Wallet className="size-4 text-muted" aria-hidden />
          Accounts
        </h2>
        <Link to="/overview" className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg">
          Portfolio overview <ArrowRight className="size-3" aria-hidden />
        </Link>
      </div>
      <QueryState q={accounts} empty="No broker account has reported yet." isEmpty={(d) => d.accounts.length === 0}>
        {(d) => (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {d.accounts.map((a) => {
              const recon = enumLabel(a.reconStatus, "RECONCILIATION_STATUS_");
              return (
                <div key={a.brokerAccountId} className="space-y-2 rounded-xl border border-border bg-panel p-4 shadow-panel">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate font-mono text-xs font-semibold">{a.brokerAccountId}</p>
                    <Badge tone={recon === "ok" ? "ok" : "warn"}>recon {recon}</Badge>
                  </div>
                  <div>
                    <p className="text-[11px] uppercase tracking-wider text-muted">Equity</p>
                    <p className="font-mono text-xl font-semibold tabular-nums">
                      <Num d={a.brokerSnapshot?.equity} />
                    </p>
                  </div>
                  <dl className="grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <dt className="text-muted">Cash</dt>
                      <dd className="font-mono tabular-nums">
                        <Num d={a.brokerSnapshot?.cash} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted">Buying power</dt>
                      <dd className="font-mono tabular-nums">
                        <Num d={a.brokerSnapshot?.buyingPower} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-muted">Manual day P&amp;L</dt>
                      <dd className="font-mono">
                        <Signed d={a.manual?.dayPnl} />
                      </dd>
                    </div>
                  </dl>
                  <p className="text-[11px] text-subtle">
                    {a.owners.length} strateg{a.owners.length === 1 ? "y" : "ies"} · reconciled {formatNs(a.reconAsOf)}
                  </p>
                </div>
              );
            })}
          </div>
        )}
      </QueryState>
    </section>
  );
}

/** Strategy cards (UI-014): mode, allocation, equity, day P&L, health, and an equity sparkline. */
function StrategyCards() {
  const strategies = useStrategies();
  const accounts = useAccounts();
  const status = useStatus();
  const views = new Map((accounts.data?.accounts ?? []).flatMap((a) => a.owners.map((o) => [o.owner, o] as const)));
  const health = new Map((status.data?.trading?.strategies ?? []).map((s) => [s.strategyId, s]));
  return (
    <Card>
      <CardHeader icon={<Workflow />} title="Strategies" description="Each strategy trades only its own virtual portfolio." action={<Link to="/strategies" className="text-xs text-muted hover:text-fg">All strategies</Link>} />
      <CardBody>
        <QueryState q={strategies} empty="No strategies registered yet." isEmpty={(d) => d.strategies.length === 0}>
          {(d) => (
            <div className="grid gap-3 md:grid-cols-2">
              {d.strategies.map((s) => (
                <StrategyCard key={s.strategyId} s={s} view={views.get(s.strategyId)} health={health.get(s.strategyId)} />
              ))}
            </div>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}

type Health = TradingCore["strategies"][number];

function StrategyCard({ s, view, health }: { s: Strategy; view: OwnerView | undefined; health: Health | undefined }) {
  const curve = useEquityCurve(s.strategyId, s.brokerAccountId);
  const values = (curve.data?.points ?? []).map((p) => Number(p.equity?.value ?? NaN));
  return (
    <div className="space-y-3 rounded-lg border border-border bg-panel-2/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <Link to="/strategies/$strategyId" params={{ strategyId: s.strategyId }} className="block truncate text-sm font-medium hover:underline">
            {s.name || s.strategyId}
          </Link>
          <p className="truncate font-mono text-[11px] text-muted">
            {s.brokerAccountId} · {enumLabel(s.horizon, "HORIZON_")}
          </p>
        </div>
        <ModeBadge mode={s.mode} />
      </div>
      <Sparkline values={values} label={`${s.strategyId} equity`} />
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted">Allocation</dt>
        <dd className="text-right font-mono">
          <Num d={view?.allocation} />
        </dd>
        <dt className="text-muted">Equity</dt>
        <dd className="text-right font-mono">
          <Num d={view?.equity} />
        </dd>
        <dt className="text-muted">Day P&amp;L</dt>
        <dd className="text-right font-mono">
          <Signed d={view?.dayPnl} />
        </dd>
        <dt className="text-muted">Health</dt>
        <dd className="text-right" title={health?.blockedReasons.join("; ") || undefined}>
          {!health ? <Badge tone="neutral">unknown</Badge> : health.acceptingIntents ? <Badge tone="ok">accepting</Badge> : <Badge tone="warn">blocked</Badge>}
        </dd>
      </dl>
    </div>
  );
}

