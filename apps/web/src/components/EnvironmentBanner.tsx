import { cn } from "@/lib/utils";

/**
 * Persistent PAPER / LIVE banner (section 14, N4). LIVE is high-contrast and labelled in text, not
 * conveyed by color alone.
 */
export function EnvironmentBanner({ mode, environment }: { mode: "paper" | "live" | null; environment?: string }) {
  const live = mode === "live";
  return (
    <div
      role="status"
      aria-live="polite"
      data-mode={mode ?? "unknown"}
      className={cn(
        "flex items-center justify-center gap-3 px-4 py-1.5 text-xs font-bold uppercase tracking-[0.2em]",
        live ? "live-pulse bg-live text-white" : mode === "paper" ? "bg-paper/20 text-paper" : "bg-panel-2 text-muted",
      )}
    >
      <span>{live ? "● LIVE TRADING ENABLED" : mode === "paper" ? "PAPER" : "CONNECTING…"}</span>
      {environment && <span className="font-mono font-normal tracking-normal opacity-70">env: {environment}</span>}
    </div>
  );
}
