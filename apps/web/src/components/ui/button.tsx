import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "outline" | "ghost" | "danger";
type Size = "xs" | "sm" | "md" | "lg" | "icon";

const variants: Record<Variant, string> = {
  primary: "bg-accent text-white shadow-sm hover:brightness-110 active:brightness-95",
  secondary: "bg-panel-2 text-fg border border-border hover:bg-panel-3 hover:border-border-strong",
  outline: "border border-border text-fg hover:bg-panel-2",
  ghost: "text-muted hover:text-fg hover:bg-panel-2",
  danger: "bg-live text-white shadow-sm hover:brightness-110 active:brightness-95",
};

const sizes: Record<Size, string> = {
  xs: "h-7 px-2 text-xs",
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-5 text-base",
  icon: "size-9 p-0",
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
        "inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
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
