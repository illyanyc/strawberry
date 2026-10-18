import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { RiskChecks } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError, type TradingStatus } from "@/lib/api";
import { useResolveSymbol } from "@/lib/instruments";
import { dec, type ManualTicket, type PreviewResponse, type SubmitResponse } from "@/lib/trading";

const empty: ManualTicket = { broker_account_id: "", instrument_id: "", side: "buy", quantity: "", order_type: "limit", limit_price: "", tif: "day", reason: "" };

/** Client-side shape checks only; the gateway and the risk gate decide (RM-001). */
export function ticketErrors(t: ManualTicket): string[] {
  const e: string[] = [];
  if (!t.broker_account_id) e.push("Choose an account");
  if (!t.instrument_id) e.push("Look up the symbol");
  if (!/^\d+(\.\d+)?$/.test(t.quantity) || /^0*(\.0*)?$/.test(t.quantity)) e.push("Quantity must be a positive number");
  if (t.order_type === "limit" && (!/^\d+(\.\d+)?$/.test(t.limit_price) || /^0*(\.0*)?$/.test(t.limit_price))) e.push("Limit price must be a positive number");
  return e;
}

function newIntentId(): string {
  // Crockford base32 ULID: 48-bit time + 80 random bits. Reused across retries of one ticket so a
  // repeated submit is deduplicated by execution (OE-006).
  const alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
  let t = Date.now();
  let time = "";
  for (let i = 0; i < 10; i++) {
    time = alphabet[t % 32] + time;
    t = Math.floor(t / 32);
  }
  const rnd = crypto.getRandomValues(new Uint8Array(16));
  let rand = "";
  for (let i = 0; i < 16; i++) rand += alphabet[rnd[i]! % 32];
  return time + rand;
}

/**
 * Manual order ticket (UI-015): account, instrument, side, quantity, type, limit, TIF; a pre-trade
 * preview (dry-run decision with every check, estimated price and notional, ownership note);
 * explicit confirmation. Live accounts need a passkey step-up (server-side, SE-003).
 */
