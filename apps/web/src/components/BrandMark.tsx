/** Berry silhouette path. The canonical drawing is `brand/logo.svg`; keep the two in sync. */
export const berryPath = "M32 14 47 21 54 32 47 49 32 56 17 49 10 32 17 21Z";

/** Strwbrry mark (decorative; the surrounding link carries the accessible name). */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden focusable="false">
      <path d={berryPath} className="fill-accent" />
      <rect x="30" y="2" width="4" height="12" className="fill-ok" />
      <polygon points="30,9 15,3 19,14" className="fill-ok" />
      <polygon points="34,9 49,3 45,14" className="fill-ok" />
      <g className="fill-bg">
        <rect x="26" y="23" width="6" height="6" />
        <rect x="36" y="23" width="6" height="6" />
        <rect x="21" y="33" width="6" height="6" />
        <rect x="31" y="33" width="6" height="6" />
        <rect x="41" y="33" width="6" height="6" />
        <rect x="26" y="43" width="6" height="6" />
        <rect x="36" y="43" width="6" height="6" />
      </g>
    </svg>
  );
}
