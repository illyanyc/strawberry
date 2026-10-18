import { Link } from "@tanstack/react-router";
import { AlertTriangle, Clock, Cpu, Radio, Server } from "lucide-react";
import type { ReactNode } from "react";

import type { TradingStatus } from "@/lib/api";
import { useNow } from "@/lib/events";
import { ageLabel, ageSeconds, enumLabel, type FeedStatus } from "@/lib/trading";
import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "danger" | "neutral";

const tones: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-live",
  neutral: "text-muted",
};

function Chip({ tone, icon, children, title, className }: { tone: Tone; icon?: ReactNode; children: ReactNode; title?: string; className?: string }) {
  return (
    <li
      className={cn("flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-panel-2/60 px-2 py-0.5 [&_svg]:size-3", className)}
      title={title}
    >
      <span className={cn("size-1.5 rounded-full", tone === "ok" ? "bg-ok" : tone === "warn" ? "bg-warn" : tone === "danger" ? "bg-live" : "bg-subtle")} aria-hidden />
      {icon && (
        <span className="text-subtle" aria-hidden>
          {icon}
        </span>
      )}
      <span className={tones[tone]}>{children}</span>
    </li>
  );
}

/** A feed is healthy when connected, in state ok/streaming, and has no stale symbols. */
export function feedTone(f: FeedStatus): Tone {
  if (!f.connected) return "danger";
  if (f.symbolsStale.length > 0) return "warn";
  const st = f.state.toLowerCase();
  return st === "" || st === "ok" || st === "streaming" || st === "connected" ? "ok" : "warn";
}

/**
 * Global trading status (UI-013): execution state, each broker account's session, reconciliation,
 * margin flags and day-trade count, market-data feed freshness, clock, strategy-runtime heartbeat,
 * open orders, and halts. Unreachable services show "unknown", never a healthy state. Every chip
 * states its condition in text; the dot is a redundant cue.
 */
export function StatusStrip({ status }: { status: TradingStatus | null }) {
  const now = useNow();
  if (!status) return <div className="h-8 border-b border-border bg-panel" aria-hidden />;
  const t = status.trading;
  const feeds = status.feeds?.feeds ?? null;
  return (
    <div className="border-b border-border bg-panel">
      <ul aria-label="Trading status" className="flex items-center gap-1.5 overflow-x-auto px-3 py-1.5 text-[11px] [scrollbar-width:none] sm:px-4">
        {!t ? (
          <Chip tone="warn" icon={<Server />}>
            Execution: unknown
          </Chip>
        ) : (
          <>
            <Chip tone={t.globalState === "running" ? "ok" : "danger"} icon={<Server />}>
              {t.globalState === "running" ? "Running" : "Frozen"}
            </Chip>
            {t.accounts.map((a) => {
              const recon = enumLabel(a.reconciliationStatus, "RECONCILIATION_STATUS_");
              const healthy = a.acceptingIntents && recon === "ok";
              const margin = a.intradayMarginDeficit || a.marginCall;
              return (
                <Chip key={a.brokerAccountId} tone={margin ? "danger" : healthy ? "ok" : "warn"} title={a.blockedReasons.join("; ") || undefined}>
                  {a.brokerAccountId}: {enumLabel(a.sessionState, "SESSION_STATE_")} · recon {recon}
                  {margin ? " · margin!" : ""} · DT {a.dayTradeCount}
                  {a.pdtGuard === "legacy" ? " (PDT on)" : ""}
                </Chip>
              );
            })}
            {t.activeHalts.length > 0 && (
              <Chip tone="warn" icon={<AlertTriangle />}>
                <Link to="/risk" className="underline-offset-2 hover:underline">
                  {t.activeHalts.length} halt{t.activeHalts.length > 1 ? "s" : ""}
                </Link>
              </Chip>
            )}
            <Chip tone={t.clockOk ? "ok" : "danger"} icon={<Clock />}>
              Clock {t.clockOk ? "ok" : "skewed"}
            </Chip>
            <RuntimeChip ns={t.runtimeLastHeartbeat} now={now} />
            <Chip tone="neutral">{t.openOrders} open order{t.openOrders === 1 ? "" : "s"}</Chip>
          </>
        )}
        {feeds === null ? (
          <Chip tone="warn" icon={<Radio />}>
            Feeds: unknown
          </Chip>
        ) : feeds.length === 0 ? (
          <Chip tone="neutral" icon={<Radio />}>
            No feeds
          </Chip>
        ) : (
          feeds.map((f) => (
            <Chip
              key={f.feed}
              tone={feedTone(f)}
              icon={<Radio />}
              title={f.symbolsStale.length ? `stale: ${f.symbolsStale.slice(0, 20).join(", ")}` : undefined}
            >
              {f.feed.toUpperCase()} {f.connected ? `${ageLabel(ageSeconds(f.lastMessageAt, now))} ago` : "disconnected"}
              {f.symbolsStale.length > 0 ? ` · ${f.symbolsStale.length} stale` : ""}
            </Chip>
          ))
        )}
      </ul>
    </div>
  );
}

function RuntimeChip({ ns, now }: { ns: string; now: number }) {
  const age = ageSeconds(ns, now);
  const tone: Tone = age === null ? "neutral" : age <= 30 ? "ok" : "warn";
  return (
    <Chip tone={tone} icon={<Cpu />} title="Strategy-runtime heartbeat">
      Runtime {age === null ? "not seen" : `${ageLabel(age)} ago`}
    </Chip>
  );
}
