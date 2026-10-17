import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-white hover:brightness-110",
  secondary: "bg-panel-2 text-fg border border-border hover:bg-border/60",
  ghost: "text-muted hover:text-fg hover:bg-panel-2",
  danger: "bg-live/90 text-white hover:bg-live",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-5 text-base",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  busy?: boolean;
}

/** shadcn/ui-style button. `busy` disables it and shows progress for async actions. */
export function Button({ variant = "primary", size = "md", busy = false, className, disabled, children, ...props }: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy && <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  );
}