export function OrderTicketPage() {
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });
  const accounts = status.data?.trading?.accounts ?? [];
  const [t, setT] = useState<ManualTicket>(empty);
  const [symbol, setSymbol] = useState("");
  const [intentId, setIntentId] = useState(newIntentId);
  const resolve = useResolveSymbol();
  const set = (patch: Partial<ManualTicket>) => {
    setT((s) => ({ ...s, ...patch }));
    preview.reset();
  };
  const preview = useMutation({ mutationFn: (ticket: ManualTicket) => api<PreviewResponse>("/api/v1/orders/preview", { method: "POST", body: ticket }) });
  const submit = useMutation({
    mutationFn: (ticket: ManualTicket) => api<SubmitResponse>("/api/v1/orders", { method: "POST", body: { ...ticket, intent_id: intentId } }),
  });
  const errors = ticketErrors(t);
  const env = accounts.find((a) => a.brokerAccountId === t.broker_account_id)?.environment;
  const live = env !== undefined && env !== "paper";

  if (submit.isSuccess) {
    const d = submit.data.decision;
    const approved = d?.approved === true;
    return (
      <div className="mx-auto max-w-3xl space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Order {approved ? "submitted" : "rejected"}</h1>
        {approved ? <Alert tone="ok">Approved by the risk gate{submit.data.duplicate ? " (already submitted earlier)" : ""}.</Alert> : <Alert>Rejected: {d?.firstFailure || "no decision returned"}</Alert>}
        <RiskChecks checks={d?.checks ?? []} />
        <div className="flex gap-2">
          <Button
            onClick={() => {
              submit.reset();
              preview.reset();
              setIntentId(newIntentId());
            }}
          >
            New order
          </Button>
          <Link to="/orders" className="inline-flex h-10 items-center rounded-lg px-4 text-sm text-muted hover:text-fg">
            Go to blotter
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Manual order</h1>
      <Alert tone="warn">Manual orders are owned by you (owner: manual). Strategies never trade them.</Alert>
      <Card>
        <CardHeader title="Ticket" action={env ? <Badge tone={live ? "live" : "paper"}>{env}</Badge> : undefined} />
        <CardBody className="space-y-4">
          <label className="block space-y-1.5">
            <span className="block text-xs font-medium text-muted">Account</span>
            <select
              className="h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm"
              value={t.broker_account_id}
              onChange={(e) => set({ broker_account_id: e.target.value })}
            >
              <option value="">Choose…</option>
              {accounts.map((a) => (
                <option key={a.brokerAccountId} value={a.brokerAccountId}>
                  {a.brokerAccountId} ({a.environment})
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Field
                label="Symbol"
                value={symbol}
                onChange={(e) => {
                  setSymbol(e.target.value.toUpperCase());
                  set({ instrument_id: "" });
                }}
                hint={t.instrument_id ? `instrument ${t.instrument_id}` : undefined}
                error={resolve.isError ? (resolve.error instanceof Error ? resolve.error.message : "lookup failed") : null}
              />
            </div>
            <Button
              variant="secondary"
              disabled={!symbol}
              busy={resolve.isPending}
              onClick={() => resolve.mutate(symbol, { onSuccess: (id) => set({ instrument_id: id }) })}
            >
              Look up
            </Button>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Select label="Side" value={t.side} options={["buy", "sell", "sell_short"]} onChange={(v) => set({ side: v as ManualTicket["side"] })} />
            <Select label="Type" value={t.order_type} options={["limit", "market"]} onChange={(v) => set({ order_type: v as ManualTicket["order_type"] })} />
            <Select label="Time in force" value={t.tif} options={["day", "gtc"]} onChange={(v) => set({ tif: v as ManualTicket["tif"] })} />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Quantity" inputMode="decimal" value={t.quantity} onChange={(e) => set({ quantity: e.target.value.trim() })} />
            {t.order_type === "limit" && <Field label="Limit price" inputMode="decimal" value={t.limit_price} onChange={(e) => set({ limit_price: e.target.value.trim() })} />}
          </div>
          {t.order_type === "market" && <p className="text-xs text-muted">Market orders are sent as marketable limits inside the price collar (OE-013).</p>}
          <Field label="Reason (optional)" value={t.reason} onChange={(e) => set({ reason: e.target.value })} maxLength={300} />
          {errors.length > 0 && t.quantity !== "" && <p className="text-xs text-warn">{errors.join(" · ")}</p>}
          <Button variant="secondary" disabled={errors.length > 0} busy={preview.isPending} onClick={() => preview.mutate(t)}>
            Preview
          </Button>
          {preview.isError && <Alert>{preview.error instanceof ApiError ? preview.error.message : "Preview failed"}</Alert>}
        </CardBody>
      </Card>
      {preview.isSuccess && (
        <Card>
          <CardHeader
            title="Pre-trade preview"
            description={preview.data.ownershipNote}
            action={<Badge tone={preview.data.decision?.approved ? "ok" : "danger"}>{preview.data.decision?.approved ? "would pass" : "would be rejected"}</Badge>}
          />
          <CardBody className="space-y-4">
            <p className="text-sm">
              Estimated price <span className="tabular-nums">{dec(preview.data.estimatedPrice)}</span> · notional{" "}
              <span className="tabular-nums">{dec(preview.data.estimatedNotional)}</span>
            </p>
            <RiskChecks checks={preview.data.decision?.checks ?? []} />
            {submit.isError && <Alert>{submit.error instanceof ApiError ? submit.error.message : "Submit failed"}</Alert>}
            <Button variant={live ? "danger" : "primary"} disabled={preview.data.decision?.approved !== true} busy={submit.isPending} onClick={() => submit.mutate(t)}>
              {live ? "Submit LIVE order" : "Submit paper order"}
            </Button>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <label className="block space-y-1.5">
      <span className="block text-xs font-medium text-muted">{label}</span>
      <select className="h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm" value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o} value={o}>
            {o.replace("_", " ")}
          </option>
        ))}
      </select>
    </label>
  );
}
