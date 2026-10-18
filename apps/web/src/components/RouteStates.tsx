import type { ErrorComponentProps } from "@tanstack/react-router";
import { RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/form";
import { Skeleton } from "@/components/ui/layout";

/** Shown while a page chunk loads (UI-007, UI-043). */
export function RoutePending() {
  return (
    <div className="mx-auto max-w-6xl space-y-4" role="status" aria-label="Loading page">
      <Skeleton className="h-8 w-56" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

/**
 * A page that failed to load or render (for example a chunk missing after a deploy). The shell,
 * status strip, and kill switch stay usable around it.
 */
export function RouteError({ error, reset }: ErrorComponentProps) {
  const chunk = error instanceof Error && /dynamically imported module|Failed to fetch|Importing a module script failed/i.test(error.message);
  return (
    <div className="mx-auto max-w-2xl space-y-3">
      <Alert>{chunk ? "This page's code could not be loaded (the app may have been updated)." : "This page failed to render."}</Alert>
      <Button variant="secondary" size="sm" onClick={() => (chunk ? window.location.reload() : reset())}>
        <RotateCw />
        {chunk ? "Reload the app" : "Try again"}
      </Button>
    </div>
  );
}
