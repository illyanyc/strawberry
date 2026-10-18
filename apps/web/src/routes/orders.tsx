import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { QueryState, RiskChecks } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import { dec, enumLabel, isTerminal, type Fill, type FillsResponse, type IntentRecord, type IntentsResponse, type Order, type OrdersResponse, type TraceResponse } from "@/lib/trading";
import { cn, formatNs } from "@/lib/utils";

type Tab = "orders" | "fills" | "rejected";

function query(params: Record<string, string | boolean | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "" && v !== false) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/**
 * Orders and blotter (UI-017): orders with their state and owner, fills, and rejected intents
 * with every failed risk check (never hidden). Filters by strategy (or `manual`) and account;
 * open orders can be canceled; any row opens the correlation trace.
 */
export function OrdersPage() {
  const [tab, setTab] = useState<Tab>("orders");
  const [strategy, setStrategy] = useState("");
  const [account, setAccount] = useState("");
  const [openOnly, setOpenOnly] = useState(false);
  const [trace, setTrace] = useState<string | null>(null);
  const filters = { strategy_id: strategy, account };
  const orders = useQuery({
    queryKey: ["orders", filters, openOnly],
    enabled: tab === "orders",
    queryFn: () => api<OrdersResponse>(`/api/v1/orders${query({ ...filters, open_only: openOnly, limit: "500" })}`),
    refetchInterval: 5_000,
  });
  const fills = useQuery({
    queryKey: ["fills", filters],
    enabled: tab === "fills",
    queryFn: () => api<FillsResponse>(`/api/v1/fills${query({ ...filters, limit: "500" })}`),
    refetchInterval: 5_000,
  });
  const rejected = useQuery({
    queryKey: ["intents", "rejected", strategy],
    enabled: tab === "rejected",
    queryFn: () => api<IntentsResponse>(`/api/v1/intents${query({ strategy_id: strategy, rejected_only: true, limit: "200" })}`),
    refetchInterval: 10_000,
  });
  const validFilter = (v: string) => v === "" || /^[A-Za-z0-9_-]{1,64}$/.test(v);

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Orders</h1>
        <Link to="/orders/new" className="ml-auto inline-flex h-10 items-center rounded-lg bg-accent px-4 text-sm font-medium text-white hover:brightness-110">
          New manual order
        </Link>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div role="tablist" aria-label="Blotter" className="flex gap-1">
          {(["orders", "fills", "rejected"] as Tab[]).map((t) => (
            <Button key={t} role="tab" aria-selected={tab === t} size="sm" variant={tab === t ? "secondary" : "ghost"} onClick={() => setTab(t)}>
              {t === "rejected" ? "Rejected intents" : t[0]!.toUpperCase() + t.slice(1)}
            </Button>
          ))}
        </div>
        <div className="w-44">
          <Field label="Strategy or manual" value={strategy} onChange={(e) => setStrategy(e.target.value.trim())} error={validFilter(strategy) ? null : "invalid"} />
        </div>
        <div className="w-44">
          <Field label="Account" value={account} onChange={(e) => setAccount(e.target.value.trim())} error={validFilter(account) ? null : "invalid"} />
        </div>
        {tab === "orders" && (
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Open only
          </label>
        )}
      </div>
      <Card>
        <CardBody className="overflow-x-auto">
          {tab === "orders" && (
            <QueryState q={orders} empty="No orders." isEmpty={(d) => d.orders.length === 0}>
              {(d) => <OrdersTable orders={d.orders} onTrace={setTrace} />}
            </QueryState>
          )}
          {tab === "fills" && (
            <QueryState q={fills} empty="No fills." isEmpty={(d) => d.fills.length === 0}>
              {(d) => <FillsTable fills={d.fills} onTrace={setTrace} />}
            </QueryState>
          )}
          {tab === "rejected" && (
            <QueryState q={rejected} empty="No rejected intents." isEmpty={(d) => d.intents.length === 0}>
              {(d) => <RejectedList intents={d.intents} onTrace={setTrace} />}
            </QueryState>
          )}
        </CardBody>
      </Card>
      {trace && <TraceDialog correlationId={trace} onClose={() => setTrace(null)} />}
    </div>
  );
}

