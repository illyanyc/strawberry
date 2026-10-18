# Runbook: TradingView webhook strategies

Rules: SM-020, SE-064, SE-065 · Decision: [ADR-0052](../adr/0052-tradingview-webhook-strategies-and-chart-embedding.md) · Page: TradingView (`/settings/tradingview`)

TradingView alerts can drive a dedicated `tv-<name>` strategy. They produce **signals**, never orders: the strategy runtime sizes and protects each trade from the platform's own bars, and every intent passes the normal pre-trade gate with the strategy's own allocation and limits. Intake is **off** by default.

## Prerequisites

- TradingView Essential plan or higher (webhooks) with 2FA on the TradingView account.
- The optional Cloudflare Tunnel (`make up PROFILES=core,observability,remote-cloudflare`, token in `.secrets/cloudflared_tunnel_token`).

## Set up

1. **Register the strategy** in `infra/compose/strategies.json` (execution and the strategy runtime both read it). It starts in `paper` like every strategy:

   ```json
   {
     "strategy_id": "tv-breakout", "short_id": "tvbrkaa", "name": "TradingView breakout alerts",
     "horizon": "intraday", "broker_account_id": "alpaca-paper", "mode": "paper", "allocation": "5000",
     "allow_short": false, "segregation_allowlist": [], "version": "1",
     "limits": { "max_positions": "2", "max_daily_loss": "150", "max_order_notional": "2500" },
     "runtime": { "class": "tradingview", "timeframe": "15m", "instruments": ["<instrument_id>"],
                  "params": { "stop_bps": "150", "risk_per_trade": "0.005", "max_signal_age_seconds": 60 } }
   }
   ```

   `instruments` are instrument IDs from `refdata.instruments`; signals for any other ticker are dropped.
2. **Enable the listener** in `infra/compose/.env` and restart api-gateway:

   ```
   TB_TV_WEBHOOK_ADDR=:8090
   TB_TV_PUBLIC_URL=https://tv.example.com
   TB_TV_TRUSTED_PROXIES=172.16.0.0/12
   TB_CF_ACCESS_TEAM=<team>.cloudflareaccess.com
   TB_CF_ACCESS_TV_AUD=<AUD tag of the webhook Access application>
   ```

   `TB_TV_TRUSTED_PROXIES` is the `edge` network's subnet (`docker network inspect strawberry_edge`): only requests from cloudflared may name the client address in `Cf-Connecting-Ip`.
3. **Cloudflare** (Zero Trust dashboard):
   - Tunnel → Public hostname `tv.example.com` → service `http://api-gateway:8090`. Do not route this hostname to port 8080.
   - Access → Applications → Self-hosted `tv.example.com`, path `/webhooks/tradingview/*`. Policy: action **Service Auth**, include **IP ranges** `52.89.214.238/32, 34.212.75.30/32, 54.218.53.128/32, 52.32.178.7/32`. **Never Bypass** (Bypass requests are not logged and carry no Access token, so the gateway refuses them anyway).
   - Copy the application's AUD tag into `TB_CF_ACCESS_TV_AUD`.
4. **Get the strategy's secret** on the Mac (it is never shown in the UI):

   ```
   make tv-secret NAME=breakout
   ```
5. **TradingView alert:** Webhook URL `https://tv.example.com/webhooks/tradingview/breakout`; message:

   ```json
   {"secret":"<from step 4>","ticker":"{{ticker}}","action":"buy","time":"{{timenow}}","price":"{{close}}","alert_id":"{{strategy.order.id}}"}
   ```

   `action` is `buy` (open long when flat, or close a short), `sell` (close a long, or open a short when flat and allowed), or `exit` (close the strategy's own position).
6. **Check** the TradingView page: intake "listening", Access "required", and after the first alert a delivery row `accepted`. The signal appears on the Charts page for that symbol.

## Operate

| Symptom | Cause | Action |
|---|---|---|
| Delivery `stale` | TradingView delivered later than 60 s, or the Mac's clock is off | Check `Clock` in the status strip; raise `TB_TV_MAX_AGE` only if TradingView is consistently late (≤ 10 min) |
| Delivery `unknown_symbol` | The ticker is not in the instrument master (or uses another symbol) | Fix the alert's ticker; the platform never guesses |
| Delivery `unknown_strategy` | `tv-<name>` not in the registry | Register it (step 1) and restart execution and the strategy runtime |
| `accepted` but no order | The strategy dropped the signal (no fresh bar, instrument not listed, already long, shorts off, too close to the close) or the gate rejected the intent | Strategy runtime logs (`webhook signal dropped`); Orders page → rejected intents |
| Security alert "TradingView webhook rejections" | Five unauthenticated requests in 10 minutes (wrong secret, wrong IP, no Access token) | Check the Access policy and the alert message; if the secret leaked, rotate it (below) |

## Rotate secrets

Secrets derive from `.secrets/tv_webhook_key`. To rotate every `tv-*` secret: stop api-gateway, delete `.secrets/tv_webhook_key`, run `make secrets`, start api-gateway, run `make tv-secret NAME=…` for each strategy, and update the TradingView alerts.

## Stop

Set the strategy to `disabled` (Strategies page) or engage its kill switch; unset `TB_TV_WEBHOOK_ADDR` to stop intake entirely.

## Chart widget

Charts always use Lightweight Charts with the attribution link and an "Open on TradingView" deep link. The embedded TradingView widget is optional: Platform settings → "TradingView chart widget" (passkey). While on, the SPA's CSP allows frames from `https://s.tradingview.com` and TradingView sees which symbols you open (SE-065).
