import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import { registerPasskey, assertPasskey } from "@/lib/webauthn";

function message(e: unknown): string {
  if (e instanceof ApiError) return e.status === 429 ? "Too many attempts. Try again later." : e.message;
  if (e instanceof Error && e.name === "NotAllowedError") return "The passkey prompt was cancelled.";
  return "Something went wrong. Try again.";
}

function AuthFrame({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-[radial-gradient(ellipse_at_top,var(--color-panel-2),transparent_60%)] p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center text-center">
          <BrandMark className="size-12" />
          <h1 className="mt-3 text-xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-1 text-sm text-muted">{subtitle}</p>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Login: passkey first; TOTP and recovery codes as reduced-assurance fallbacks (SE-014). */
export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"passkey" | "totp" | "recovery">("passkey");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      queryClient.clear();
      await navigate({ to: "/" });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame title="Sign in" subtitle="Use the passkey on this device or your phone.">
      <Card>
        <CardBody className="space-y-4">
          {error && <Alert>{error}</Alert>}
          {mode === "passkey" ? (
            <Button size="lg" className="w-full" busy={busy} onClick={() => run(() => assertPasskey("/auth/webauthn/login/begin", "/auth/webauthn/login/finish"))}>
              Sign in with passkey
            </Button>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void run(() => api(mode === "totp" ? "/auth/totp/login" : "/auth/recovery", { method: "POST", body: { code }, allow401: true }));
              }}
            >
              <Field
                label={mode === "totp" ? "Authenticator code" : "Recovery code"}
                inputMode={mode === "totp" ? "numeric" : "text"}
                autoComplete="one-time-code"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                hint="Fallback sign-in has reduced assurance: register a new passkey to unlock dangerous actions."
              />
              <Button type="submit" className="w-full" busy={busy} disabled={code.length < 6}>
                Sign in
              </Button>
            </form>
          )}
          <div className="flex justify-center gap-4 text-xs">
            {(["passkey", "totp", "recovery"] as const)
              .filter((m) => m !== mode)
              .map((m) => (
                <button key={m} type="button" className="text-muted underline-offset-4 hover:text-fg hover:underline" onClick={() => { setMode(m); setCode(""); setError(null); }}>
                  {m === "passkey" ? "Use a passkey" : m === "totp" ? "Use an authenticator code" : "Use a recovery code"}
                </button>
              ))}
          </div>
        </CardBody>
      </Card>
    </AuthFrame>
  );
}

/** First-run setup (SE-007): setup token from `make setup-token`, first passkey, recovery codes. */
export function SetupPage() {
  const navigate = useNavigate();
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [device, setDevice] = useState("This device");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = (await registerPasskey("/auth/setup/begin", "/auth/setup/finish", { setup_token: token.trim(), display_name: name }, {
        setup_token: token.trim(),
        display_name: name,
        passkey_name: device,
      })) as { recovery_codes: string[] };
      setCodes(res.recovery_codes);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  if (codes) {
    return (
      <AuthFrame title="Save your recovery codes" subtitle="Each code works once. They are shown only now.">
        <Card>
          <CardBody className="space-y-4">
            <ol className="grid grid-cols-2 gap-2 font-mono text-sm" aria-label="Recovery codes">
              {codes.map((c) => (
                <li key={c} className="rounded-md border border-border bg-bg px-3 py-2 text-center">
                  {c}
                </li>
              ))}
            </ol>
            <Alert tone="warn">Store them offline (password manager or paper). Next, add a second passkey and an authenticator app under Security.</Alert>
            <Button className="w-full" onClick={() => navigate({ to: "/settings/security" })}>
              I saved them — continue
            </Button>
          </CardBody>
        </Card>
      </AuthFrame>
    );
  }

  return (
    <AuthFrame title="Set up Strwbrry" subtitle="Create the owner account with a passkey.">
      <Card>
        <CardHeader title="First-run setup" description={<>Run <code className="font-mono">make setup-token</code> on the host to get a one-time token.</>} />
        <CardBody>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            {error && <Alert>{error}</Alert>}
            <Field label="Setup token" autoComplete="off" spellCheck={false} value={token} onChange={(e) => setToken(e.target.value)} required />
            <Field label="Your name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
            <Field label="Name for this passkey" value={device} onChange={(e) => setDevice(e.target.value)} hint="e.g. MacBook, iPhone" />
            <Button type="submit" size="lg" className="w-full" busy={busy} disabled={token.trim().length < 16}>
              Create passkey
            </Button>
          </form>
        </CardBody>
      </Card>
    </AuthFrame>
  );
}
