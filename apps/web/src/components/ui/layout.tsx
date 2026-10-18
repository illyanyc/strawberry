import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Page title row shared by every page (UI-040): one h1, an optional description, and actions that
 * wrap under the title on narrow screens.
 */
export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {eyebrow && <p className="mb-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">{eyebrow}</p>}
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Page container: full width on data-heavy pages, readable width elsewhere. */
export function Page({ children, wide = false, className }: { children: ReactNode; wide?: boolean; className?: string }) {
  return <div className={cn("mx-auto w-full space-y-5", wide ? "max-w-[1800px]" : "max-w-6xl", className)}>{children}</div>;
}

/**
 * A KPI tile. `value` is rendered verbatim (server decimal strings, UI-008); `delta` is a node such
 * as <Signed/> so signs are text, not color alone.
 */
export function Stat({ label, value, delta, hint, className }: { label: ReactNode; value: ReactNode; delta?: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0 rounded-xl border border-border bg-panel px-4 py-3 shadow-panel", className)}>
      <p className="truncate text-[11px] font-medium uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-1 truncate font-mono text-lg font-semibold tabular-nums sm:text-xl">{value}</p>
      {(delta || hint) && (
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
          {delta}
          {hint && <span className="truncate">{hint}</span>}
        </p>
      )}
    </div>
  );
}

/** Placeholder block while data loads (announced once by the surrounding region). */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-panel-2", className)} aria-hidden />;
}

/** Empty state with an icon, a sentence, and an optional action. */
export function EmptyState({ icon, title, children, action }: { icon?: ReactNode; title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-8 text-center">
      {icon && (
        <span className="text-subtle [&_svg]:size-6" aria-hidden>
          {icon}
        </span>
      )}
      <p className="text-sm font-medium">{title}</p>
      {children && <p className="max-w-md text-xs text-muted">{children}</p>}
      {action}
    </div>
  );
}

/** A segmented control (radio group semantics) for small option sets such as timeframes. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  size = "sm",
}: {
  value: T;
  options: readonly { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: "xs" | "sm";
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-border bg-panel-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          aria-label={o.title}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md font-medium transition-colors [&_svg]:size-3.5",
            size === "xs" ? "h-6 px-2 text-[11px]" : "h-7 px-2.5 text-xs",
            value === o.value ? "bg-panel-3 text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Keyboard key hint. */
export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}
