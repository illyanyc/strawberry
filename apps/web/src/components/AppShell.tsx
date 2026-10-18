import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";

import { EnvironmentBanner } from "@/components/EnvironmentBanner";
import { GlobalKillSwitch } from "@/components/KillSwitch";
import { Badge } from "@/components/ui/form";
import { Button } from "@/components/ui/button";
import { api, type TradingStatus } from "@/lib/api";
import { STALE_AFTER_MS, useNow, useTradingStatus } from "@/lib/events";
import { enumLabel } from "@/lib/trading";
import { cn, formatTime } from "@/lib/utils";

const nav = [
  { to: "/", label: "Dashboard", short: "Home" },
  { to: "/portfolio", label: "Portfolio", short: "Portfolio" },
  { to: "/strategies", label: "Strategies", short: "Strategies" },
  { to: "/orders", label: "Orders", short: "Orders" },
  { to: "/charts", label: "Charts", short: "Charts" },
  { to: "/risk", label: "Risk & kill switches", short: "Risk" },
  { to: "/settings/security", label: "Security", short: "Security" },
  { to: "/settings/brokers", label: "Broker connections", short: "Brokers" },
  { to: "/settings/platform", label: "Platform settings", short: "Settings" },
  { to: "/audit", label: "Audit log", short: "Audit" },
] as const;

/** Authenticated layout: environment banner, trading status bar, navigation (sidebar / bottom bar). */
export function AppShell() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const onSessionEnded = useCallback(() => void navigate({ to: "/login" }), [navigate]);
  const stream = useTradingStatus(onSessionEnded);
  const { connected, lastEventAt } = stream;
  // The REST snapshot is refetched right after a settings change; the SSE stream updates periodically.
  // Show whichever the server produced most recently so the banner never lags a change.
  const snapshot = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });
  const status = latest(stream.status, snapshot.data ?? null);
  const now = useNow();
  const stale = lastEventAt === null || now - lastEventAt > STALE_AFTER_MS;

  const logout = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    // Never show one session's data to the next one.
    queryClient.clear();
    void navigate({ to: "/login" });
  };

  return (
    <div className="flex min-h-full flex-col">
      <EnvironmentBanner mode={status?.mode ?? null} environment={status?.environment} />
      <header className="flex items-center gap-3 border-b border-border bg-panel px-4 py-2 text-xs">
        <span className="font-semibold tracking-tight text-fg">🍓 Strawberry</span>
        <span className="hidden text-muted sm:inline">·</span>
        <span className="flex items-center gap-1.5" title={lastEventAt ? `last update ${formatTime(new Date(lastEventAt).toISOString())}` : "no updates yet"}>
          <span className={cn("size-2 rounded-full", connected && !stale ? "bg-ok" : "bg-warn")} aria-hidden />
          <span className="text-muted">{connected && !stale ? "Live status" : "Status stale"}</span>
        </span>
        <TradingBadges status={status} />
        <div className="ml-auto flex items-center gap-2">
          <GlobalKillSwitch trading={status?.trading} />
          <Button variant="ghost" size="sm" onClick={logout}>
            Log out
          </Button>
        </div>
      </header>
      <div className="flex flex-1">
        <nav aria-label="Main" className="hidden w-56 shrink-0 border-r border-border bg-panel p-3 md:block">
          <ul className="space-y-1">
            {nav.map((n) => (
              <li key={n.to}>
                <Link
                  to={n.to}
                  activeOptions={{ exact: n.to === "/" }}
                  className="block rounded-lg px-3 py-2 text-sm text-muted hover:bg-panel-2 hover:text-fg"
                  activeProps={{ className: "bg-panel-2 text-fg font-medium" }}
                >
                  {n.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <main className="flex-1 overflow-x-hidden p-4 pb-24 md:p-8 md:pb-8">
          <Outlet />
        </main>
      </div>
      <nav aria-label="Main mobile" className="fixed inset-x-0 bottom-0 z-10 flex overflow-x-auto border-t border-border bg-panel md:hidden">
        {nav.map((n) => (
          <Link
            key={n.to}
            to={n.to}
            activeOptions={{ exact: n.to === "/" }}
            className="min-w-[4.5rem] flex-1 px-1 py-3 text-center text-[11px] text-muted"
            activeProps={{ className: "text-fg font-semibold" }}
          >
            {n.short}
          </Link>
        ))}
      </nav>
    </div>
  );
}

/**
 * Global trading status (UI-013): execution state, each broker account's session, reconciliation,
 * margin flags, and day-trade count. Execution unreachable shows "unknown", never a healthy state.
 */
function TradingBadges({ status }: { status: TradingStatus | null }) {
  if (!status) return null;
  const t = status.trading;
  if (!t) {
    return (
      <span className="flex flex-wrap gap-2">
        <Badge tone="warn">Execution: unknown</Badge>
      </span>
    );
  }
  const frozen = t.globalState !== "running";
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Badge tone={frozen ? "danger" : "ok"}>{frozen ? "Frozen" : "Running"}</Badge>
      {t.accounts.map((a) => {
        const recon = enumLabel(a.reconciliationStatus, "RECONCILIATION_STATUS_");
        const healthy = a.acceptingIntents && recon === "ok";
        return (
          <span key={a.brokerAccountId} className="hidden lg:inline-flex" title={a.blockedReasons.join("; ") || undefined}>
            <Badge tone={healthy ? "neutral" : "warn"}>
              {a.brokerAccountId}: {enumLabel(a.sessionState, "SESSION_STATE_")} · recon {recon}
              {a.intradayMarginDeficit || a.marginCall ? " · margin!" : ""} · DT {a.dayTradeCount}
              {a.pdtGuard === "legacy" ? " (PDT on)" : ""}
            </Badge>
          </span>
        );
      })}
      {t.activeHalts.length > 0 && <Badge tone="warn">{t.activeHalts.length} halt{t.activeHalts.length > 1 ? "s" : ""}</Badge>}
    </span>
  );
}

function latest(a: TradingStatus | null, b: TradingStatus | null): TradingStatus | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a.server_time) >= Date.parse(b.server_time) ? a : b;
}
