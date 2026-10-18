import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ExternalLink, Radio, ShieldCheck, Webhook } from "lucide-react";

import { ModeBadge, QueryState } from "@/components/trading";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { EmptyState, Page, PageHeader } from "@/components/ui/layout";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/utils";

/** GET /api/v1/tradingview (UI-048): webhook intake status; never any secret (SE-031, SE-064). */
export interface TradingViewStatus {
  enabled: boolean;
  /** Public webhook base URL (the dedicated Cloudflare Access hostname), or "" when unset. */
  public_url: string;
  access_jwt_required: boolean;
  allowed_ips: string[];
  max_age_seconds: number;
  widget_enabled: boolean;
  strategies: {
    strategy_id: string;
    name: string;
    mode: string;
    broker_account_id: string;
    webhook_path: string;
  }[];
  deliveries: {
    at: string;
    strategy_id: string;
    outcome: string;
    ticker: string;
    action: string;
    detail: string;
  }[];
}

/** The alert message to paste into TradingView. `<secret>` comes from `make tv-secret NAME=…` on the host. */
export function alertTemplate(): string {
  return JSON.stringify(
    { secret: "<secret from: make tv-secret NAME=…>", ticker: "{{ticker}}", action: "buy", time: "{{timenow}}", price: "{{close}}", alert_id: "{{strategy.order.id}}" },
    null,
    2,
  );
}

/**
 * TradingView integration (UI-048, D69): the opt-in `tv-<name>` webhook strategies (signals only,
 * through normal risk with their own ledger and limits), the dedicated Cloudflare Access ingress,
 * recent deliveries from the audit log, and chart options (Lightweight Charts with attribution,
 * deep links, and the optional widget).
 */
