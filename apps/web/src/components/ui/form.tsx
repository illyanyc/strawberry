import { type InputHTMLAttributes, type ReactNode, useId } from "react";

import { cn } from "@/lib/utils";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-lg border border-border bg-bg px-3 text-sm text-fg placeholder:text-muted/70 focus:border-accent focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

/** A labelled input with an optional hint and error message (accessible by id). */
export function Field({
  label,
  hint,
  error,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode; error?: string | null }) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-xs font-medium text-muted">
        {label}
      </label>
      <Input id={id} aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-err` : undefined} {...props} />
      {hint && !error && <p className="text-xs text-muted">{hint}</p>}
      {error && (
        <p id={`${id}-err`} className="text-xs text-live" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-border transition disabled:opacity-50",
        checked ? "bg-accent" : "bg-panel-2",
      )}
    >
      <span className={cn("inline-block size-4 rounded-full bg-white shadow transition", checked ? "translate-x-6" : "translate-x-1")} />
    </button>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "danger" | "paper" | "live"; children: ReactNode }) {
  const tones = {
    neutral: "bg-panel-2 text-muted border-border",
    ok: "bg-ok/15 text-ok border-ok/30",
    warn: "bg-warn/15 text-warn border-warn/30",
    danger: "bg-live/15 text-live border-live/40",
    paper: "bg-paper/15 text-paper border-paper/30",
    live: "bg-live text-white border-live",
  } as const;
  return <span className={cn("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide", tones[tone])}>{children}</span>;
}

export function Alert({ tone = "danger", children }: { tone?: "danger" | "warn" | "ok"; children: ReactNode }) {
  const tones = { danger: "border-live/40 bg-live/10 text-live", warn: "border-warn/40 bg-warn/10 text-warn", ok: "border-ok/40 bg-ok/10 text-ok" };
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("rounded-lg border px-3 py-2 text-sm", tones[tone])}>
      {children}
    </div>
  );
}
