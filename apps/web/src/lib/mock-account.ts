import type { PlanSlug } from "@/lib/plans";

const KEY = "strwbrry.mock.account";

/** Browser-only stand-in for Clerk when `VITE_CLERK_PUBLISHABLE_KEY` is unset. Not a gateway session. */
export interface MockAccount {
  email: string;
  plan: PlanSlug;
  mfa: true;
  disclosure: boolean;
  trial: boolean;
}

export function clerkPublishableKey(): string {
  const key = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;
  return typeof key === "string" ? key.trim() : "";
}

export function clerkConfigured(): boolean {
  return clerkPublishableKey().startsWith("pk_");
}

export function readMockAccount(): MockAccount | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MockAccount>;
    if (typeof parsed.email !== "string" || parsed.mfa !== true) return null;
    const plan = parsed.plan;
    if (plan !== "free" && plan !== "research" && plan !== "live" && plan !== "desk") return null;
    return {
      email: parsed.email,
      plan,
      mfa: true,
      disclosure: parsed.disclosure === true,
      trial: parsed.trial === true,
    };
  } catch {
    return null;
  }
}

export function writeMockAccount(account: MockAccount): void {
  sessionStorage.setItem(KEY, JSON.stringify(account));
}

export function clearMockAccount(): void {
  sessionStorage.removeItem(KEY);
}
