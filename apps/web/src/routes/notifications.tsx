import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellOff, BellRing, Download, Send, Siren, Smartphone, Trash2 } from "lucide-react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { QueryState } from "@/components/trading";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge } from "@/components/ui/form";
import { EmptyState, Page, PageHeader } from "@/components/ui/layout";
import { api, ApiError } from "@/lib/api";
import {
  canPromptInstall,
  currentSubscription,
  deviceLabel,
  disablePush,
  enablePush,
  isIOS,
  isStandalone,
  onInstallAvailabilityChange,
  promptInstall,
  pushSupport,
  type PushConfig,
  type PushSubscriptionsResponse,
} from "@/lib/push";
import { formatTime } from "@/lib/utils";

/** SHA-256 of a push endpoint, hex, first 16 bytes: how the server identifies a device (SE-061). */
export async function endpointHash(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return [...new Uint8Array(digest)]
    .slice(0, 16)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Notifications (UI-044): install the PWA, enable Web Push on this device, see every subscribed
 * device, send a test, and how critical alerts reach the phone (Pushover emergency priority through
 * Alertmanager, independent of this app and the gateway).
 */
export function NotificationsPage() {
  const queryClient = useQueryClient();
  const cfg = useQuery({ queryKey: ["push", "config"], queryFn: () => api<PushConfig>("/api/v1/push/config") });
  const subs = useQuery({ queryKey: ["push", "subscriptions"], queryFn: () => api<PushSubscriptionsResponse>("/api/v1/push/subscriptions") });
  const [thisHash, setThisHash] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const support = pushSupport();
  const installable = useSyncExternalStore(onInstallAvailabilityChange, canPromptInstall, () => false);

  useEffect(() => {
    let alive = true;
    void currentSubscription().then(async (s) => {
      if (alive) setThisHash(s ? await endpointHash(s.endpoint) : null);
    });
    return () => {
      alive = false;
    };
  }, [subs.data]);

  const mine = subs.data?.subscriptions.find((s) => thisHash !== null && s.endpoint_hash === thisHash) ?? null;
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["push"] });

  const enable = useMutation({
    mutationFn: () => enablePush(cfg.data!, deviceLabel()),
    onSuccess: () => {
      setMessage("Push notifications are on for this device.");
      refresh();
    },
  });
  const disable = useMutation({
    mutationFn: () => disablePush(mine?.id ?? null),
    onSuccess: () => {
      setMessage("Push notifications are off for this device.");
      setThisHash(null);
      refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/v1/push/subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: refresh,
  });
  const test = useMutation({
    mutationFn: () => api<{ sent: number; failed: number }>("/api/v1/push/test", { method: "POST", body: {} }),
    onSuccess: (r) => {
      setMessage(`Test sent to ${r.sent} device${r.sent === 1 ? "" : "s"}${r.failed ? `, ${r.failed} failed` : ""}.`);
      refresh();
    },
  });
  const err = [enable.error, disable.error, remove.error, test.error].find(Boolean);

  return (
    <Page>
      <PageHeader
        eyebrow="Settings"
        title="Notifications"
        description="Install the app on your phone, turn on push notifications, and check how critical alerts reach you."
      />
      {message && <Alert tone="ok">{message}</Alert>}
      {err && <Alert>{err instanceof ApiError || err instanceof Error ? err.message : "Request failed"}</Alert>}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader icon={<Download />} title="Install the app" description="An installed PWA opens full screen and can receive Web Push on iOS 16.4+." />
          <CardBody className="space-y-3 text-sm">
            {isStandalone() ? (
              <p className="flex items-center gap-2">
                <Badge tone="ok">installed</Badge> This window is the installed app.
              </p>
            ) : isIOS() ? (
              <ol className="list-decimal space-y-1 pl-5 text-muted">
                <li>Open this page in Safari.</li>
                <li>
                  Tap <span className="font-medium text-fg">Share</span>, then <span className="font-medium text-fg">Add to Home Screen</span>.
                </li>
                <li>Open Strwbrry from the Home Screen and sign in with your passkey.</li>
                <li>Come back here and turn on push notifications.</li>
              </ol>
            ) : installable ? (
              <Button variant="secondary" size="sm" onClick={() => void promptInstall()}>
                <Download />
                Install Strwbrry
              </Button>
            ) : (
              <p className="text-muted">Use your browser's "Install app" or "Add to Home screen" menu item. It appears once the browser has seen the app over HTTPS (Tailscale or Cloudflare hostname) or on localhost.</p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader
            icon={<BellRing />}
            title="Web Push on this device"
            action={mine ? <Badge tone="ok">on</Badge> : <Badge tone="neutral">off</Badge>}
            description="Warnings, critical alerts, and daily reports routed by Alertmanager (D45)."
          />
          <CardBody className="space-y-3 text-sm">
            <QueryState q={cfg} empty="" isEmpty={() => false}>
              {(c) =>
                !c.enabled ? (
                  <Alert tone="warn">Web Push is not configured on the server (VAPID key missing). Run `make secrets` and restart api-gateway.</Alert>
                ) : support === "needs-install" ? (
                  <Alert tone="warn">On iPhone and iPad, push works only in the installed app. Add Strwbrry to the Home Screen first.</Alert>
                ) : support === "unsupported" ? (
                  <Alert tone="warn">This browser does not support Web Push.</Alert>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {mine ? (
                      <Button variant="secondary" size="sm" busy={disable.isPending} onClick={() => disable.mutate()}>
                        <BellOff />
                        Turn off on this device
                      </Button>
                    ) : (
                      <Button size="sm" busy={enable.isPending} onClick={() => enable.mutate()}>
                        <BellRing />
                        Turn on push notifications
                      </Button>
                    )}
                    <Button variant="ghost" size="sm" busy={test.isPending} disabled={(subs.data?.subscriptions.length ?? 0) === 0} onClick={() => test.mutate()}>
                      <Send />
                      Send a test
                    </Button>
                  </div>
                )
              }
            </QueryState>
            {cfg.data?.redact && <p className="text-xs text-muted">Lock-screen privacy is on: notifications show the severity and a generic message only.</p>}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader icon={<Smartphone />} title="Subscribed devices" description="Each browser or installed app that receives Web Push. Removing one stops delivery immediately." />
        <CardBody className="overflow-x-auto p-0 sm:p-0">
          <QueryState q={subs} empty="" isEmpty={() => false}>
            {(d) =>
              d.subscriptions.length === 0 ? (
                <EmptyState icon={<BellOff />} title="No device receives Web Push yet" />
              ) : (
                <table className="data-table" aria-label="Push subscriptions">
                  <thead>
                    <tr>
                      <th className="pl-4 sm:pl-5">Device</th>
                      <th>Push service</th>
                      <th>Added</th>
                      <th>Last delivered</th>
                      <th>Last error</th>
                      <th className="pr-4 sm:pr-5">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.subscriptions.map((s) => (
                      <tr key={s.id}>
                        <td className="pl-4 font-medium sm:pl-5">
                          {s.label || "device"} {mine?.id === s.id && <Badge tone="info">this device</Badge>}
                        </td>
                        <td className="font-mono text-xs text-muted">{s.service}</td>
                        <td className="text-xs text-muted">{formatTime(s.created_at)}</td>
                        <td className="text-xs text-muted">{formatTime(s.last_success_at)}</td>
                        <td className="max-w-[16rem] truncate text-xs text-warn" title={s.last_error}>
                          {s.last_error || "—"}
                        </td>
                        <td className="pr-4 sm:pr-5">
                          <Button variant="ghost" size="xs" onClick={() => remove.mutate(s.id)} aria-label={`Remove ${s.label || "device"}`}>
                            <Trash2 />
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            }
          </QueryState>
        </CardBody>
      </Card>
      <Card>
        <CardHeader icon={<Siren />} title="Critical alerts: Pushover emergency" description="Independent of this app: Alertmanager pages through Pushover even when the gateway or the PWA is down." />
        <CardBody className="space-y-2 text-sm text-muted">
          <p>
            Critical alerts (risk breach, broker disconnect, reconciliation break, host down, IBKR 2FA) use Pushover <span className="text-fg">emergency priority</span>: they
            repeat every 2 minutes until acknowledged in the Pushover app. Warnings use normal priority and are muted 22:00–07:00 New York; critical alerts never are.
          </p>
          <p>
            Keys live in <code className="font-mono text-xs">.secrets/pushover_user_key</code> and <code className="font-mono text-xs">.secrets/pushover_app_token</code> (runbook: Notifications).
          </p>
        </CardBody>
      </Card>
    </Page>
  );
}
