import { useMutation, useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/utils";

interface AuditEvent {
  seq: number;
  occurred_at: number;
  actor: string;
  action: string;
  target: string;
  ip: string;
  details_json: string;
  hash: string;
}

interface VerifyResult {
  ok: boolean;
  count: number;
  head_seq: number;
  head_hash: string;
  broken_at: number;
  reason: string;
}

/** Audit log viewer with on-demand hash-chain verification (SE-022). */
export function AuditPage() {
  const events = useQuery({ queryKey: ["audit"], queryFn: () => api<{ events: AuditEvent[] }>("/api/v1/audit?limit=200") });
  const verify = useMutation({ mutationFn: () => api<VerifyResult>("/api/v1/audit/verify") });

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
      <Card>
        <CardHeader
          title="Hash chain"
          description="Every event's hash covers the previous one. Verification recomputes the whole chain."
          action={
            <Button variant="secondary" size="sm" busy={verify.isPending} onClick={() => verify.mutate()}>
              Verify chain
            </Button>
          }
        />
        {verify.data && (
          <CardBody>
            {verify.data.ok ? (
              <Alert tone="ok">
                Intact: {verify.data.count} events, head #{verify.data.head_seq} <code className="font-mono text-xs">{verify.data.head_hash.slice(0, 16)}…</code>
              </Alert>
            ) : (
              <Alert>
                Broken at #{verify.data.broken_at}: {verify.data.reason}
              </Alert>
            )}
          </CardBody>
        )}
      </Card>
      <Card>
        <CardBody className="overflow-x-auto p-0">
          {events.isPending ? (
            <p className="p-5 text-sm text-muted">Loading…</p>
          ) : events.isError ? (
            <div className="p-5">
              <Alert>Could not load the audit log.</Alert>
            </div>
          ) : events.data.events.length === 0 ? (
            <p className="p-5 text-sm text-muted">No events yet.</p>
          ) : (
            <table className="data-table text-xs">
              <thead className="border-b border-border text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium">#</th>
                  <th className="px-4 py-2 font-medium">Time</th>
                  <th className="px-4 py-2 font-medium">Actor</th>
                  <th className="px-4 py-2 font-medium">Action</th>
                  <th className="px-4 py-2 font-medium">Target</th>
                  <th className="hidden px-4 py-2 font-medium lg:table-cell">Details</th>
                  <th className="hidden px-4 py-2 font-medium md:table-cell">Hash</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {events.data.events.map((e) => (
                  <tr key={e.seq} className="align-top">
                    <td className="px-4 py-2 font-mono text-muted">{e.seq}</td>
                    <td className="whitespace-nowrap px-4 py-2">{formatTime(e.occurred_at)}</td>
                    <td className="px-4 py-2">{e.actor}</td>
                    <td className="px-4 py-2">
                      <Badge tone={e.action.includes("failure") || e.action.includes("lockout") ? "danger" : "neutral"}>{e.action}</Badge>
                    </td>
                    <td className="px-4 py-2 font-mono">{e.target}</td>
                    <td className="hidden max-w-xs truncate px-4 py-2 font-mono text-muted lg:table-cell" title={e.details_json}>
                      {e.details_json}
                    </td>
                    <td className="hidden px-4 py-2 font-mono text-muted md:table-cell">{e.hash.slice(0, 12)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
