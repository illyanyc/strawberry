import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-w-0 rounded-xl border border-border bg-panel shadow-panel", className)} {...props} />;
}

export function CardHeader({
  title,
  description,
  action,
  icon,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** A small leading icon (decorative). */
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-border px-4 py-3 sm:px-5">
      <div className="flex min-w-0 items-start gap-2.5">
        {icon && (
          <span className="mt-0.5 text-muted [&_svg]:size-4" aria-hidden>
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-4 py-4 sm:px-5", className)} {...props} />;
}
