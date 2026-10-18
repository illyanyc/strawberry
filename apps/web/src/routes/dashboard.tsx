import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { ModeBadge, Num, QueryState, Signed } from "@/components/trading";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { api, type Me, type TradingStatus } from "@/lib/api";
import { enumLabel } from "@/lib/trading";
import { formatNs, formatTime } from "@/lib/utils";
import { useAccounts } from "@/routes/portfolio";
import { useStrategies } from "@/routes/strategies";

const phases = [
  { n: 0, name: "Foundation", detail: "Auth, secrets, audit, local stack, observability", done: true },
  { n: 1, name: "Data and brokers", detail: "Alpaca + IBKR paper, market data, charts", done: true },
  { n: 2, name: "Trading core on paper", detail: "Ledgers, OMS, risk, kill switches", done: true },
  { n: 3, name: "Research core", detail: "Backtester, validation, context data", done: false },
  { n: 4, name: "ML and optimization", detail: "Model zoo, studies, assistant", done: false },
];

export function DashboardPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Welcome{me.data ? `, ${me.data.owner.display_name}` : ""}</h1>
        <p className="mt-1 text-sm text-muted">Paper trading core: virtual ledgers, pre-trade risk, kill switches, and the ORB baseline.</p>
      </div>
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
      <AccountsSummary />
      <StrategyCards />
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader title="Trading mode" />
          <CardBody className="space-y-2 text-sm">
            {status.isPending ? <p className="text-muted">Loading…</p> : status.isError ? <p className="text-live">Status unavailable</p> : (
              <>
                <Badge tone={status.data.mode === "live" ? "live" : "paper"}>{status.data.mode}</Badge>
                <p className="text-muted">Live trading flag: {status.data.live_enabled ? "on" : "off"}</p>
              </>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Session" />
          <CardBody className="space-y-1 text-sm text-muted">
            {me.data ? (
              <>
                <p>
                  Method: <span className="text-fg">{me.data.session.auth_method}</span>
                </p>
                <p>Idle timeout: {formatTime(me.data.session.idle_expires_at)}</p>
                <p>Ends at: {formatTime(me.data.session.absolute_expires_at)}</p>
              </>
            ) : (
              <p>Loading…</p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Account security" />
          <CardBody className="space-y-1 text-sm text-muted">
            {me.data ? (
              <>
                <p>
                  Passkeys: <span className="text-fg">{me.data.passkeys.length}</span>
                </p>
                <p>
                  Authenticator app: <span className="text-fg">{me.data.totp_enabled ? "enabled" : "not set up"}</span>
                </p>
                <p>
                  Recovery codes left: <span className="text-fg">{me.data.recovery_codes_left}</span>
                </p>
              </>
            ) : (
              <p>Loading…</p>
            )}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Build progress" description="Each phase ends with an acceptance gate." />
        <CardBody>
          <ol className="space-y-3">
            {phases.map((p) => (
              <li key={p.n} className="flex items-center gap-3 text-sm">
                <Badge tone={p.done ? "ok" : "neutral"}>{p.done ? "built" : "planned"}</Badge>
                <span className="font-medium">
                  Phase {p.n}: {p.name}
                </span>
                <span className="hidden text-muted sm:inline">— {p.detail}</span>
              </li>
            ))}
          </ol>
        </CardBody>
      </Card>
    </div>
  );
}

/** Accounts summary (UI-016): broker equity, cash, buying power, and reconciliation per account. */
function AccountsSummary() {
  const accounts = useAccounts();
  return (
    <Card>
      <CardHeader title="Accounts" action={<Link to="/portfolio" className="text-xs text-muted underline">Portfolio</Link>} />
      <CardBody className="overflow-x-auto">
        <QueryState q={accounts} empty="No broker account has reported yet." isEmpty={(d) => d.accounts.length === 0}>
          {(d) => (
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted">
                <tr>
                  <th className="py-2 pr-4 font-medium">Account</th>
                  <th className="py-2 pr-4 font-medium">Equity</th>
                  <th className="py-2 pr-4 font-medium">Cash</th>
                  <th className="py-2 pr-4 font-medium">Buying power</th>
                  <th className="py-2 pr-4 font-medium">Manual day P&amp;L</th>
                  <th className="py-2 pr-4 font-medium">Reconciliation</th>
                </tr>
              </thead>
              <tbody>
                {d.accounts.map((a) => {
                  const recon = enumLabel(a.reconStatus, "RECONCILIATION_STATUS_");
                  return (
                    <tr key={a.brokerAccountId} className="border-t border-border">
                      <td className="py-2 pr-4 font-medium">{a.brokerAccountId}</td>
                      <td className="py-2 pr-4">
                        <Num d={a.brokerSnapshot?.equity} />
                      </td>
                      <td className="py-2 pr-4">
                        <Num d={a.brokerSnapshot?.cash} />
                      </td>
                      <td className="py-2 pr-4">
                        <Num d={a.brokerSnapshot?.buyingPower} />
                      </td>
                      <td className="py-2 pr-4">
                        <Signed d={a.manual?.dayPnl} />
                      </td>
                      <td className="py-2 pr-4">
                        <Badge tone={recon === "ok" ? "ok" : "warn"}>{recon}</Badge>
                        <span className="ml-2 text-xs text-muted">{formatNs(a.reconAsOf)}</span>
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
  );
}

/** Strategy cards (UI-014): mode, allocation, P&L, and health. Values are the ledger's. */
function StrategyCards() {
  const strategies = useStrategies();
  const accounts = useAccounts();
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });
  const views = new Map((accounts.data?.accounts ?? []).flatMap((a) => a.owners.map((o) => [o.owner, o] as const)));
  const health = new Map((status.data?.trading?.strategies ?? []).map((s) => [s.strategyId, s]));
  return (
    <QueryState q={strategies} empty="No strategies registered yet." isEmpty={(d) => d.strategies.length === 0}>
      {(d) => (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {d.strategies.map((s) => {
            const v = views.get(s.strategyId);
            const h = health.get(s.strategyId);
            return (
              <Card key={s.strategyId}>
                <CardHeader
                  title={
                    <Link to="/strategies/$strategyId" params={{ strategyId: s.strategyId }} className="hover:underline">
                      {s.name || s.strategyId}
                    </Link>
                  }
                  description={`${s.brokerAccountId} · ${enumLabel(s.horizon, "HORIZON_")}`}
                  action={<ModeBadge mode={s.mode} />}
                />
                <CardBody className="grid grid-cols-2 gap-y-1 text-sm">
                  <span className="text-muted">Allocation</span>
                  <Num d={v?.allocation} />
                  <span className="text-muted">Equity</span>
                  <Num d={v?.equity} />
                  <span className="text-muted">Day P&amp;L</span>
                  <Signed d={v?.dayPnl} />
                  <span className="text-muted">Health</span>
                  <span title={h?.blockedReasons.join("; ") || undefined}>
                    {!h ? <Badge tone="neutral">unknown</Badge> : h.acceptingIntents ? <Badge tone="ok">accepting</Badge> : <Badge tone="warn">blocked</Badge>}
                  </span>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
    </QueryState>
  );
}
