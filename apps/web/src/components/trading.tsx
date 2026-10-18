import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { Alert, Badge } from "@/components/ui/form";
import { ApiError } from "@/lib/api";
import { dec, decSign, enumLabel, type Dec, type InstrumentBreakdown, type RiskCheck } from "@/lib/trading";
import { cn } from "@/lib/utils";

/**
 * A server decimal with its sign shown as text and an arrow, never by color alone (section 14
 * accessibility). The string is displayed as the service computed it (UI-008).
 */
export function Signed({ d, className }: { d: Dec | null | undefined; className?: string }) {
  const s = decSign(d);
  const v = dec(d);
  const text = s > 0 && !v.startsWith("+") ? `+${v}` : v;
  return (
    <span className={cn("tabular-nums", s > 0 && "text-ok", s < 0 && "text-live", className)}>
      <span aria-hidden>{s > 0 ? "▲ " : s < 0 ? "▼ " : ""}</span>
      {text}
    </span>
  );
}

export function Num({ d }: { d: Dec | null | undefined }) {
  return <span className="tabular-nums">{dec(d)}</span>;
}

export function ModeBadge({ mode }: { mode: string }) {
  const m = enumLabel(mode, "STRATEGY_MODE_");
  const tone = m === "live" ? "live" : m === "paper" ? "paper" : m === "halted" ? "danger" : "neutral";
  return <Badge tone={tone}>{m}</Badge>;
}

/** Loading, error, and empty states for every data view (UI-007). */
export function QueryState<T>({
  q,
  empty,
  isEmpty,
  children,
}: {
  q: UseQueryResult<T>;
  empty: string;
  isEmpty?: (d: T) => boolean;
  children: (d: T) => ReactNode;
}) {
  if (q.isPending) return <p className="text-sm text-muted">Loading…</p>;
  if (q.isError) {
    const msg = q.error instanceof ApiError ? q.error.message : "Request failed";
    return <Alert>{msg}</Alert>;
  }
  if (isEmpty?.(q.data)) return <p className="text-sm text-muted">{empty}</p>;
  return <>{children(q.data)}</>;
}

/** Every pre-trade check with its value and limit; failures first (RM-003: nothing hidden). */
export function RiskChecks({ checks }: { checks: RiskCheck[] }) {
  const sorted = [...checks].sort((a, b) => Number(a.passed) - Number(b.passed));
  return (
    <div className="overflow-x-auto">
      <table className="data-table text-xs">
        <thead className="text-muted">
          <tr>
            <th className="py-1 pr-3 font-medium">Check</th>
            <th className="py-1 pr-3 font-medium">Result</th>
            <th className="py-1 pr-3 font-medium">Value</th>
            <th className="py-1 pr-3 font-medium">Limit</th>
            <th className="py-1 font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={c.name} className="border-t border-border">
              <td className="py-1 pr-3 font-mono">{c.name}</td>
              <td className="py-1 pr-3">
                <Badge tone={c.passed ? "ok" : "danger"}>{c.passed ? "pass" : "fail"}</Badge>
              </td>
              <td className="py-1 pr-3 tabular-nums">{c.value || "—"}</td>
              <td className="py-1 pr-3 tabular-nums">{c.limit || "—"}</td>
              <td className="py-1 text-muted">{c.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Real positions per instrument split by owner (VP-001, N3): broker quantity, the ledger total,
 * the manual bucket, and each strategy. Values come from the ledger; the UI adds nothing up.
 */
export function OwnershipTable({ instruments, strategies }: { instruments: InstrumentBreakdown[]; strategies: string[] }) {
  if (instruments.length === 0) return <p className="text-sm text-muted">No positions in this account.</p>;
  const qty = (row: InstrumentBreakdown, owner: string) => row.owners.find((o) => o.owner === owner)?.qty;
  return (
    <div className="overflow-x-auto">
      <table className="data-table" aria-label="Positions by owner">
        <thead className="text-xs text-muted">
          <tr>
            <th className="py-2 pr-4 font-medium">Symbol</th>
            <th className="py-2 pr-4 font-medium">Broker</th>
            <th className="py-2 pr-4 font-medium">Ledger</th>
            <th className="py-2 pr-4 font-medium">Manual</th>
            {strategies.map((s) => (
              <th key={s} className="py-2 pr-4 font-medium">
                {s}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {instruments.map((row) => {
            const mismatch = dec(row.brokerQty) !== dec(row.ledgerQty);
            return (
              <tr key={row.instrumentId} className="border-t border-border" data-instrument={row.instrumentId}>
                <td className="py-2 pr-4 font-medium" title={row.instrumentId}>
                  {row.symbol || row.instrumentId}
                </td>
                <td className="py-2 pr-4">
                  <Num d={row.brokerQty} />
                </td>
                <td className={cn("py-2 pr-4", mismatch && "text-warn")}>
                  <Num d={row.ledgerQty} />
                  {mismatch && <span className="ml-1 text-xs">(differs)</span>}
                </td>
                <td className="py-2 pr-4" data-owner="manual">
                  <Num d={qty(row, "manual")} />
                </td>
                {strategies.map((s) => (
                  <td key={s} className="py-2 pr-4" data-owner={s}>
                    <Num d={qty(row, s)} />
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
