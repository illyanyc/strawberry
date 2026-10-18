/** The Strawberry logo mark (decorative; the surrounding link carries the accessible name). */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden focusable="false">
      <rect width="64" height="64" rx="14" className="fill-panel-3" />
      <path d="M32 54c-11 0-18-9-18-20 0-9 6-15 13-16 2 0 3.5.5 5 1.4 1.5-.9 3-1.4 5-1.4 7 1 13 7 13 16 0 11-7 20-18 20z" className="fill-accent" />
      <path d="M32 19c-2-5-6-8-11-8 2 4 5 7 11 8zm0 0c2-5 6-8 11-8-2 4-5 7-11 8z" className="fill-ok" />
      <g className="fill-white/80">
        <circle cx="25" cy="31" r="1.4" />
        <circle cx="39" cy="31" r="1.4" />
        <circle cx="32" cy="37" r="1.4" />
        <circle cx="26" cy="43" r="1.4" />
        <circle cx="38" cy="43" r="1.4" />
      </g>
    </svg>
  );
}
