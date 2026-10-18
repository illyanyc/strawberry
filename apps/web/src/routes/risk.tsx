import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { EngageKillDialog, type EngageTarget } from "@/components/KillSwitch";
import { LimitsEditor } from "@/components/LimitsEditor";
import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError, type TradingStatus } from "@/lib/api";
import { enumLabel, type KillSwitch, type KillSwitchesResponse } from "@/lib/trading";
import { formatNs } from "@/lib/utils";

/**
 * Risk (UI-013, UI-006): latched kill switches with release (fresh passkey from a desktop,
 * RM-007), scoped engagement, active halts, and the global and manual limit scopes.
 */
export function RiskPage() {
  const [all, setAll] = useState(false);
  const kills = useQuery({
    queryKey: ["kill-switches", all],
    queryFn: () => api<KillSwitchesResponse>(`/api/v1/kill-switches${all ? "?all=true" : ""}`),
    refetchInterval: 5_000,
  });
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status"), refetchInterval: 5_000 });
  const [releasing, setReleasing] = useState<KillSwitch | null>(null);
  const [engage, setEngage] = useState<EngageTarget | null>(null);
  const [scope, setScope] = useState<EngageTarget["scope"]>("account");
  const [target, setTarget] = useState("");
  const halts = status.data?.trading?.activeHalts ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Risk &amp; kill switches</h1>
      {status.data && !status.data.trading && <Alert tone="warn">Execution is unreachable: kill-switch and halt state is unknown.</Alert>}
      <Card>
        <CardHeader
          title="Kill switches"
          description="Engaging needs only a confirmation. Releasing needs a fresh passkey from a desktop."
          action={
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Show released
            </label>
          }
        />
        <CardBody>
          <QueryState q={kills} empty={all ? "No kill switches recorded." : "No kill switch is engaged."} isEmpty={(d) => d.killSwitches.length === 0}>
            {(d) => (
              <ul className="space-y-3" aria-label="Kill switches">
                {d.killSwitches.map((k) => (
                  <li key={k.eventId} className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border p-3 text-sm">
                    <div className="space-y-1">
                      <p className="flex items-center gap-2 font-medium">
                        <Badge tone={k.latched ? "danger" : "neutral"}>{k.latched ? "engaged" : "released"}</Badge>
                        {enumLabel(k.scope, "KILL_SWITCH_SCOPE_")}
                        {k.target && <span className="font-mono text-xs">{k.target}</span>} · {enumLabel(k.action, "KILL_SWITCH_ACTION_")}
                      </p>
                      <p className="text-muted">{k.reason}</p>
                      <p className="text-xs text-muted">
                        by {k.engagedBy} at {formatNs(k.engagedAt)}
                        {!k.latched && ` · released by ${k.releasedBy} at ${formatNs(k.releasedAt)}`}
                      </p>
                    </div>
                    {k.latched && (
                      <Button size="sm" variant="secondary" onClick={() => setReleasing(k)}>
                        Release…
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
          <form
            className="mt-4 flex flex-wrap items-end gap-2 border-t border-border pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              setEngage({ scope, target, label: `${scope} ${target}` });
            }}
          >
            <label className="block space-y-1.5">
              <span className="block text-xs font-medium text-muted">Scope</span>
              <select className="h-10 rounded-lg border border-border bg-bg px-3 text-sm" value={scope} onChange={(e) => setScope(e.target.value as EngageTarget["scope"])}>
                <option value="account">account</option>
                <option value="strategy">strategy</option>
                <option value="instrument">instrument</option>
              </select>
            </label>
            <div className="w-56">
              <Field label="Target ID" value={target} onChange={(e) => setTarget(e.target.value.trim())} />
            </div>
            <Button type="submit" variant="danger" disabled={!/^[A-Za-z0-9_-]{1,64}$/.test(target)}>
              Engage scoped switch…
            </Button>
          </form>
        </CardBody>
      </Card>
      {engage && <EngageKillDialog open onClose={() => setEngage(null)} target={engage} />}
      {releasing && <ReleaseDialog kill={releasing} onClose={() => setReleasing(null)} />}
      <Card>
        <CardHeader title="Active halts" description="Automatic halts (unknown order state, breaks, margin events, dead-man) block new intents until cleared." />
        <CardBody>
          {halts.length === 0 ? (
            <p className="text-sm text-muted">No active halts.</p>
          ) : (
            <ul className="space-y-2 text-sm">
              {halts.map((h) => (
                <li key={h.haltId} className="rounded-lg border border-border p-2">
                  <span className="font-medium">{h.strategyId || "all strategies"}</span> · {h.brokerAccountId} {h.instrumentId && `· ${h.instrumentId}`} —{" "}
                  <span className="text-muted">{h.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Global limits" description="Apply to every strategy and manual order; narrower scopes can only be tighter." />
        <CardBody>
          <LimitsEditor scope="global" />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Manual order limits" />
        <CardBody>
          <LimitsEditor scope="manual" />
        </CardBody>
      </Card>
    </div>
  );
}

function ReleaseDialog({ kill, onClose }: { kill: KillSwitch; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const release = useMutation({
    mutationFn: () => api(`/api/v1/kill-switches/${encodeURIComponent(kill.eventId)}/release`, { method: "POST", body: { reason } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["kill-switches"] });
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      onClose();
    },
  });
  return (
    <Dialog open onClose={onClose} title="Release kill switch">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          Releasing lets new orders through for {enumLabel(kill.scope, "KILL_SWITCH_SCOPE_")} {kill.target}. Strategies that were demoted stay demoted.
        </p>
        <Field label="Release reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
        {release.isError && <Alert>{release.error instanceof ApiError ? release.error.message : "Release failed"}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button busy={release.isPending} disabled={reason.trim() === ""} onClick={() => release.mutate()}>
            Release
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
