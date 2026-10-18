/*
 * Strawberry service worker (D13, UI-044). Shows Web Push notifications and focuses the app when one
 * is tapped. It caches nothing and intercepts no requests: a stale bundle must never keep running in
 * a trading UI, and every page needs a live session anyway.
 *
 * Payloads use the Declarative Web Push shape ({"web_push": 8030, "notification": {...}}) so Safari
 * 18.4+ can display them even without this worker; other browsers hand the same JSON to `push` here.
 */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function sameOriginPath(url) {
  try {
    const u = new URL(url || "/", self.location.origin);
    return u.origin === self.location.origin ? u.pathname + u.search : "/";
  } catch {
    return "/";
  }
}

self.addEventListener("push", (event) => {
  let msg = {};
  try {
    msg = event.data ? event.data.json() : {};
  } catch {
    msg = { notification: { title: "Strawberry", body: event.data ? event.data.text() : "" } };
  }
  const n = msg.notification || {};
  const title = n.title || "Strawberry";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: n.body || "",
      tag: n.tag || undefined,
      renotify: Boolean(n.tag),
      requireInteraction: n.require_interaction === true,
      icon: "/icons/icon-192.png",
      badge: "/icons/badge-96.png",
      timestamp: typeof n.timestamp === "number" ? n.timestamp : Date.now(),
      data: { navigate: sameOriginPath(n.navigate) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.navigate) || "/";
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const c of all) {
        if (new URL(c.url).origin === self.location.origin) {
          await c.focus();
          if ("navigate" in c) await c.navigate(target).catch(() => undefined);
          return;
        }
      }
      await self.clients.openWindow(target);
    })(),
  );
});
