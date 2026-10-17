import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind class names (shadcn/ui convention). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Format a server timestamp (RFC 3339 or UTC ns) for display in local time, labelled with the zone. */
export function formatTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const d = typeof value === "number" ? new Date(value / 1e6) : new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  // dateStyle/timeStyle cannot be combined with timeZoneName, so list the fields explicitly.
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}
