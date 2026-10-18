import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { KeyRound, Unplug } from "lucide-react";
import { useState } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { EmptyState, Page, PageHeader } from "@/components/ui/layout";
import { api, ApiError, type Me } from "@/lib/api";
import { ClerkFrame } from "@/lib/clerk-live";
import { clerkConfigured, readMockAccount, writeMockAccount } from "@/lib/mock-account";
import { PLANS, planAllowsLiveKey, type PlanSlug } from "@/lib/plans";
import { formatTime } from "@/lib/utils";

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "The request failed.";
}

interface KeyRow {
  kind: string;
  environment: string;
  key_hint: string;
  created_at: string;
  rotated_at: string;
}

interface Disclosure {
  accepted: boolean;
  accepted_at?: string;
}

interface CredentialRow {
  broker_account_id: string;
  broker: string;
  environment: "paper" | "live";
  fields: string[];
  created_at: number;
  rotated_at: number;
}

export function OnboardingPage() {
  const disclosure = useQuery({ queryKey: ["risk-disclosure"], queryFn: () => api<Disclosure>("/api/v1/risk-disclosure") });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api<{ keys: KeyRow[] }>("/api/v1/keys"), retry: false });
  const accept = useMutation({
    mutationFn: () => api("/api/v1/risk-disclosure", { method: "POST", body: { accepted: true } }),
    onSuccess: () => void disclosure.refetch(),
  });

  return (
    <Page>
      <PageHeader eyebrow="Setup" title="Set up" description="Accept the risk disclosure, confirm an authenticator, and connect an Alpaca paper key. The dashboard stays empty until you do." />
      <QueryState q={disclosure} empty="Disclosure status is empty.">
        {(row) =>
          row.accepted ? (
            <Alert tone="ok">Risk disclosure accepted{row.accepted_at ? ` ${formatTime(row.accepted_at)}` : ""}.</Alert>
          ) : (
            <Card>
              <CardHeader title="Risk disclosure" />
              <CardBody className="space-y-3 text-sm">
                <p>Trading can lose money. Paper results are not live results. If the broker is down, the kill switch here cannot reach it.</p>
                {accept.isError && <Alert>{errText(accept.error)}</Alert>}
                <Button onClick={() => accept.mutate()} busy={accept.isPending}>
                  Accept and continue
                </Button>
              </CardBody>
            </Card>
          )
        }
      </QueryState>
      <Card>
        <CardHeader title="Authenticator" />
        <CardBody className="text-sm">
          {me.isPending ? (
            <p className="text-muted">Loading account…</p>
          ) : me.isError ? (
            <Alert>{errText(me.error)}</Alert>
          ) : me.data.totp_enabled ? (
            <Alert tone="ok">An authenticator app is enrolled.</Alert>
          ) : (
            <p>
              No authenticator app yet.{" "}
              <Link to="/settings/security" className="underline">
                Enroll one
              </Link>{" "}
              before using a hosted session. SMS is not offered.
            </p>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Alpaca paper" />
        <CardBody className="space-y-3 text-sm">
          {keys.isPending ? (
            <p className="text-muted">Loading connections…</p>
          ) : keys.isError ? (
            <Alert>{errText(keys.error)}</Alert>
          ) : (
            <p className="text-muted">API keys on file: {keys.data.keys.length}. Broker paper keys are on the connections page.</p>
          )}
          <Link to="/connections" className="inline-flex h-8 items-center rounded-lg bg-accent px-3 text-xs font-medium text-white">
            Connect Alpaca paper
          </Link>
        </CardBody>
      </Card>
    </Page>
  );
}

/** Alpaca paper and live slots. Live stays refused while the deployment flag is off (UI-055). No IBKR button. */
export function ConnectionsPage() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api<{ credentials: CredentialRow[] }>("/api/v1/broker/credentials"),
    retry: false,
  });
  const disclosure = useQuery({ queryKey: ["risk-disclosure"], queryFn: () => api<Disclosure>("/api/v1/risk-disclosure") });
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<{ live_enabled?: boolean }>("/api/v1/system/status") });
  const mock = readMockAccount();
  const plan: PlanSlug = mock?.plan ?? "free";
  const liveBlocked =
    status.data?.live_enabled !== true || !planAllowsLiveKey(plan) || disclosure.data?.accepted !== true;
  const [paper, setPaper] = useState({ account: "", keyId: "", secret: "" });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api(`/api/v1/broker/credentials/${encodeURIComponent(paper.account)}/paper`, {
        method: "PUT",
        body: { broker: "alpaca", secret: { api_key_id: paper.keyId, api_secret: paper.secret } },
      }),
    onSuccess: () => {
      setPaper((p) => ({ ...p, keyId: "", secret: "" }));
      setMessage("Paper key stored. The secret is not shown again.");
      setError(null);
      void qc.invalidateQueries({ queryKey: ["credentials"] });
    },
    onError: (e) => setError(errText(e)),
  });

  return (
    <Page>
      <PageHeader
        eyebrow="Brokers"
        title="Connections"
        description="Alpaca paper and live are different slots. Hosted accounts do not include Interactive Brokers."
      />
      {error && <Alert>{error}</Alert>}
      {message && <Alert tone="ok">{message}</Alert>}
      <Card>
        <CardHeader title="Stored broker keys" />
        <CardBody>
          <QueryState q={list} empty="No broker key stored">
            {(data) =>
              data.credentials.length === 0 ? (
                <EmptyState icon={<Unplug />} title="No broker key stored">
                  Add an Alpaca paper key below. Nothing is connected until you do.
                </EmptyState>
              ) : (
                <ul className="divide-y divide-border text-sm">
                  {data.credentials.map((c) => (
                    <li key={`${c.broker_account_id}-${c.environment}`} className="flex items-center justify-between py-3">
                      <span>
                        {c.broker_account_id} <span className="text-muted">({c.broker})</span>
                        <span className="mt-1 block text-xs text-muted">fields stored · rotated {formatTime(c.rotated_at)}</span>
                      </span>
                      <Badge tone={c.environment === "live" ? "live" : "paper"}>{c.environment}</Badge>
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryState>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Alpaca paper" />
        <CardBody>
          <form
            className="grid gap-4 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              setMessage(null);
              save.mutate();
            }}
          >
            <Field label="Account id" value={paper.account} onChange={(e) => setPaper({ ...paper, account: e.target.value.toLowerCase() })} required pattern="[a-z0-9][a-z0-9_-]{1,63}" autoComplete="off" />
            <Field label="Key id" value={paper.keyId} onChange={(e) => setPaper({ ...paper, keyId: e.target.value })} required autoComplete="off" spellCheck={false} />
            <Field label="Secret" type="password" autoComplete="new-password" value={paper.secret} onChange={(e) => setPaper({ ...paper, secret: e.target.value })} required />
            <div className="flex items-end">
              <Button type="submit" busy={save.isPending}>
                Store paper key
              </Button>
            </div>
          </form>
          <p className="mt-3 text-xs text-muted">Write-only. Saving needs a fresh passkey. The secret is cleared from this form after a successful store.</p>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Alpaca live" />
        <CardBody className="space-y-3 text-sm">
          <Alert tone="warn">
            Live keys stay refused. Live trading is off on this deployment
            {status.data?.live_enabled === true ? "" : " (the live flag is not on)"}. A Live or Desk plan and an accepted risk disclosure are also required. Connecting a key does not turn live trading on.
          </Alert>
          <p className="text-muted">Plan seen by this browser: {plan}. {liveBlocked ? "The live slot is closed." : "The slot stays closed because this build does not submit live secrets."}</p>
          <Button variant="danger" disabled>
            Store live key
          </Button>
        </CardBody>
      </Card>
    </Page>
  );
}

/** FMP keys: write-only, paper environment, sealed by the gateway (SE-067). */
export function KeysPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["api-keys"], queryFn: () => api<{ keys: KeyRow[] }>("/api/v1/keys"), retry: false });
  const [form, setForm] = useState({ keyId: "", secret: "" });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => api("/api/v1/keys/fmp", { method: "PUT", body: { key_id: form.keyId, secret: form.secret } }),
    onSuccess: () => {
      setForm({ keyId: "", secret: "" });
      setSaved("FMP key stored. The secret is not shown again.");
      setError(null);
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (e) => setError(errText(e)),
  });
  const remove = useMutation({
    mutationFn: () => api("/api/v1/keys/fmp", { method: "DELETE" }),
    onSuccess: () => {
      setSaved("FMP key deleted.");
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (e) => setError(errText(e)),
  });

  return (
    <Page>
      <PageHeader eyebrow="Settings" title="API keys" description="Financial Modeling Prep, if you bring your own key. Strwbrry does not lend a platform key." />
      {error && <Alert>{error}</Alert>}
      {saved && <Alert tone="ok">{saved}</Alert>}
      <Card>
        <CardHeader title="Stored keys" />
        <CardBody>
          <QueryState q={list} empty="No API key stored">
            {(data) =>
              data.keys.length === 0 ? (
                <EmptyState icon={<KeyRound />} title="No API key stored">
                  A personal FMP plan cannot be shared. Paste your own key, or leave this empty.
                </EmptyState>
              ) : (
                <ul className="divide-y divide-border text-sm">
                  {data.keys.map((k) => (
                    <li key={k.kind} className="flex items-center justify-between py-3">
                      <span>
                        {k.kind} · {k.environment}
                        <span className="mt-1 block text-xs text-muted">hint {k.key_hint} · rotated {formatTime(k.rotated_at)}</span>
                      </span>
                      <Button variant="secondary" size="sm" onClick={() => remove.mutate()} busy={remove.isPending}>
                        Delete
                      </Button>
                    </li>
                  ))}
                </ul>
              )
            }
          </QueryState>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Add or rotate FMP" />
        <CardBody>
          <form
            className="grid gap-4 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              setSaved(null);
              save.mutate();
            }}
          >
            <Field label="Key id" value={form.keyId} onChange={(e) => setForm({ ...form, keyId: e.target.value })} required autoComplete="off" spellCheck={false} />
            <Field label="Secret" type="password" autoComplete="new-password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} required />
            <div className="sm:col-span-2">
              <Button type="submit" busy={save.isPending}>
                Store key
              </Button>
            </div>
          </form>
          <p className="mt-3 text-xs text-muted">Write-only. A live environment is not accepted for this key. Saving and deleting need a fresh passkey.</p>
        </CardBody>
      </Card>
    </Page>
  );
}

