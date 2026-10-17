import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Field } from "@/components/ui/form";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { api, ApiError, setStepUpHandler, type StepUpMethod } from "@/lib/api";
import { assertPasskey } from "@/lib/webauthn";

interface Pending {
  method: StepUpMethod;
  action: string;
  resolve: (ok: boolean) => void;
}

/**
 * Global step-up dialog (SE-003). The API client calls it on `step_up_required`; the server decides
 * which factor is acceptable (`passkey` for money/safety actions, `any` for account recovery).
 */
export function StepUpDialog() {
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const pendingRef = useRef<Pending | null>(null);

  const request = useCallback(
    (method: StepUpMethod, action: string) =>
      new Promise<boolean>((resolve) => {
        const p = { method, action, resolve };
        pendingRef.current = p;
        setError(null);
        setCode("");
        setPending(p);
      }),
    [],
  );

  useEffect(() => {
    setStepUpHandler(request);
    return () => setStepUpHandler(null);
  }, [request]);

  const finish = (ok: boolean) => {
    pendingRef.current?.resolve(ok);
    pendingRef.current = null;
    setPending(null);
  };

  const withPasskey = async () => {
    setBusy(true);
    setError(null);
    try {
      await assertPasskey("/auth/step-up/begin", "/auth/step-up/finish");
      finish(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Passkey prompt was cancelled or failed.");
    } finally {
      setBusy(false);
    }
  };

  const withTOTP = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/auth/step-up/totp", { method: "POST", body: { code }, noStepUp: true });
      finish(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Code rejected.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={pending !== null} onClose={() => finish(false)} title="Confirm it's you">
      <div className="space-y-4">
        <p className="text-sm text-muted">
          This action needs a fresh {pending?.method === "passkey" ? "passkey" : "second-factor"} check
          {pending?.action ? (
            <>
              {" "}
              (<code className="font-mono text-xs text-fg">{pending.action}</code>)
            </>
          ) : null}
          . It stays valid for a few minutes on this session.
        </p>
        {error && <Alert>{error}</Alert>}
        <Button className="w-full" onClick={withPasskey} busy={busy} autoFocus>
          Use passkey
        </Button>
        {pending?.method === "any" && (
          <form
            className="space-y-3 border-t border-border pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              void withTOTP();
            }}
          >
            <Field label="Or enter an authenticator code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />
            <Button type="submit" variant="secondary" className="w-full" disabled={code.length !== 6 || busy}>
              Verify code
            </Button>
          </form>
        )}
        <Button variant="ghost" className="w-full" onClick={() => finish(false)}>
          Cancel
        </Button>
      </div>
    </Dialog>
  );
}
