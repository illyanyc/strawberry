import { cn } from "@/lib/utils";

/**
 * Small SVG charts for research views (learning curves, convergence, importance, parallel
 * coordinates). React-rendered SVG with Tailwind stroke/fill classes: no inline `<style>` and no
 * script, so the gateway CSP needs no exception (UI-005). Values are plotted for drawing only; the
 * numbers shown in text come straight from the service.
 */

const STROKES = ["stroke-accent", "stroke-paper", "stroke-ok", "stroke-warn", "stroke-muted"] as const;
const FILLS = ["fill-accent", "fill-paper", "fill-ok", "fill-warn", "fill-muted"] as const;

export interface Series {
  name: string;
  points: [number, number][];
}

function extent(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1];
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  return [lo, hi];
}

const W = 600;

/** Multi-series line chart (x = step or trial number). */
export function LineChart({ series, height = 180, label }: { series: Series[]; height?: number; label: string }) {
  const pts = series.flatMap((s) => s.points);
  if (pts.length === 0) return <p className="text-sm text-muted">No points yet.</p>;
  const [x0, x1] = extent(pts.map((p) => p[0]));
  const [y0, y1] = extent(pts.map((p) => p[1]));
  const pad = 28;
  const sx = (x: number) => pad + ((x - x0) / (x1 - x0)) * (W - 2 * pad);
  const sy = (y: number) => height - pad + 8 - ((y - y0) / (y1 - y0)) * (height - 2 * pad);
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${height}`} className="h-auto w-full" role="img" aria-label={label}>
        <line x1={pad} y1={height - pad + 8} x2={W - pad} y2={height - pad + 8} className="stroke-border" />
        <line x1={pad} y1={pad - 8} x2={pad} y2={height - pad + 8} className="stroke-border" />
        <text x={2} y={pad} className="fill-muted text-[10px]">
          {y1.toPrecision(3)}
        </text>
        <text x={2} y={height - pad + 8} className="fill-muted text-[10px]">
          {y0.toPrecision(3)}
        </text>
        <text x={W - pad} y={height - 4} textAnchor="end" className="fill-muted text-[10px]">
          {x1}
        </text>
        {series.map((s, i) => (
          <polyline
            key={s.name}
            fill="none"
            strokeWidth={1.75}
            className={STROKES[i % STROKES.length]}
            points={s.points.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ")}
          />
        ))}
      </svg>
      <figcaption className="flex flex-wrap gap-3 text-xs text-muted">
        {series.map((s, i) => (
          <span key={s.name} className="flex items-center gap-1.5">
            <svg viewBox="0 0 10 10" className="size-2.5" aria-hidden>
              <rect width={10} height={10} rx={2} className={FILLS[i % FILLS.length]} />
            </svg>
            {s.name}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Horizontal bars with an optional ± spread (importance mean ± std across folds). */
export function BarList({ items, label }: { items: { label: string; value: number; spread?: number; text: string }[]; label: string }) {
  if (items.length === 0) return <p className="text-sm text-muted">Nothing to show.</p>;
  const max = Math.max(...items.map((i) => Math.abs(i.value) + (i.spread ?? 0)), 1e-12);
  const rowH = 20;
  const lx = 170;
  const bw = W - lx - 70;
  return (
    <svg viewBox={`0 0 ${W} ${items.length * rowH + 4}`} className="h-auto w-full" role="img" aria-label={label}>
      {items.map((it, i) => {
        const y = i * rowH + 2;
        const w = (Math.abs(it.value) / max) * bw;
        const sp = ((it.spread ?? 0) / max) * bw;
        return (
          <g key={it.label}>
            <text x={lx - 6} y={y + 13} textAnchor="end" className="fill-fg text-[11px]">
              {it.label.length > 26 ? `${it.label.slice(0, 25)}…` : it.label}
            </text>
            <rect x={lx} y={y + 3} width={Math.max(w, 1)} height={rowH - 8} rx={2} className={it.value < 0 ? "fill-live/70" : "fill-paper/80"} />
            {sp > 0 && <line x1={lx + w - sp} x2={lx + w + sp} y1={y + rowH / 2} y2={y + rowH / 2} className="stroke-fg" strokeWidth={1} />}
            <text x={lx + bw + 8} y={y + 13} className="fill-muted text-[10px]">
              {it.text}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Parallel coordinates: one vertical axis per numeric parameter plus the inner metric; each trial is
 * a polyline. The best `highlight` fraction by the metric is drawn in the accent color.
 */
export function ParallelCoordinates({
  rows,
  axes,
  metric,
  label,
  highlight = 0.1,
}: {
  rows: Record<string, number>[];
  axes: string[];
  metric: string;
  label: string;
  highlight?: number;
}) {
  const all = [...axes, metric];
  if (rows.length === 0 || axes.length === 0) return <p className="text-sm text-muted">No numeric parameters to plot.</p>;
  const h = 220;
  const pad = 24;
  const ext = new Map(all.map((a) => [a, extent(rows.map((r) => r[a] ?? NaN).filter((v) => Number.isFinite(v)))]));
  const x = (i: number) => pad + (i / (all.length - 1)) * (W - 2 * pad);
  const y = (a: string, v: number) => {
    const [lo, hi] = ext.get(a) ?? [0, 1];
    return h - pad - ((v - lo) / (hi - lo)) * (h - 2 * pad);
  };
  const val = (r: Record<string, number>, a: string) => r[a] ?? NaN;
  const sorted = [...rows].sort((a, b) => val(a, metric) - val(b, metric));
  const cut = sorted.length - Math.max(1, Math.round(sorted.length * highlight));
  return (
    <svg viewBox={`0 0 ${W} ${h}`} className="h-auto w-full" role="img" aria-label={label}>
      {sorted.map((r, i) => {
        const pts = all.map((a, j) => (Number.isFinite(val(r, a)) ? `${x(j).toFixed(1)},${y(a, val(r, a)).toFixed(1)}` : null)).filter(Boolean);
        const top = i >= cut;
        return <polyline key={i} fill="none" points={pts.join(" ")} strokeWidth={top ? 1.75 : 1} strokeOpacity={top ? 0.95 : 0.25} className={top ? "stroke-accent" : "stroke-muted"} />;
      })}
      {all.map((a, j) => (
        <g key={a}>
          <line x1={x(j)} x2={x(j)} y1={pad} y2={h - pad} className="stroke-border" />
          <text x={x(j)} y={h - 6} textAnchor={j === 0 ? "start" : j === all.length - 1 ? "end" : "middle"} className={cn("text-[10px]", a === metric ? "fill-fg" : "fill-muted")}>
            {a}
          </text>
        </g>
      ))}
    </svg>
  );
}
