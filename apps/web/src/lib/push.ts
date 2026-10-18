/**
 * PWA install and Web Push (D13, UI-044, SE-060..SE-063).
 *
 * The service worker (`public/sw.js`) only shows notifications and focuses the app on click; it
 * caches nothing, so a stale bundle can never keep running. Subscriptions are created with the
 * gateway's VAPID public key and stored server-side; the browser keeps no secret of ours. Critical
 * alerts are also sent through Pushover emergency priority by Alertmanager (D45): Web Push is the
 * in-app channel, not the only one.
 */

import { api } from "./api";

export interface PushConfig {
  enabled: boolean;
  vapid_public_key: string;
  /** Lock-screen privacy: notifications carry severity and a generic text only (SE-062). */
  redact: boolean;
}

export interface PushSubscriptionRow {
  id: string;
  label: string;
  /** Push service host (e.g. fcm.googleapis.com, web.push.apple.com); never the full endpoint. */
  service: string;
  /** Hex SHA-256 prefix of the endpoint, so a browser can recognise its own row. */
  endpoint_hash: string;
  created_at: string;
  last_success_at: string | null;
  last_error: string;
  failures: number;
}

export interface PushSubscriptionsResponse {
  subscriptions: PushSubscriptionRow[];
}

export type PushSupport = "supported" | "needs-install" | "unsupported";

export function isStandalone(): boolean {
  return (
    window.matchMedia?.("(display-mode: standalone)").matches === true ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

export function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

/**
 * iOS delivers Web Push only to web apps added to the Home Screen (16.4+); elsewhere a service
 * worker and the Push API are enough.
 */
export function pushSupport(): PushSupport {
  const hasApi = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  if (isIOS() && !isStandalone()) return "needs-install";
  return hasApi ? "supported" : "unsupported";
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch {
    return null;
  }
}

/** URL-safe base64 (VAPID key) to the bytes PushManager expects. */
export function b64urlToBytes(s: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function bytesToB64url(buf: ArrayBuffer | null): string {
  if (!buf) return "";
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await navigator.serviceWorker?.getRegistration("/");
  return (await reg?.pushManager.getSubscription()) ?? null;
}

/** Ask for permission, subscribe with the gateway's VAPID key, and register the subscription. */
export async function enablePush(cfg: PushConfig, label: string): Promise<string> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error(permission === "denied" ? "Notifications are blocked for this site in the browser settings." : "Notification permission was not granted.");
  const reg = (await navigator.serviceWorker.getRegistration("/")) ?? (await registerServiceWorker());
  if (!reg) throw new Error("The service worker could not be registered.");
  await navigator.serviceWorker.ready;
  const sub =
    (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(cfg.vapid_public_key) }));
  const res = await api<{ id: string }>("/api/v1/push/subscriptions", {
    method: "POST",
    body: { endpoint: sub.endpoint, p256dh: bytesToB64url(sub.getKey("p256dh")), auth: bytesToB64url(sub.getKey("auth")), label },
  });
  return res.id;
}

/** Unsubscribe this browser and remove its server-side row. */
export async function disablePush(id: string | null): Promise<void> {
  const sub = await currentSubscription();
  await sub?.unsubscribe().catch(() => false);
  if (id) await api(`/api/v1/push/subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** A default device label such as "Chrome on macOS" (shown in the subscription list only). */
export function deviceLabel(ua: string = navigator.userAgent): string {
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /iPhone|iPad|iPod/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "device";
  return `${browser} on ${os}`;
}

/** The deferred install prompt (Chromium's `beforeinstallprompt`), captured at startup. */
interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferredPrompt: InstallPromptEvent | null = null;
const installListeners = new Set<() => void>();

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e as InstallPromptEvent;
    for (const l of installListeners) l();
  });
  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    for (const l of installListeners) l();
  });
}

export function canPromptInstall(): boolean {
  return deferredPrompt !== null;
}

export function onInstallAvailabilityChange(l: () => void): () => void {
  installListeners.add(l);
  return () => installListeners.delete(l);
}

export async function promptInstall(): Promise<boolean> {
  if (!deferredPrompt) return false;
  await deferredPrompt.prompt();
  const choice = await deferredPrompt.userChoice;
  deferredPrompt = null;
  for (const l of installListeners) l();
  return choice.outcome === "accepted";
}
