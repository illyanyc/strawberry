import { useId } from "react";

import { cn } from "@/lib/utils";

/**
 * A tiny equity line. Points are converted to numbers for drawing only; the values shown as text
 * elsewhere stay server decimal strings (UI-I2). The trend color comes with an accessible label.
 */
export function Sparkline({ values, className, label }: { values: number[]; className?: string; label: string }) {
  const gradId = useId();
  const pts = values.filter((v) => Number.isFinite(v));
  if (pts.length < 2) {
    return (
      <div className={cn("flex items-center text-[11px] text-subtle", className)} role="img" aria-label={`${label}: not enough history`}>
        not enough history
      </div>
    );
  }
  const w = 120;
  const h = 32;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const xy = pts.map((v, i) => [(i / (pts.length - 1)) * w, h - 2 - ((v - min) / span) * (h - 4)] as const);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const up = pts[pts.length - 1]! >= pts[0]!;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={cn("h-8 w-full", up ? "text-ok" : "text-live", className)} role="img" aria-label={`${label}: ${up ? "up" : "down"} over the period`}>
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.25" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w},${h} L0,${h} Z`} fill={`url(#${gradId})`} />
      <path d={line} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