export function BillingPage() {
  const mock = readMockAccount();
  const [plan, setPlan] = useState<PlanSlug>(mock?.plan ?? "free");
  const [note, setNote] = useState<string | null>(null);

  const choose = (slug: PlanSlug) => {
    setPlan(slug);
    const current = readMockAccount();
    if (current && !clerkConfigured()) {
      writeMockAccount({ ...current, plan: slug, trial: slug === "research" });
      setNote(
        slug === "free"
          ? "Free does not need a card. No charge was made."
          : "Recorded in this browser only. No card was collected. Live trading stays off.",
      );
    } else if (!clerkConfigured()) {
      setNote("Create a local mock account on the sign-up page before selecting a plan. No card was collected.");
    }
  };

  return (
    <Page>
      <PageHeader eyebrow="Billing" title="Billing" description="Monthly plans in US dollars. Tax is not included. Canceling does not flatten positions." />
      {clerkConfigured() ? (
        <Card>
          <CardHeader title="Change plan" />
          <CardBody>
            <ClerkFrame>{(mod) => <mod.PricingTable />}</ClerkFrame>
          </CardBody>
        </Card>
      ) : (
        <Alert tone="warn">Clerk is not configured. Plan choice is local to this browser. No card number is collected and nothing is charged. Live trading stays off.</Alert>
      )}
      {note && <Alert tone="ok">{note}</Alert>}
      <div className="grid gap-3 md:grid-cols-2">
        {PLANS.map((p) => (
          <Card key={p.slug}>
            <CardHeader title={p.name} action={<span className="font-mono text-sm">{p.price}</span>} />
            <CardBody className="space-y-3 text-sm">
              <p className="text-muted">{p.summary}</p>
              <p className="text-xs text-muted">
                {p.strategies} strategies · {p.backtests} backtests / month · {p.analyst} analyst runs
                {p.liveCap ? ` · live cap ${p.liveCap}` : ""}
              </p>
              <Button variant={plan === p.slug ? "primary" : "secondary"} onClick={() => choose(p.slug)} aria-pressed={plan === p.slug}>
                {p.slug === "research" ? "Start Research trial" : p.slug === "free" ? "Stay on Free" : `Select ${p.name}`}
              </Button>
            </CardBody>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader title="If a payment fails" />
        <CardBody className="space-y-2 text-sm text-muted">
          <p>New entries stop. Exits, cancels, and the kill switch stay available. Protective stops are not canceled because of a bill. Positions are not flattened.</p>
          <p>Usage counters on this deployment: no runs recorded yet.</p>
          <EmptyState title="No invoices">Clerk has not issued an invoice to this install.</EmptyState>
        </CardBody>
      </Card>
    </Page>
  );
}

export function AccountPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me") });
  return (
    <Page>
      <PageHeader eyebrow="Account" title="Account" description="Profile for this trading session. Closing the account does not liquidate a broker account." />
      <Card>
        <CardHeader title="Profile" />
        <CardBody>
          {me.isPending ? (
            <p className="text-sm text-muted">Loading account…</p>
          ) : me.isError ? (
            <Alert>{errText(me.error)}</Alert>
          ) : (
            <p className="text-sm">
              {me.data.owner.display_name}
              <span className="mt-1 block text-xs text-muted">Signed in with {me.data.session.auth_method}. Email for the hosted product is managed by Clerk when Clerk is configured.</span>
            </p>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Close account" />
        <CardBody className="space-y-3 text-sm">
          <p className="text-muted">Deletion is refused while a strategy quantity is non-zero or an order is open. This control does not flatten positions and does not close the broker account.</p>
          <Button variant="danger" disabled>
            Close account
          </Button>
          <p className="text-xs text-muted">The button stays disabled on this deployment. Halt and exit strategy quantity yourself before a later version can delete the software account.</p>
        </CardBody>
      </Card>
    </Page>
  );
}
