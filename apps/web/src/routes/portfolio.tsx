import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import { Num, OwnershipTable, QueryState, Signed } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import { dec, enumLabel, type AccountsResponse, type AccountView, type LedgerBreak } from "@/lib/trading";
import { formatNs } from "@/lib/utils";

export function useAccounts() {
  return useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountsResponse>("/api/v1/accounts"), refetchInterval: 15_000 });
}

/** Portfolio (UI-016): per account, real positions split by owner, cash buckets, reconciliation, breaks. */
export function PortfolioPage() {
  const accounts = useAccounts();
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Portfolio</h1>
      <QueryState q={accounts} empty="No broker accounts have reported a snapshot yet." isEmpty={(d) => d.accounts.length === 0}>
        {(d) => {
          const acct = d.accounts.find((a) => a.brokerAccountId === selected) ?? d.accounts[0]!;
          return (
            <>
              {d.accounts.length > 1 && (
                <div role="tablist" aria-label="Broker accounts" className="flex flex-wrap gap-2">
                  {d.accounts.map((a) => (
                    <Button
                      key={a.brokerAccountId}
                      role="tab"
                      aria-selected={a.brokerAccountId === acct.brokerAccountId}
                      variant={a.brokerAccountId === acct.brokerAccountId ? "secondary" : "ghost"}
                      size="sm"
                      onClick={() => setSelected(a.brokerAccountId)}
                    >
                      {a.brokerAccountId}
                    </Button>
                  ))}
                </div>
              )}
              <AccountPanel view={acct} />
            </>
          );
        }}
      </QueryState>
    </div>
  );
}

