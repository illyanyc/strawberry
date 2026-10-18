# Runbook: Notifications (Pushover and Web Push)

Rules: MN-002, MN-003, SE-060..SE-063, UI-044 · Decisions: [ADR-0027](../adr/0027-monitoring-ops-slice.md), [ADR-0052](../adr/0052-web-push-from-the-gateway-and-the-installed-pwa.md) · Page: Notifications (`/settings/notifications`)

Alertmanager routes every alert. Each notifying receiver sends to **Pushover** (critical = emergency priority, repeating until acknowledged) and to the gateway, which sends **Web Push** to every subscribed browser or installed PWA. Pushover is the critical path: it works when the gateway or the PWA is down.

## Set up Pushover (critical alerts)

1. Buy the Pushover app on the phone; create an application at pushover.net.
2. Put the user key in `.secrets/pushover_user_key` and the application token in `.secrets/pushover_app_token`.
3. Restart Alertmanager: `docker compose -f infra/compose/docker-compose.yml restart alertmanager`.

## Install the PWA and enable Web Push

1. Open the platform over HTTPS (Tailscale `https://<mini>.ts.net` or the Cloudflare hostname); Web Push needs a secure origin.
2. **iPhone/iPad:** Safari → Share → Add to Home Screen; open Strawberry from the Home Screen and sign in with the passkey. **Android/desktop Chrome or Edge:** the Notifications page's "Install Strawberry" button (or the browser's install menu).
3. Notifications page → **Turn on push notifications** → allow notifications → **Send a test**.
4. Optional: Platform settings → "Push notifications for fills".

## Lock-screen privacy

`TB_PUSH_REDACT=true` (default) shows only the severity and alert name. Set it to `false` in `infra/compose/.env` to include alert summaries, then restart api-gateway.

## Existing installs

Run `make secrets` (adds `webpush_vapid_key`, `alertmanager_webhook_token`), `make migrate` (table `auth.push_subscriptions`), then restart api-gateway and Alertmanager.

## Troubleshoot

| Symptom | Check |
|---|---|
| "Web Push is not configured" | `.secrets/webpush_vapid_key` missing: `make secrets`, restart api-gateway |
| Device shows a last error | The push service refused it (expired subscription, rate limit). Turn push off and on again on that device |
| No Web Push but Pushover works | `docker compose logs alertmanager | grep webhook` (401 = token mismatch: restart both after `make secrets`) |
| iPhone gets nothing | Push only works in the Home Screen app, after allowing notifications there |
| Rotating the VAPID key | Delete `.secrets/webpush_vapid_key`, `make secrets`, restart api-gateway; every device must turn push on again |
