import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field, Switch } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import { setTheme } from "@/lib/theme";
import { formatTime } from "@/lib/utils";

interface Setting {
  value: unknown;
  version: number;
  default: boolean;
  updated_at?: string;
}

const descriptions: Record<string, { label: string; detail: string; danger?: boolean }> = {
  "trading.live_enabled": { label: "Global live-trading flag", detail: "Allows live adapters to start. Strategies still need individual promotion (Phase 6).", danger: true },
  "mobile.order_entry_enabled": { label: "Mobile order entry", detail: "Off by default (U6). Enabling needs a passkey.", danger: true },
  "assistant.write_tools_enabled": { label: "Assistant write tools", detail: "Lets the AI assistant launch experiments (Phase 4).", danger: true },
  "alerts.push_fills_enabled": { label: "Push notifications for fills", detail: "Web Push for every fill to every subscribed device (Notifications page)." },
  "ui.tradingview_widget_enabled": {
    label: "TradingView chart widget",
    detail: "Lets the Charts page frame TradingView's own chart (adds frame-src s.tradingview.com to the CSP; TradingView then sees the symbols you open). Enabling needs a passkey.",
    danger: true,
  },
};

function errText(e: unknown) {
  return e instanceof ApiError ? (e.code === "step_up_cancelled" ? "Cancelled." : e.message) : "Request failed.";
}

