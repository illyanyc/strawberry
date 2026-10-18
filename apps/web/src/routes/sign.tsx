import { Link } from "@tanstack/react-router";
import { useState, type FormEvent, type ReactNode } from "react";

import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Alert, Field } from "@/components/ui/form";
import { ClerkFrame } from "@/lib/clerk-live";
import { clearMockAccount, clerkConfigured, readMockAccount, writeMockAccount } from "@/lib/mock-account";

function Frame({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center p-4">
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

/** Hosted sign-up. Clerk when a publishable key is set; otherwise a local mock that does not charge or trade (ADR-0055). */
export function SignUpPage() {
  if (clerkConfigured()) {
    return (
      <Frame title="Create a Strwbrry account" subtitle="Email verification and an authenticator app finish signup.">
        <ClerkFrame>{(mod) => <mod.SignUp />}</ClerkFrame>
      </Frame>
    );
  }
  return <MockSignUp />;
}

export function SignInPage() {
  if (clerkConfigured()) {
    return (
      <Frame title="Sign in to Strwbrry" subtitle="The trading app still requires its own session on this deployment.">
        <ClerkFrame>{(mod) => <mod.SignIn />}</ClerkFrame>
      </Frame>
    );
  }
  return <MockSignIn />;
}

function MockSignUp() {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [disclosure, setDisclosure] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      setError("Enter an email address.");
      return;
    }
    if (!disclosure) {
      setError("Accept the risk disclosure before the account exists.");
      return;
    }
    if (!/^\d{6}$/.test(code)) {
      setError("Enter the 6-digit authenticator code. In this local mock any 6 digits stand in for the app.");
      return;
    }
    writeMockAccount({ email, plan: "free", mfa: true, disclosure: true, trial: false });
    setError(null);
    setDone(true);
  };

  return (
    <Frame title="Create a free account" subtitle="Clerk is not configured. This mock stays in this browser. It does not charge a card and it does not sign you into the trading session.">
      <Alert tone="warn">Local mock. No card is collected. Live trading stays off. The passkey login at /login is still the trading session on this install.</Alert>
      {done ? (
        <Card>
          <CardBody className="space-y-3 text-sm">
            <p>Account {readMockAccount()?.email} is on the Free plan in this browser. Enroll MFA was recorded for the mock only.</p>
            <p>
              <Link to="/login" className="underline">
                Sign in with the trading passkey
              </Link>{" "}
              to open the dashboard, then finish onboarding.
            </p>
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardBody>
            <form className="space-y-4" onSubmit={submit}>
              {error && <Alert>{error}</Alert>}
              <Field label="Email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              <label className="flex items-start gap-2 text-sm text-muted">
                <input type="checkbox" className="mt-1" checked={disclosure} onChange={(e) => setDisclosure(e.target.checked)} />
                <span>I have read that trading can lose money and that paper results are not live results.</span>
              </label>
              <Field label="Authenticator code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
              <Button type="submit">Create free account</Button>
            </form>
          </CardBody>
        </Card>
      )}
    </Frame>
  );
}

function MockSignIn() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const existing = readMockAccount();

  return (
    <Frame title="Sign in" subtitle="This mock checks the account stored in this browser. It is not the trading session.">
      {!existing ? (
        <Alert>No local mock account yet. Create one first. The trading app still uses the passkey at login.</Alert>
      ) : null}
      {ready ? (
        <Card>
          <CardBody className="space-y-3 text-sm">
            <p>Signed in to the mock as {existing?.email}. Plan: {existing?.plan}. This did not open a broker session.</p>
            <Button variant="secondary" onClick={() => { clearMockAccount(); setReady(false); }}>
              Sign out of mock
            </Button>
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardBody>
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                if (!existing || existing.email.toLowerCase() !== email.trim().toLowerCase()) {
                  setError("No local mock account uses that email.");
                  return;
                }
                setError(null);
                setReady(true);
              }}
            >
              {error && <Alert>{error}</Alert>}
              <Field label="Email" type="email" autoComplete="email" value={email} onChange={(ev) => setEmail(ev.target.value)} required />
              <Button type="submit">Continue</Button>
            </form>
          </CardBody>
        </Card>
      )}
      <p className="text-center text-sm text-muted">
        <Link to="/login" className="underline">
          Trading passkey login
        </Link>
      </p>
    </Frame>
  );
}
