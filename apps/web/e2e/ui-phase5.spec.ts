import { existsSync, readFileSync, writeFileSync } from "node:fs";

import { type Browser, type BrowserContext, devices, expect, type Page, test } from "@playwright/test";

import { addVirtualAuthenticator, AUTH_STATE, SHOTS } from "./helpers";

// Section 14 Phase 5 journeys on the same fresh stack as shell.spec.ts (which runs first and hands
// over the owner's passkey and unused recovery codes): the desktop shell, palette, shortcuts, theme,
// overview, backtests, charts, notifications, TradingView; the phone tab bar, "More" sheet, the
// passkey halt (RM-020), desktop-only release (RM-007), and phone order entry refused while its
// setting is off (U6). Both viewports must produce zero CSP violations.

interface OwnerState {
  credentials: Record<string, unknown>[];
  recoveryCodes: string[];
}

// Read when a test starts: shell.spec.ts writes the file during the same run, after collection.
let state: OwnerState | null = null;
const loadState = () => {
  state = existsSync(AUTH_STATE) ? (JSON.parse(readFileSync(AUTH_STATE, "utf8")) as OwnerState) : null;
  return state;
};

async function withPasskey(page: Page) {
  const { cdp, authenticatorId } = await addVirtualAuthenticator(page);
  for (const credential of state!.credentials) await cdp.send("WebAuthn.addCredential", { authenticatorId, credential: credential as never });
}

function watchCSP(page: Page, into: string[]) {
  page.on("console", (m) => {
    if (m.type() === "error" && /Content Security Policy/i.test(m.text())) into.push(`${page.viewportSize()?.width}px: ${m.text()}`);
  });
}

async function phone(browser: Browser): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext({ ...devices["Pixel 7"], baseURL: process.env.TB_E2E_BASE_URL ?? "http://localhost:8080" });
  return { ctx, page: await ctx.newPage() };
}

const shot = (page: Page, name: string, fullPage = true) => page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage });

test.describe.configure({ mode: "serial" });

