/** Berry silhouette path. The canonical drawing is `brand/logo.svg`; keep the two in sync. */
export const berryPath = "M32 13 45 20 51 33 45 48 32 56 19 48 13 33 19 20Z";

/** Strwbrry mark (decorative; the surrounding link carries the accessible name). */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden focusable="false">
      <path d={berryPath} className="fill-accent" />
      <path d="M32 13V6M25 8l7 5 7-5" className="stroke-ok" fill="none" strokeWidth="2.2" strokeLinecap="square" />
      <g className="fill-bg">
        <rect x="29" y="24" width="3.2" height="3.2" />
        <rect x="36" y="28" width="3.2" height="3.2" />
        <rect x="25" y="33" width="3.2" height="3.2" />
        <rect x="32" y="36" width="3.2" height="3.2" />
        <rect x="39" y="33" width="3.2" height="3.2" />
        <rect x="28" y="43" width="3.2" height="3.2" />
        <rect x="36" y="44" width="3.2" height="3.2" />
      </g>
    </svg>
  );
}
