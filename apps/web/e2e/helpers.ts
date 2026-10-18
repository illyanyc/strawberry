import type { Page } from "@playwright/test";

/**
 * Handed from shell.spec.ts to ui-phase5.spec.ts on the same fresh stack: the owner's passkey (the
 * virtual authenticator's credential) and the unused recovery codes. Test-only, under the gitignored
 * test-results/ directory.
 */
export const AUTH_STATE = process.env.TB_E2E_AUTH_STATE ?? "test-results/.auth/owner.json";

export const SHOTS = process.env.TB_E2E_SCREENSHOTS ?? "test-results/screens";

/** Chrome's virtual WebAuthn authenticator (resident keys, user verification) for passkey ceremonies. */
export async function addVirtualAuthenticator(page: Page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
  });
  return { cdp, authenticatorId };
}