export function TradingViewPage() {
  const st = useQuery({ queryKey: ["tradingview"], queryFn: () => api<TradingViewStatus>("/api/v1/tradingview"), refetchInterval: 30_000 });
  return (
    <Page>
      <PageHeader
        eyebrow="Settings"
        title="TradingView"
        description="Use TradingView alerts as a signal source for a dedicated tv-<name> strategy, and open charts on tradingview.com. TradingView never places orders here."
      />
      <QueryState q={st} empty="" isEmpty={() => false}>
        {(d) => (
          <>
            {!d.enabled && (
              <Alert tone="info">
                Webhook intake is off (default). Turn it on with <code className="font-mono text-xs">TB_TV_WEBHOOK_ADDR</code> and a dedicated Cloudflare Access hostname; see the
                TradingView runbook.
              </Alert>
            )}
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              <Card>
                <CardHeader icon={<ShieldCheck />} title="Ingress" description="A separate listener and hostname that serves only the webhook path." />
                <CardBody className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
                  <span className="text-muted">Status</span>
                  <span>{d.enabled ? <Badge tone="ok">listening</Badge> : <Badge tone="neutral">off</Badge>}</span>
                  <span className="text-muted">Public URL</span>
                  <span className="truncate font-mono text-xs">{d.public_url || "—"}</span>
                  <span className="text-muted">Cloudflare Access JWT</span>
                  <span>{d.access_jwt_required ? <Badge tone="ok">required</Badge> : <Badge tone="warn">not verified</Badge>}</span>
                  <span className="text-muted">Source IPs</span>
                  <span className="font-mono text-xs">{d.allowed_ips.join(", ") || "any"}</span>
                  <span className="text-muted">Max alert age</span>
                  <span>{d.max_age_seconds} s</span>
                </CardBody>
              </Card>
              <Card>
                <CardHeader icon={<Webhook />} title="Alert message" description="Paste as the alert's message; the webhook URL is the strategy's path below." />
                <CardBody className="space-y-2 text-sm">
                  <pre className="overflow-x-auto rounded-lg border border-border bg-bg p-3 font-mono text-xs leading-relaxed">{alertTemplate()}</pre>
                  <p className="text-xs text-muted">
                    <span className="font-medium text-fg">action</span>: <code>buy</code> opens or adds long, <code>sell</code> opens short (only if the strategy allows shorts), <code>exit</code>{" "}
                    closes the strategy's own position. Each strategy has its own secret; it is shown only on the host terminal, never in this app.
                  </p>
                </CardBody>
              </Card>
            </div>
            <Card>
              <CardHeader icon={<Radio />} title="Webhook strategies" description="Registry entries with runtime class tradingview. Each has its own virtual ledger, allocation, and limits, and starts in paper." />
              <CardBody className="overflow-x-auto p-0 sm:p-0">
                {d.strategies.length === 0 ? (
                  <EmptyState title="No tv-<name> strategy registered">Add one to the strategy registry (runtime class tradingview); see the runbook.</EmptyState>
                ) : (
                  <table className="data-table" aria-label="TradingView strategies">
                    <thead>
                      <tr>
                        <th className="pl-4 sm:pl-5">Strategy</th>
                        <th>Mode</th>
                        <th>Account</th>
                        <th className="pr-4 sm:pr-5">Webhook path</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.strategies.map((s) => (
                        <tr key={s.strategy_id}>
                          <td className="pl-4 sm:pl-5">
                            <Link to="/strategies/$strategyId" params={{ strategyId: s.strategy_id }} className="font-medium hover:underline">
                              {s.strategy_id}
                            </Link>
                            {s.name && <span className="ml-2 text-xs text-muted">{s.name}</span>}
                          </td>
                          <td>
                            <ModeBadge mode={s.mode} />
                          </td>
                          <td className="font-mono text-xs">{s.broker_account_id}</td>
                          <td className="pr-4 font-mono text-xs sm:pr-5">
                            {d.public_url}
                            {s.webhook_path}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Recent deliveries" description="From the audit log (actor webhook:tradingview): accepted signals and authenticated requests that were refused." />
              <CardBody className="overflow-x-auto p-0 sm:p-0">
                {d.deliveries.length === 0 ? (
                  <EmptyState title="No webhook deliveries yet" />
                ) : (
                  <table className="data-table" aria-label="Webhook deliveries">
                    <thead>
                      <tr>
                        <th className="pl-4 sm:pl-5">Time</th>
                        <th>Strategy</th>
                        <th>Outcome</th>
                        <th>Ticker</th>
                        <th>Action</th>
                        <th className="pr-4 sm:pr-5">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.deliveries.map((x, i) => (
                        <tr key={`${x.at}-${i}`}>
                          <td className="pl-4 text-xs text-muted sm:pl-5">{formatTime(x.at)}</td>
                          <td className="font-mono text-xs">{x.strategy_id}</td>
                          <td>
                            <Badge tone={x.outcome === "accepted" ? "ok" : x.outcome === "duplicate" ? "neutral" : "warn"}>{x.outcome}</Badge>
                          </td>
                          <td className="font-mono text-xs">{x.ticker || "—"}</td>
                          <td className="text-xs">{x.action || "—"}</td>
                          <td className="max-w-[20rem] truncate pr-4 text-xs text-muted sm:pr-5" title={x.detail}>
                            {x.detail || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Charts" description="TradingView's licensed open-source library draws every chart here; tradingview.com opens in a new tab." />
              <CardBody className="space-y-2 text-sm text-muted">
                <p>
                  Charts use TradingView Lightweight Charts™ (Apache-2.0, attribution shown under each chart) with this platform's own data. The Advanced Charts library is not
                  used: its licence forbids private use (D12).
                </p>
                <p>
                  Each chart has an <span className="inline-flex items-center gap-1 text-fg">Open on TradingView <ExternalLink className="size-3" aria-hidden /></span> link. Nothing is sent
                  to TradingView until you click it. The embedded TradingView widget is {d.widget_enabled ? <Badge tone="warn">on</Badge> : <Badge tone="neutral">off</Badge>} (Platform
                  settings → <code className="font-mono text-xs">ui.tradingview_widget_enabled</code>); when on, the chart page can show TradingView's own chart in a sandboxed frame,
                  which tells TradingView which symbols you view.
                </p>
              </CardBody>
            </Card>
          </>
        )}
      </QueryState>
    </Page>
  );
}
