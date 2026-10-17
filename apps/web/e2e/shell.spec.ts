import { createHmac } from "node:crypto";

import { expect, type Page, test } from "@playwright/test";

// Phase 0 shell slice end to end in a real browser against the running stack (`make up`), using
// Chrome's virtual authenticator for passkeys. Needs a fresh stack and TB_E2E_SETUP_TOKEN.
// Screenshots go to TB_E2E_SCREENSHOTS (default test-results/screens).

const shots = process.env.TB_E2E_SCREENSHOTS ?? "test-results/screens";

async function addVirtualAuthenticator(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
}

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits) for the secret shown during enrollment. */
function totp(base32: string, offsetSteps = 0): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of base32.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offsetSteps));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1]! & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

test("owner setup, passkey and TOTP login, step-up, credentials, audit", async ({ page }) => {
  const token = process.env.TB_E2E_SETUP_TOKEN;
  test.skip(!token, "TB_E2E_SETUP_TOKEN not set");
  const cspErrors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && /Content Security Policy/i.test(m.text())) cspErrors.push(m.text());
  });
  await addVirtualAuthenticator(page);

  // First-run setup → passkey → recovery codes.
  await page.goto("/");
  await expect(page).toHaveURL(/\/setup$/);
  await page.getByLabel("Setup token").fill(token!);
  await page.getByLabel("Your name").fill("Illya");
  await page.getByLabel("Name for this passkey").fill("MacBook");
  await page.screenshot({ path: `${shots}/01-setup.png` });
  await page.getByRole("button", { name: "Create passkey" }).click();
  await expect(page.getByRole("list", { name: "Recovery codes" }).getByRole("listitem")).toHaveCount(10);
  await page.screenshot({ path: `${shots}/02-recovery-codes.png` });
  await page.getByRole("button", { name: /I saved them/ }).click();

  // Security page: enroll the authenticator app.
  await expect(page).toHaveURL(/\/settings\/security$/);
  await expect(page.getByRole("status").first()).toContainText("PAPER");
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.locator("code.break-all").innerText()).trim();
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("enabled", { exact: true })).toBeVisible();
  await page.screenshot({ path: `${shots}/03-security.png`, fullPage: true });

  // Log out, then sign in with the TOTP fallback (reduced assurance, SE-014).
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("button", { name: "Use an authenticator code" }).click();
  await page.getByLabel("Authenticator code").fill(totp(secret, 1));
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(/You signed in with a TOTP code/)).toBeVisible();
  await page.screenshot({ path: `${shots}/04-dashboard-totp.png` });

  // Enabling live trading demands a passkey step-up; the dialog offers no TOTP option.
  await page.getByRole("link", { name: "Platform settings" }).click();
  await page.getByRole("switch", { name: "Global live-trading flag" }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel(/authenticator code/)).toHaveCount(0);
  await page.screenshot({ path: `${shots}/05-step-up.png` });
  await dialog.getByRole("button", { name: "Use passkey" }).click();
  await expect(page.getByRole("switch", { name: "Global live-trading flag" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("status").first()).toContainText("LIVE TRADING ENABLED");
  await page.screenshot({ path: `${shots}/06-live-banner.png` });
  await page.getByRole("switch", { name: "Global live-trading flag" }).click();
  await expect(page.getByRole("switch", { name: "Global live-trading flag" })).toHaveAttribute("aria-checked", "false");
  await expect(page.getByRole("status").first()).toContainText("PAPER");

  // Write-only broker credential (step-up still fresh).
  await page.getByRole("link", { name: "Broker connections" }).click();
  await page.getByLabel("Account ID").fill("alpaca-paper-ui");
  await page.getByLabel("API key ID").fill("PKUITEST000000000000");
  await page.getByLabel("API secret").fill("ui-test-secret-value");
  await page.getByRole("button", { name: "Store PAPER credential" }).click();
  await expect(page.getByText("Stored credential for alpaca-paper-ui (paper).")).toBeVisible();
  await page.getByRole("button", { name: "Show credential status" }).click();
  await expect(page.getByRole("listitem").filter({ hasText: "alpaca-paper-ui" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("ui-test-secret-value");
  await page.screenshot({ path: `${shots}/07-brokers.png`, fullPage: true });

  // Audit log and chain verification.
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByRole("button", { name: "Verify chain" }).click();
  await expect(page.getByText(/Intact: \d+ events/)).toBeVisible();
  await page.screenshot({ path: `${shots}/08-audit.png`, fullPage: true });

  // Mobile layout.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("navigation", { name: "Main mobile" }).getByRole("link", { name: "Home" }).click();
  await expect(page.getByRole("heading", { name: /Welcome, Illya/ })).toBeVisible();
  await page.screenshot({ path: `${shots}/09-mobile-dashboard.png`, fullPage: true });

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  // Passkey login from the login screen.
  await page.getByRole("button", { name: "Sign in with passkey" }).click();
  await expect(page.getByRole("heading", { name: /Welcome, Illya/ })).toBeVisible();
  expect(cspErrors, "no CSP violations").toEqual([]);
});