function stateTone(state: string): "ok" | "warn" | "danger" | "neutral" {
  if (state === "ORDER_STATE_FILLED") return "ok";
  if (state === "ORDER_STATE_REJECTED" || state === "ORDER_STATE_UNKNOWN") return "danger";
  if (isTerminal(state)) return "neutral";
  return "warn";
}

function OrdersTable({ orders, onTrace }: { orders: Order[]; onTrace: (c: string) => void }) {
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: (intentId: string) => api(`/api/v1/intents/${encodeURIComponent(intentId)}`, { method: "DELETE" }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["orders"] }),
  });
  return (
    <>
      {cancel.isError && <Alert>{cancel.error instanceof ApiError ? cancel.error.message : "Cancel failed"}</Alert>}
      <table className="w-full text-left text-sm" aria-label="Orders">
        <thead className="text-xs text-muted">
          <tr>
            <th className="py-2 pr-3 font-medium">Created</th>
            <th className="py-2 pr-3 font-medium">Owner</th>
            <th className="py-2 pr-3 font-medium">Instrument</th>
            <th className="py-2 pr-3 font-medium">Side</th>
            <th className="py-2 pr-3 font-medium">Qty</th>
            <th className="py-2 pr-3 font-medium">Type</th>
            <th className="py-2 pr-3 font-medium">Price</th>
            <th className="py-2 pr-3 font-medium">Role</th>
            <th className="py-2 pr-3 font-medium">State</th>
            <th className="py-2 pr-3 font-medium">Filled</th>
            <th className="py-2 font-medium" />
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.orderId} className="border-t border-border">
              <td className="py-2 pr-3 text-xs text-muted">{formatNs(o.createdAt)}</td>
              <td className="py-2 pr-3">{o.strategyId || "manual"}</td>
              <td className="py-2 pr-3 font-mono text-xs">{o.instrumentId}</td>
              <td className="py-2 pr-3">{enumLabel(o.side, "SIDE_")}</td>
              <td className="py-2 pr-3 tabular-nums">{dec(o.quantity)}</td>
              <td className="py-2 pr-3">{enumLabel(o.orderType, "ORDER_TYPE_")}</td>
              <td className="py-2 pr-3 tabular-nums">{o.stopPrice?.value ? `stop ${o.stopPrice.value}` : dec(o.limitPrice)}</td>
              <td className="py-2 pr-3">{enumLabel(o.role, "ORDER_ROLE_")}</td>
              <td className="py-2 pr-3">
                <span title={o.rejectReason || undefined}>
                  <Badge tone={stateTone(o.state)}>{enumLabel(o.state, "ORDER_STATE_")}</Badge>
                </span>
              </td>
              <td className="py-2 pr-3 tabular-nums">
                {dec(o.filledQty)} {o.avgPrice?.value ? `@ ${o.avgPrice.value}` : ""}
              </td>
              <td className="flex gap-1 py-2">
                <Button size="sm" variant="ghost" onClick={() => onTrace(o.correlationId)} disabled={!o.correlationId}>
                  Trace
                </Button>
                {!isTerminal(o.state) && (
                  <Button size="sm" variant="ghost" busy={cancel.isPending && cancel.variables === o.intentId} onClick={() => cancel.mutate(o.intentId)}>
                    Cancel
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function FillsTable({ fills, onTrace }: { fills: Fill[]; onTrace: (c: string) => void }) {
  return (
    <table className="w-full text-left text-sm" aria-label="Fills">
      <thead className="text-xs text-muted">
        <tr>
          <th className="py-2 pr-3 font-medium">Time</th>
          <th className="py-2 pr-3 font-medium">Owner</th>
          <th className="py-2 pr-3 font-medium">Instrument</th>
          <th className="py-2 pr-3 font-medium">Side</th>
          <th className="py-2 pr-3 font-medium">Qty</th>
          <th className="py-2 pr-3 font-medium">Price</th>
          <th className="py-2 pr-3 font-medium">Fees</th>
          <th className="py-2 font-medium" />
        </tr>
      </thead>
      <tbody>
        {fills.map((f) => (
          <tr key={f.brokerExecId} className="border-t border-border">
            <td className="py-2 pr-3 text-xs text-muted">{formatNs(f.eventTime)}</td>
            <td className="py-2 pr-3">{f.strategyId || "manual"}</td>
            <td className="py-2 pr-3 font-mono text-xs">{f.instrumentId}</td>
            <td className="py-2 pr-3">{enumLabel(f.side, "SIDE_")}</td>
            <td className="py-2 pr-3 tabular-nums">{dec(f.qty)}</td>
            <td className="py-2 pr-3 tabular-nums">{dec(f.price)}</td>
            <td className="py-2 pr-3 tabular-nums">{dec(f.fees)}</td>
            <td className="py-2">
              <Button size="sm" variant="ghost" onClick={() => onTrace(f.correlationId)} disabled={!f.correlationId}>
                Trace
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RejectedList({ intents, onTrace }: { intents: IntentRecord[]; onTrace: (c: string) => void }) {
  return (
    <ul className="space-y-3" aria-label="Rejected intents">
      {intents.map((r) => {
        const failed = r.decision?.checks.filter((c) => !c.passed) ?? [];
        return (
          <li key={r.intent?.intentId ?? r.receivedAt} className="rounded-lg border border-border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="danger">rejected</Badge>
              <span className="font-medium">{r.intent?.strategyId || "manual"}</span>
              <span>
                {enumLabel(r.intent?.side, "SIDE_")} {dec(r.intent?.quantity ?? r.intent?.targetPosition)} <span className="font-mono text-xs">{r.intent?.instrumentId}</span>
              </span>
              <span className="text-xs text-muted">{formatNs(r.receivedAt || r.intent?.createdAt)}</span>
              <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onTrace(r.intent?.correlationId ?? "")} disabled={!r.intent?.correlationId}>
                Trace
              </Button>
            </div>
            <p className="mt-1 text-live">
              {failed.length ? failed.map((c) => `${c.name}${c.detail ? `: ${c.detail}` : ""}`).join(" · ") : r.decision?.firstFailure || r.state}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

/** Correlation trace (UI-017): signal → intent → decision → orders → fills for one correlation ID. */
function TraceDialog({ correlationId, onClose }: { correlationId: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["trace", correlationId], queryFn: () => api<TraceResponse>(`/api/v1/trace/${encodeURIComponent(correlationId)}`) });
  return (
    <Dialog open onClose={onClose} title="Correlation trace">
      <div className="max-h-[70vh] space-y-4 overflow-y-auto text-sm">
        <p className="font-mono text-xs text-muted">{correlationId}</p>
        <QueryState q={q} empty="Nothing recorded for this correlation ID." isEmpty={(d) => d.intents.length + d.orders.length + d.fills.length === 0}>
          {(d) => (
            <ol className="space-y-3">
              {d.intents.map((r) => (
                <li key={r.intent?.intentId ?? r.receivedAt} className="space-y-2">
                  <p>
                    <Step n="1" /> Intent {r.intent?.intentId} from {r.intent?.strategyId || "manual"} ({enumLabel(r.intent?.source, "INTENT_SOURCE_")}){" "}
                    {r.intent?.reason && <span className="text-muted">— {r.intent?.reason}</span>}
                  </p>
                  {r.decision && (
                    <div>
                      <p>
                        <Step n="2" /> Decision: <Badge tone={r.decision.approved ? "ok" : "danger"}>{r.decision.approved ? "approved" : "rejected"}</Badge>
                      </p>
                      <RiskChecks checks={r.decision.checks} />
                    </div>
                  )}
                </li>
              ))}
              {d.orders.map((o) => (
                <li key={o.orderId}>
                  <Step n="3" /> Order <span className="font-mono text-xs">{o.clientOrderId}</span> {enumLabel(o.role, "ORDER_ROLE_")} · {enumLabel(o.state, "ORDER_STATE_")}
                </li>
              ))}
              {d.fills.map((f) => (
                <li key={f.brokerExecId}>
                  <Step n="4" /> Fill {enumLabel(f.side, "SIDE_")} {dec(f.qty)} @ {dec(f.price)} · {formatNs(f.eventTime)}
                </li>
              ))}
            </ol>
          )}
        </QueryState>
        <div className="flex justify-end">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function Step({ n }: { n: string }) {
  return <span className={cn("mr-1 inline-flex size-5 items-center justify-center rounded-full bg-panel-2 text-[10px] font-semibold")}>{n}</span>;
}
