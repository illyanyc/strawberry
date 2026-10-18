import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Alert, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import type { KillSwitch, TradingCore } from "@/lib/trading";

export type KillAction = "cancel_open" | "freeze" | "flatten";

const actionText: Record<KillAction, string> = {
  cancel_open: "Freeze new orders and cancel open orders (protective stops stay)",
  freeze: "Freeze new orders only",
  flatten: "Freeze, cancel, and close strategy-owned positions (manual holdings are never touched)",
};

export interface EngageTarget {
  scope: "global" | "account" | "strategy" | "instrument";
  target?: string;
  label: string;
}

/** Engage dialog shared by the global button and per-strategy/per-account buttons (RM-006, UI-006). */
export function EngageKillDialog({ open, onClose, target }: { open: boolean; onClose: () => void; target: EngageTarget }) {
  const queryClient = useQueryClient();
  const [action, setAction] = useState<KillAction>("cancel_open");
  const [reason, setReason] = useState("");
  const engage = useMutation({
    mutationFn: () =>
      api<{ kill_switch: KillSwitch }>("/api/v1/kill-switches", {
        method: "POST",
        body: { scope: target.scope, target: target.target ?? "", action, reason },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["status"] });
      void queryClient.invalidateQueries({ queryKey: ["kill-switches"] });
      void queryClient.invalidateQueries({ queryKey: ["trading-status"] });
      setReason("");
      onClose();
    },
  });
  return (
    <Dialog open={open} onClose={onClose} title={`Engage kill switch: ${target.label}`}>
      <div className="space-y-4">
        <p className="text-sm text-muted">The switch latches until you release it with a passkey from a desktop.</p>
        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-muted">Action</legend>
          {(Object.keys(actionText) as KillAction[]).map((a) => (
            <label key={a} className="flex items-start gap-2 text-sm">
              <input type="radio" name="kill-action" value={a} checked={action === a} onChange={() => setAction(a)} className="mt-1" />
              <span>{actionText[a]}</span>
            </label>
          ))}
        </fieldset>
        <Field label="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />
        {engage.isError && <Alert>{engage.error instanceof ApiError ? engage.error.message : "Kill switch request failed"}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" busy={engage.isPending} onClick={() => engage.mutate()}>
            Engage kill switch
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

/**
 * The global kill switch in the status bar (UI-006): one tap opens the confirmation, the second
 * engages. While any global switch is latched the button shows FROZEN and links to the Risk page,
 * where release asks for a passkey step-up. Unknown execution state never shows "running".
 */
export function GlobalKillSwitch({ trading }: { trading: TradingCore | null | undefined }) {
  const [open, setOpen] = useState(false);
  const active = trading?.activeKillSwitches ?? [];
  const global = active.filter((k) => k.scope === "KILL_SWITCH_SCOPE_GLOBAL");
  if (global.length > 0) {
    return (
      <Link
        to="/risk"
        className="inline-flex h-8 items-center gap-1 rounded-lg bg-live px-3 text-xs font-semibold uppercase tracking-wide text-white"
        aria-label={`Trading frozen by ${global.length} global kill switch${global.length > 1 ? "es" : ""}; open Risk to review or release`}
      >
        Frozen
      </Link>
    );
  }
  return (
    <>
      <Button variant="danger" size="sm" onClick={() => setOpen(true)} aria-label="Global kill switch" title={trading ? undefined : "Execution unreachable"}>
        Kill switch
      </Button>
      <EngageKillDialog open={open} onClose={() => setOpen(false)} target={{ scope: "global", label: "all trading" }} />
    </>
  );
}
