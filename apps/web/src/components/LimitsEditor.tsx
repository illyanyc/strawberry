import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import type { Dec } from "@/lib/trading";

type LimitsJSON = Record<string, Dec | number | null | undefined>;

interface LimitsResponse {
  scope: string;
  limits: LimitsJSON;
  effective: LimitsJSON;
}

const FIELDS: { key: string; label: string; int?: boolean }[] = [
  { key: "maxOrderNotional", label: "Max order notional" },
  { key: "maxOrderQty", label: "Max order quantity" },
  { key: "maxPositionNotional", label: "Max position notional" },
  { key: "maxGrossExposure", label: "Max gross exposure" },
  { key: "maxDailyLoss", label: "Max daily loss" },
  { key: "maxOpenOrders", label: "Max open orders", int: true },
  { key: "maxOrdersPerMinute", label: "Max orders per minute", int: true },
];

const show = (v: Dec | number | null | undefined): string => {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return v === 0 ? "" : String(v);
  return v.value === "0" ? "" : v.value;
};

/**
 * Risk limit editor for one scope (RM-007). The scope's limits are replaced as a whole, so edits
 * are merged into the loaded object; empty means "not set here" (the broader scope applies). The
 * server decides whether a change loosens anything and asks for a passkey step-up if it does.
 */
export function LimitsEditor({ scope }: { scope: string }) {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["limits", scope], queryFn: () => api<LimitsResponse>(`/api/v1/risk-limits/${encodeURIComponent(scope)}`) });
  const [edits, setEdits] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => {
      const body: LimitsJSON = { ...(q.data?.limits ?? {}) };
      for (const [k, v] of Object.entries(edits)) {
        const int = FIELDS.find((f) => f.key === k)?.int;
        body[k] = v === "" ? (int ? 0 : null) : int ? Number.parseInt(v, 10) : { value: v };
      }
      for (const k of Object.keys(body)) if (body[k] === null) delete body[k];
      return api(`/api/v1/risk-limits/${encodeURIComponent(scope)}`, { method: "PUT", body });
    },
    onSuccess: () => {
      setEdits({});
      void queryClient.invalidateQueries({ queryKey: ["limits", scope] });
    },
  });
  if (q.isPending) return <p className="text-sm text-muted">Loading…</p>;
  if (q.isError) return <Alert>{q.error instanceof ApiError ? q.error.message : "Limits unavailable"}</Alert>;
  const invalid = Object.entries(edits).some(([k, v]) => v !== "" && !(FIELDS.find((f) => f.key === k)?.int ? /^\d+$/ : /^\d+(\.\d+)?$/).test(v));
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {FIELDS.map((f) => (
          <Field
            key={f.key}
            label={f.label}
            inputMode={f.int ? "numeric" : "decimal"}
            value={edits[f.key] ?? show(q.data.limits[f.key])}
            placeholder="not set"
            hint={`effective: ${show(q.data.effective[f.key]) || "unlimited"}`}
            onChange={(e) => setEdits((s) => ({ ...s, [f.key]: e.target.value.trim() }))}
          />
        ))}
      </div>
      {save.isError && <Alert>{save.error instanceof ApiError ? save.error.message : "Saving limits failed"}</Alert>}
      <Button type="submit" variant="secondary" disabled={invalid || Object.keys(edits).length === 0} busy={save.isPending}>
        Save limits
      </Button>
    </form>
  );
}