test("Phase 5 desktop: shell, palette, shortcuts, theme, overview, backtests, charts, notifications, TradingView", async ({ page }) => {
  test.skip(!loadState(), "needs shell.spec.ts first on a fresh stack (owner passkey hand-over)");
  test.setTimeout(180_000);
  const csp: string[] = [];
  watchCSP(page, csp);
  await withPasskey(page);
  await page.goto("/login");
  await page.getByRole("button", { name: "Sign in with passkey" }).click();
  await expect(page.getByRole("heading", { name: /Welcome, Illya/ })).toBeVisible();

  // UI-013: the status strip states every condition in text.
  const strip = page.getByRole("list", { name: "Trading status" });
  await expect(strip).toContainText(/Running|Frozen|Execution: unknown/);
  await expect(strip).toContainText(/Clock|Execution: unknown/);
  await expect(strip).toContainText(/Feeds: unknown|ago|disconnected|No feeds/);
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
  await shot(page, "p5-desktop-01-dashboard");

  // UI-041: grouped sidebar from the registry.
  const main = page.getByRole("navigation", { name: "Main", exact: true });
  for (const section of ["Overview", "Trading", "Research", "Operations", "Settings"]) await expect(main.getByText(section, { exact: true })).toBeVisible();

  // UI-042: ⌘K palette → page; ticker → chart deep link.
  await page.keyboard.press("Control+K");
  const palette = page.getByRole("combobox", { name: "Search pages, symbols, and actions" });
  await expect(palette).toBeFocused();
  await palette.fill("end of day");
  await shot(page, "p5-desktop-02-command-palette", false);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Portfolio overview" })).toBeVisible();
  await shot(page, "p5-desktop-03-portfolio-overview");
  await page.getByRole("radio", { name: "End of day" }).click();
  await expect(page.getByRole("radio", { name: "End of day" })).toHaveAttribute("aria-checked", "true");
  await shot(page, "p5-desktop-04-end-of-day");

  // g <key> navigation and the shortcut help.
  await page.locator("main").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("g");
  await page.keyboard.press("c");
  await expect(page.getByRole("heading", { name: "Charts", exact: true })).toBeVisible();
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
  await page.keyboard.press("Escape");

  // UI-049: split layouts, indicators, overlays, attribution.
  await page.getByRole("radio", { name: "Four charts" }).click();
  await expect(page.getByLabel(/Symbol \(chart 4\)/)).toBeVisible();
  await page.getByRole("button", { name: "SMA 20" }).click();
  await expect(page.getByRole("button", { name: "SMA 20" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("main").getByRole("link", { name: "TradingView", exact: true }).first()).toHaveAttribute("rel", "noopener noreferrer");
  await shot(page, "p5-desktop-05-charts-4up");
  await page.getByRole("radio", { name: "Single chart" }).click();
  await page.getByLabel("Symbol", { exact: true }).fill("SPY");
  await page.getByRole("button", { name: "Show" }).click();
  await expect(page.getByText(/SPY · 15m|Unknown symbol|unknown|not found|unavailable/i).first()).toBeVisible();
  await shot(page, "p5-desktop-06-charts");

  // UI-051: the data explorer shows feeds and refuses to call unknown data fresh.
  await main.getByRole("link", { name: "Data explorer" }).click();
  await expect(page.getByRole("heading", { name: "Data explorer" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Feeds" }).or(page.getByText(/Feeds: unknown|No feeds configured/))).toBeVisible();
  await shot(page, "p5-desktop-06b-data-explorer");

  // UI-047: backtests and tearsheets render their state (no ml profile in the default stack).
  await main.getByRole("link", { name: "Backtests & tearsheets" }).click();
  await expect(page.getByRole("heading", { name: "Backtests & tearsheets" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Compare 0\/2/ })).toBeDisabled();
  await shot(page, "p5-desktop-07-backtests");

  // UI-044: Notifications (install, Web Push config, devices, Pushover).
  await main.getByRole("link", { name: "Notifications" }).click();
  await expect(page.getByRole("heading", { name: "Notifications" })).toBeVisible();
  await expect(page.getByText("Web Push on this device")).toBeVisible();
  await expect(page.getByRole("button", { name: "Turn on push notifications" })).toBeVisible();
  await expect(page.getByText("No device receives Web Push yet")).toBeVisible();
  await shot(page, "p5-desktop-08-notifications");

  // UI-048: TradingView intake is off by default and shows no secret.
  await main.getByRole("link", { name: "TradingView" }).click();
  await expect(page.getByRole("heading", { name: "TradingView" })).toBeVisible();
  await expect(page.getByText(/Webhook intake is off/)).toBeVisible();
  await expect(page.locator("pre")).toContainText("{{ticker}}");
  await shot(page, "p5-desktop-09-tradingview");

  // UI-040: light theme, then back to dark.
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).toHaveClass(/light/);
  await main.getByRole("link", { name: "Dashboard" }).click();
  await shot(page, "p5-desktop-10-dashboard-light");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);

  expect(csp, "no CSP violations").toEqual([]);
});

test("Phase 5 phone: tab bar, More sheet, passkey halt, desktop-only release, phone orders refused", async ({ browser, page }) => {
  test.skip(!loadState() || state!.recoveryCodes.length < 3, "needs shell.spec.ts first on a fresh stack");
  test.setTimeout(180_000);
  const csp: string[] = [];
  const { ctx, page: mobile } = await phone(browser);
  watchCSP(mobile, csp);
  await withPasskey(mobile);

  // Sign in with a recovery code: a reduced-assurance session with no passkey step-up (SE-014).
  await mobile.goto("/login");
  await mobile.getByRole("button", { name: "Use a recovery code" }).click();
  // Each code works once: take it out of the hand-over file so a rerun uses the next one.
  const code = state!.recoveryCodes.pop()!;
  writeFileSync(AUTH_STATE, JSON.stringify(state), { mode: 0o600 });
  await mobile.getByLabel("Recovery code").fill(code);
  await mobile.getByRole("button", { name: "Sign in" }).click();
  await expect(mobile.getByRole("heading", { name: /Welcome, Illya/ })).toBeVisible();
  const tabs = mobile.getByRole("navigation", { name: "Main mobile" });
  for (const t of ["Home", "Portfolio", "Strategies", "Charts"]) await expect(tabs.getByRole("link", { name: t, exact: true })).toBeVisible();
  await expect(mobile.getByRole("navigation", { name: "Main", exact: true })).toBeHidden();
  await shot(mobile, "p5-mobile-01-dashboard", false);

  // UI-041: the "More" sheet lists the remaining pages.
  await tabs.getByRole("button", { name: "More" }).click();
  const sheet = mobile.getByRole("dialog", { name: "All pages" });
  await expect(sheet.getByRole("link", { name: "Portfolio overview" })).toBeVisible();
  await shot(mobile, "p5-mobile-02-more-sheet", false);
  await sheet.getByRole("link", { name: "Portfolio overview" }).click();
  await expect(mobile.getByRole("heading", { name: "Portfolio overview" })).toBeVisible();
  await shot(mobile, "p5-mobile-03-overview", false);
  await tabs.getByRole("link", { name: "Charts", exact: true }).click();
  await expect(mobile.getByRole("heading", { name: "Charts", exact: true })).toBeVisible();
  await shot(mobile, "p5-mobile-04-charts", false);

  // RM-020 / UI-050: the phone halts only after a fresh passkey; the step-up offers no TOTP.
  await mobile.getByRole("button", { name: "Global kill switch" }).click();
  const engage = mobile.getByRole("dialog", { name: "Engage kill switch: all trading" });
  await expect(engage.getByText(/your passkey confirms the halt/)).toBeVisible();
  await engage.getByLabel("Reason (optional)").fill("phone drill");
  await engage.getByRole("button", { name: "Engage kill switch" }).click();
  const stepUp = mobile.getByRole("dialog", { name: "Confirm it's you" });
  await expect(stepUp).toBeVisible();
  await expect(stepUp.getByLabel(/authenticator code/)).toHaveCount(0);
  await shot(mobile, "p5-mobile-05-halt-passkey", false);
  await stepUp.getByRole("button", { name: "Use passkey" }).click();
  const frozen = mobile.getByRole("link", { name: /Trading frozen by 1 global kill switch/ });
  await expect(frozen).toBeVisible();
  await shot(mobile, "p5-mobile-06-frozen", false);

  // RM-007: release is refused on the phone.
  await frozen.click();
  await mobile.getByRole("list", { name: "Kill switches" }).getByRole("button", { name: "Release…" }).first().click();
  const release = mobile.getByRole("dialog", { name: "Release kill switch" });
  await release.getByLabel("Release reason").fill("phone drill over");
  await release.getByRole("button", { name: "Release", exact: true }).click();
  await expect(release.getByText(/only be released from a desktop/)).toBeVisible();
  await shot(mobile, "p5-mobile-07-release-refused", false);
  await release.getByRole("button", { name: "Cancel" }).click();

  // U6 / UI-015: phone order entry stays off until its setting is enabled.
  const status = await mobile.evaluate(async () => {
    const csrf = document.cookie.split("; ").find((c) => c.startsWith("tb_csrf=") || c.startsWith("__Host-tb_csrf="))?.split("=")[1] ?? "";
    const r = await fetch("/api/v1/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ broker_account_id: "sim-paper", instrument_id: "E2E_INSTRUMENT", side: "buy", quantity: "1", order_type: "limit", limit_price: "1.00", tif: "day", reason: "e2e" }),
    });
    return { code: r.status, body: (await r.json()) as { error?: string } };
  });
  expect(status.code).toBe(403);
  expect(status.body.error).toBe("mobile_order_entry_disabled");

  // Notifications on the phone (install guidance, Web Push card).
  await tabs.getByRole("button", { name: "More" }).click();
  await mobile.getByRole("dialog", { name: "All pages" }).getByRole("link", { name: "Notifications" }).click();
  await expect(mobile.getByRole("heading", { name: "Notifications" })).toBeVisible();
  await shot(mobile, "p5-mobile-08-notifications", false);
  expect(csp, "no CSP violations on the phone").toEqual([]);
  await ctx.close();

  // The desktop releases it with a passkey.
  await withPasskey(page);
  await page.goto("/login");
  await page.getByRole("button", { name: "Sign in with passkey" }).click();
  await page.getByRole("link", { name: /Trading frozen/ }).click();
  await page.getByRole("list", { name: "Kill switches" }).getByRole("button", { name: "Release…" }).first().click();
  await page.getByRole("dialog", { name: "Release kill switch" }).getByLabel("Release reason").fill("phone drill over");
  await page.getByRole("dialog", { name: "Release kill switch" }).getByRole("button", { name: "Release", exact: true }).click();
  await expect(page.getByRole("button", { name: "Global kill switch" })).toBeVisible();
  await shot(page, "p5-desktop-11-released");
});
