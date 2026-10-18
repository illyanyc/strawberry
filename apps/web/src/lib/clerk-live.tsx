import { useEffect, useState, type ComponentType, type ReactNode } from "react";

import { Alert } from "@/components/ui/form";
import { clerkConfigured, clerkPublishableKey } from "@/lib/mock-account";

interface ClerkLike {
  ClerkProvider: ComponentType<{ publishableKey: string; children?: ReactNode }>;
  SignIn: ComponentType;
  SignUp: ComponentType;
  PricingTable: ComponentType;
}

type Load =
  | { state: "off" }
  | { state: "loading" }
  | { state: "error" }
  | { state: "ready"; mod: ClerkLike };

/**
 * Loads `@clerk/react` only when a publishable key is set (ADR-0055). The Mac Mini build has no key,
 * so this import never runs and the CSP stays `script-src 'self'`. The package is optional: a missing
 * install is an error state, not a silent success.
 */
export function useClerkModule(): Load {
  const [load, setLoad] = useState<Load>(clerkConfigured() ? { state: "loading" } : { state: "off" });
  useEffect(() => {
    if (!clerkConfigured()) return;
    let cancelled = false;
    const spec = "@clerk/react";
    void import(/* @vite-ignore */ spec)
      .then((mod) => {
        if (!cancelled) setLoad({ state: "ready", mod: mod as ClerkLike });
      })
      .catch(() => {
        if (!cancelled) setLoad({ state: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return load;
}

export function ClerkFrame({ children }: { children: (mod: ClerkLike) => ReactNode }) {
  const load = useClerkModule();
  if (load.state === "off") return null;
  if (load.state === "loading") return <p className="text-sm text-muted">Loading account service…</p>;
  if (load.state === "error") {
    return (
      <Alert>
        Clerk did not load. Install <span className="font-mono">@clerk/react</span> and set VITE_CLERK_PUBLISHABLE_KEY. No card was charged.
      </Alert>
    );
  }
  const key = clerkPublishableKey();
  const Provider = load.mod.ClerkProvider;
  return <Provider publishableKey={key}>{children(load.mod)}</Provider>;
}