/** Platform settings: closed list; dangerous enables trigger passkey step-up server-side (SE-003). */
export function PlatformSettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<{ settings: Record<string, Setting> }>("/api/v1/settings") });
  const [error, setError] = useState<string | null>(null);
  const put = useMutation({
    mutationFn: ({ key, value, version }: { key: string; value: unknown; version: number }) =>
      api(`/api/v1/settings/${key}`, { method: "PUT", body: { value, version } }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["settings"] });
      void qc.invalidateQueries({ queryKey: ["status"] });
    },
    onError: (e) => setError(errText(e)),
  });
  const theme = q.data?.settings["ui.theme"];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Platform settings</h1>
      {error && <Alert>{error}</Alert>}
      <Card>
        <CardHeader title="Trading and safety" description="Every change is audited with before/after hashes." />
        <CardBody>
          {q.isPending ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : q.isError ? (
            <Alert>Could not load settings.</Alert>
          ) : (
            <ul className="divide-y divide-border">
              {Object.entries(descriptions).map(([key, d]) => {
                const s = q.data.settings[key];
                const on = s?.value === true;
                return (
                  <li key={key} className="flex items-center justify-between gap-4 py-4">
                    <div>
                      <p className="flex items-center gap-2 text-sm font-medium">
                        {d.label} {d.danger && <Badge tone="danger">step-up</Badge>}
                      </p>
                      <p className="text-xs text-muted">{d.detail}</p>
                      {s?.updated_at && <p className="text-xs text-muted">Changed {formatTime(s.updated_at)}</p>}
                    </div>
                    <Switch label={d.label} checked={on} disabled={put.isPending} onChange={(v) => put.mutate({ key, value: v, version: s?.version ?? 0 })} />
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Appearance" />
        <CardBody className="flex gap-2">
          {(["dark", "light"] as const).map((t) => (
            <Button
              key={t}
              variant={theme?.value === t ? "primary" : "secondary"}
              onClick={() => {
                setTheme(t);
                put.mutate({ key: "ui.theme", value: t, version: theme?.version ?? 0 });
              }}
            >
              {t}
            </Button>
          ))}
        </CardBody>
      </Card>
    </div>
  );
}

interface CredentialRow {
  broker_account_id: string;
  broker: string;
  environment: "paper" | "live";
  fields: string[];
  created_at: number;
  rotated_at: number;
}

/** Broker connections: write-only credential forms (SE-031). Values are never read back. */
export function BrokersPage() {
  const qc = useQueryClient();
  const [unlocked, setUnlocked] = useState(false);
  const list = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api<{ credentials: CredentialRow[] }>("/api/v1/broker/credentials"),
    enabled: unlocked,
    retry: false,
  });
  const [form, setForm] = useState({ account: "", broker: "alpaca", environment: "paper", keyId: "", secret: "" });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () =>
      api(`/api/v1/broker/credentials/${encodeURIComponent(form.account)}/${form.environment}`, {
        method: "PUT",
        body: { broker: form.broker, secret: { api_key_id: form.keyId, api_secret: form.secret } },
      }),
    onSuccess: () => {
      setSaved(`Stored credential for ${form.account} (${form.environment}).`);
      setForm((f) => ({ ...f, keyId: "", secret: "" }));
      setError(null);
      void qc.invalidateQueries({ queryKey: ["credentials"] });
    },
    onError: (e) => setError(errText(e)),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">Broker connections</h1>
      <Alert tone="warn">Credentials are write-only: they are encrypted by broker-gateway and can never be displayed again. Viewing or changing them needs a passkey.</Alert>
      {error && <Alert>{error}</Alert>}
      {saved && <Alert tone="ok">{saved}</Alert>}
      <Card>
        <CardHeader title="Stored credentials" />
        <CardBody>
          {!unlocked ? (
            <Button variant="secondary" onClick={() => setUnlocked(true)}>
              Show credential status
            </Button>
          ) : list.isPending ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : list.isError ? (
            <Alert>{errText(list.error)}</Alert>
          ) : list.data.credentials.length === 0 ? (
            <p className="text-sm text-muted">No broker credentials stored yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {list.data.credentials.map((c) => (
                <li key={`${c.broker_account_id}-${c.environment}`} className="flex items-center justify-between py-3 text-sm">
                  <div>
                    <p className="font-medium">
                      {c.broker_account_id} <span className="text-muted">({c.broker})</span>
                    </p>
                    <p className="text-xs text-muted">
                      fields: {c.fields.join(", ")} · rotated {formatTime(c.rotated_at)}
                    </p>
                  </div>
                  <Badge tone={c.environment === "live" ? "live" : "paper"}>{c.environment}</Badge>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Add or rotate a credential" />
        <CardBody>
          <form
            className="grid gap-4 sm:grid-cols-2"
            onSubmit={(e) => {
              e.preventDefault();
              setSaved(null);
              save.mutate();
            }}
          >
            <Field label="Account ID" placeholder="alpaca-paper-1" value={form.account} onChange={(e) => setForm({ ...form, account: e.target.value.toLowerCase() })} required pattern="[a-z0-9][a-z0-9_-]{1,63}" />
            <label className="space-y-1.5 text-xs font-medium text-muted">
              Broker
              <select className="mt-1.5 h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm text-fg" value={form.broker} onChange={(e) => setForm({ ...form, broker: e.target.value })}>
                <option value="alpaca">Alpaca</option>
                <option value="ibkr">IBKR</option>
              </select>
            </label>
            <fieldset className="sm:col-span-2">
              <legend className="mb-1.5 text-xs font-medium text-muted">Environment</legend>
              <div className="flex gap-2">
                {(["paper", "live"] as const).map((env) => (
                  <Button key={env} variant={form.environment === env ? (env === "live" ? "danger" : "primary") : "secondary"} onClick={() => setForm({ ...form, environment: env })} aria-pressed={form.environment === env}>
                    {env.toUpperCase()}
                  </Button>
                ))}
              </div>
            </fieldset>
            <Field label="API key ID" autoComplete="off" spellCheck={false} value={form.keyId} onChange={(e) => setForm({ ...form, keyId: e.target.value })} required />
            <Field label="API secret" type="password" autoComplete="new-password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} required />
            <div className="sm:col-span-2">
              <Button type="submit" variant={form.environment === "live" ? "danger" : "primary"} busy={save.isPending}>
                Store {form.environment.toUpperCase()} credential
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
