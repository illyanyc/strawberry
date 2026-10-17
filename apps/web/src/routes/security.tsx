import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError, type Me } from "@/lib/api";
import { registerPasskey } from "@/lib/webauthn";
import { formatTime } from "@/lib/utils";

interface SessionRow {
  id: string;
  auth_method: string;
  created_at: string;
  last_seen_at: string;
  ip: string;
  user_agent: string;
  current: boolean;
}

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.code === "step_up_cancelled" ? "Cancelled." : e.message;
  if (e instanceof Error && e.name === "NotAllowedError") return "The passkey prompt was cancelled.";
  return "Request failed.";
}

/** Security settings: passkeys, authenticator app, recovery codes, sessions (section 14 slice 0). */
export function SecurityPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<{ sessions: SessionRow[] }>("/auth/sessions") });
  const [error, setError] = useState<string | null>(null);
  const [passkeyName, setPasskeyName] = useState("");
  const [totp, setTotp] = useState<{ url: string; secret: string; qr: string } | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["me"] });
    void qc.invalidateQueries({ queryKey: ["sessions"] });
  };
  const onError = (e: unknown) => setError(errText(e));

  const addPasskey = useMutation({
    mutationFn: () =>
      // The begin call may trigger step-up; registerPasskey goes through api() which handles it.
      registerPasskey("/auth/webauthn/register/begin", "/auth/webauthn/register/finish", undefined, { name: passkeyName || "Passkey" }),
    onSuccess: () => {
      setPasskeyName("");
      refresh();
    },
    onError,
  });
  const deletePasskey = useMutation({ mutationFn: (id: string) => api(`/auth/webauthn/credentials/${id}`, { method: "DELETE" }), onSuccess: refresh, onError });
  const enrollTotp = useMutation({
    mutationFn: async () => {
      const r = await api<{ otpauth_url: string; secret: string }>("/auth/totp/enroll", { method: "POST" });
      return { url: r.otpauth_url, secret: r.secret, qr: await QRCode.toDataURL(r.otpauth_url, { margin: 1, width: 192 }) };
    },
    onSuccess: setTotp,
    onError,
  });
  const verifyTotp = useMutation({
    mutationFn: () => api("/auth/totp/verify", { method: "POST", body: { code: totpCode } }),
    onSuccess: () => {
      setTotp(null);
      setTotpCode("");
      refresh();
    },
    onError,
  });
  const regen = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/auth/recovery/regenerate", { method: "POST" }),
    onSuccess: (r) => {
      setCodes(r.recovery_codes);
      refresh();
    },
    onError,
  });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }), onSuccess: refresh, onError });

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Security</h1>
      {error && <Alert>{error}</Alert>}

      <Card>
        <CardHeader title="Passkeys" description="Register at least two (e.g. laptop and phone). Adding or deleting one needs a fresh check." />
        <CardBody className="space-y-4">
          {me.isPending ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : me.isError ? (
            <Alert>Could not load passkeys.</Alert>
          ) : me.data.passkeys.length === 0 ? (
            <p className="text-sm text-muted">No passkeys registered.</p>
          ) : (
            <ul className="divide-y divide-border">
              {me.data.passkeys.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3 py-3 text-sm">
                  <div>
                    <p className="font-medium">{p.name}</p>
                    <p className="text-xs text-muted">
                      Added {formatTime(p.created_at)} · last used {formatTime(p.last_used_at)}
                    </p>
                  </div>
                  <Button variant="ghost" size="sm" disabled={me.data.passkeys.length < 2} busy={deletePasskey.isPending && deletePasskey.variables === p.id} onClick={() => deletePasskey.mutate(p.id)}>
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form
            className="flex flex-col gap-3 sm:flex-row sm:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              setError(null);
              addPasskey.mutate();
            }}
          >
            <div className="flex-1">
              <Field label="New passkey name" placeholder="iPhone" value={passkeyName} onChange={(e) => setPasskeyName(e.target.value)} />
            </div>
            <Button type="submit" busy={addPasskey.isPending}>
              Add passkey
            </Button>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Authenticator app (TOTP)"
          description="Fallback sign-in when no passkey is at hand. It cannot authorize live trading."
          action={me.data && <Badge tone={me.data.totp_enabled ? "ok" : "warn"}>{me.data.totp_enabled ? "enabled" : "off"}</Badge>}
        />
        <CardBody className="space-y-4">
          {totp ? (
            <form
              className="grid gap-4 sm:grid-cols-[auto_1fr]"
              onSubmit={(e) => {
                e.preventDefault();
                verifyTotp.mutate();
              }}
            >
              <img src={totp.qr} alt="TOTP QR code" className="size-48 rounded-lg bg-white p-2" />
              <div className="space-y-3">
                <p className="text-sm text-muted">Scan the code, or enter this secret manually:</p>
                <code className="block break-all rounded-md border border-border bg-bg px-3 py-2 font-mono text-xs">{totp.secret}</code>
                <Field label="6-digit code" inputMode="numeric" maxLength={6} autoComplete="one-time-code" value={totpCode} onChange={(e) => setTotpCode(e.target.value)} />
                <Button type="submit" busy={verifyTotp.isPending} disabled={totpCode.length !== 6}>
                  Confirm
                </Button>
              </div>
            </form>
          ) : (
            <Button variant="secondary" busy={enrollTotp.isPending} onClick={() => { setError(null); enrollTotp.mutate(); }}>
              {me.data?.totp_enabled ? "Re-enroll authenticator" : "Set up authenticator"}
            </Button>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Recovery codes" description={me.data ? `${me.data.recovery_codes_left} unused code(s) left. Regenerating invalidates the old ones.` : undefined} />
        <CardBody className="space-y-4">
          {codes && (
            <ol className="grid grid-cols-2 gap-2 font-mono text-sm sm:grid-cols-5" aria-label="New recovery codes">
              {codes.map((c) => (
                <li key={c} className="rounded-md border border-border bg-bg px-2 py-1.5 text-center">
                  {c}
                </li>
              ))}
            </ol>
          )}
          <Button variant="secondary" busy={regen.isPending} onClick={() => { setError(null); regen.mutate(); }}>
            Generate new codes
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Active sessions" />
        <CardBody>
          {sessions.isPending ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : sessions.isError ? (
            <Alert>Could not load sessions.</Alert>
          ) : (
            <ul className="divide-y divide-border">
              {sessions.data.sessions.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-3 py-3 text-sm">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      {s.auth_method} {s.current && <Badge tone="ok">this device</Badge>}
                    </p>
                    <p className="truncate text-xs text-muted">
                      {s.ip} · {s.user_agent || "unknown device"} · last seen {formatTime(s.last_seen_at)}
                    </p>
                  </div>
                  {!s.current && (
                    <Button variant="ghost" size="sm" busy={revoke.isPending && revoke.variables === s.id} onClick={() => revoke.mutate(s.id)}>
                      Revoke
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