function AccountPanel({ view }: { view: AccountView }) {
  const strategies = view.owners.map((o) => o.owner).filter((o) => o !== "manual");
  const recon = enumLabel(view.reconStatus, "RECONCILIATION_STATUS_");
  const snap = view.brokerSnapshot;
  const env = enumLabel(snap?.mode, "MODE_");
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Equity (broker)" value={<Num d={snap?.equity} />} />
        <Stat label="Cash (broker)" value={<Num d={snap?.cash} />} />
        <Stat label="Unallocated cash" value={<Num d={view.unallocatedCash} />} />
        <Stat label="Reserve" value={<Num d={view.reserve} />} />
      </div>
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              {view.brokerAccountId} <Badge tone={env === "live" ? "live" : "paper"}>{env}</Badge>
            </span>
          }
          description={`Snapshot ${formatNs(snap?.asOf)} · reconciliation ${recon} as of ${formatNs(view.reconAsOf)}`}
          action={<Badge tone={recon === "ok" ? "ok" : "warn"}>recon {recon}</Badge>}
        />
        <CardBody>
          <OwnershipTable instruments={view.instruments} strategies={strategies} />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Owners" description="Virtual portfolios inside this account; manual is everything not placed by a strategy." />
        <CardBody className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-2 pr-4 font-medium">Owner</th>
                <th className="py-2 pr-4 font-medium">Allocation</th>
                <th className="py-2 pr-4 font-medium">Cash available</th>
                <th className="py-2 pr-4 font-medium">Reserved</th>
                <th className="py-2 pr-4 font-medium">Equity</th>
                <th className="py-2 pr-4 font-medium">Day P&amp;L</th>
                <th className="py-2 pr-4 font-medium">Unrealized</th>
              </tr>
            </thead>
            <tbody>
              {[...(view.manual ? [view.manual] : []), ...view.owners.filter((o) => o.owner !== "manual")].map((o) => (
                <tr key={o.owner} className="border-t border-border">
                  <td className="py-2 pr-4 font-medium">{o.owner}</td>
                  <td className="py-2 pr-4">{o.owner === "manual" ? "—" : <Num d={o.allocation} />}</td>
                  <td className="py-2 pr-4">
                    <Num d={o.cashAvailable} />
                  </td>
                  <td className="py-2 pr-4">
                    <Num d={o.reserved} />
                  </td>
                  <td className="py-2 pr-4">
                    <Num d={o.equity} />
                  </td>
                  <td className="py-2 pr-4">
                    <Signed d={o.dayPnl} />
                  </td>
                  <td className="py-2 pr-4">
                    <Signed d={o.unrealizedPnl} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>
      <BreaksCard view={view} />
      <ShadowLots view={view} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Card>
      <CardBody>
        <p className="text-xs text-muted">{label}</p>
        <p className="mt-1 text-lg font-semibold">{value}</p>
      </CardBody>
    </Card>
  );
}

function BreaksCard({ view }: { view: AccountView }) {
  const [resolving, setResolving] = useState<LedgerBreak | null>(null);
  return (
    <Card>
      <CardHeader title="Reconciliation breaks" description="Open differences between the broker and the ledgers. Resolution needs a passkey." />
      <CardBody>
        {view.openBreaks.length === 0 ? (
          <p className="text-sm text-muted">No open breaks.</p>
        ) : (
          <ul className="space-y-3">
            {view.openBreaks.map((b) => (
              <li key={b.breakId} className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border p-3 text-sm">
                <div className="space-y-1">
                  <p className="flex items-center gap-2 font-medium">
                    <Badge tone={b.severity === "critical" ? "danger" : "warn"}>{b.severity || "warning"}</Badge>
                    {enumLabel(b.type, "BREAK_TYPE_")} · {b.symbol || b.instrumentId || "cash"}
                  </p>
                  <p className="text-muted">{b.detail}</p>
                  <p className="text-xs text-muted">
                    Broker {dec(b.brokerQty)} vs ledger {dec(b.ledgerQty)} · halted: {b.affectedOwners.join(", ") || "none"} · detected {formatNs(b.detectedAt)}
                  </p>
                </div>
                <Button size="sm" variant="secondary" onClick={() => setResolving(b)}>
                  Resolve…
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
      {resolving && <ResolveDialog brk={resolving} account={view.brokerAccountId} onClose={() => setResolving(null)} />}
    </Card>
  );
}

const resolutionText: Record<string, string> = {
  accept: "Accept the broker's numbers (no quantity moves)",
  transfer_to_manual: "Transfer quantity from a strategy to manual (moves lots with cost basis)",
  book_to_manual: "Book the broker difference to manual at the mark",
  rebaseline_cash: "Re-baseline ledger cash to the broker",
};

function ResolveDialog({ brk, account, onClose }: { brk: LedgerBreak; account: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const options = brk.suggestedResolutions.length ? brk.suggestedResolutions : ["accept"];
  const [resolution, setResolution] = useState(options[0]!);
  const [fromOwner, setFromOwner] = useState(brk.affectedOwners[0] ?? "");
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");
  const resolve = useMutation({
    mutationFn: () =>
      api(`/api/v1/accounts/${encodeURIComponent(account)}/breaks/${encodeURIComponent(brk.breakId)}/resolve`, {
        method: "POST",
        body: { resolution, from_owner: resolution === "transfer_to_manual" ? fromOwner : "", qty: resolution === "transfer_to_manual" ? qty : "", note },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
  });
  return (
    <Dialog open onClose={onClose} title={`Resolve break: ${brk.symbol || brk.instrumentId || "cash"}`}>
      <div className="space-y-4">
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-muted">Resolution</legend>
          {options.map((o) => (
            <label key={o} className="flex items-start gap-2 text-sm">
              <input type="radio" name="resolution" value={o} checked={resolution === o} onChange={() => setResolution(o)} className="mt-1" />
              <span>{resolutionText[o] ?? o}</span>
            </label>
          ))}
        </fieldset>
        {resolution === "transfer_to_manual" && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="From strategy" value={fromOwner} onChange={(e) => setFromOwner(e.target.value)} />
            <Field label="Quantity" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
        )}
        <Field label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        {resolve.isError && <Alert>{resolve.error instanceof ApiError ? resolve.error.message : "Resolution failed"}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={resolve.isPending} onClick={() => resolve.mutate()}>
            Resolve break
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

/** The broker-side FIFO lot ledger across all owners (VP-021), for tax-lot comparison. */
function ShadowLots({ view }: { view: AccountView }) {
  const rows = view.instruments.filter((i) => i.brokerLots.length > 0);
  return (
    <Card>
      <CardHeader title="Broker lots (shadow ledger)" description="The broker's FIFO tax lots across all owners; strategy lots are in each virtual portfolio." />
      <CardBody>
        {rows.length === 0 ? (
          <p className="text-sm text-muted">No open broker lots.</p>
        ) : (
          <details>
            <summary className="cursor-pointer text-sm text-muted">Show {rows.reduce((n, r) => n + r.brokerLots.length, 0)} lots</summary>
            <table className="mt-3 w-full text-left text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 pr-3 font-medium">Symbol</th>
                  <th className="py-1 pr-3 font-medium">Quantity</th>
                  <th className="py-1 pr-3 font-medium">Cost</th>
                  <th className="py-1 font-medium">Opened</th>
                </tr>
              </thead>
              <tbody>
                {rows.flatMap((r) =>
                  r.brokerLots.map((l) => (
                    <tr key={l.lotId} className="border-t border-border">
                      <td className="py-1 pr-3">{r.symbol || r.instrumentId}</td>
                      <td className="py-1 pr-3 tabular-nums">{dec(l.qty)}</td>
                      <td className="py-1 pr-3 tabular-nums">{dec(l.cost)}</td>
                      <td className="py-1">{formatNs(l.openedAt)}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </details>
        )}
      </CardBody>
    </Card>
  );
}
