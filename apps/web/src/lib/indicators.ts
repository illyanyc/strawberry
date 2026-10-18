/**
 * Chart indicators (UI-049): drawing aids computed from the bars already on screen. They are display
 * only: no strategy, risk check, or P&L uses them (strategies use `tbcore` features, DF-004), and
 * prices are plotted as numbers, never shown as money (UI-I2).
 */

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Point {
  time: number;
  value: number;
}

export function sma(c: Candle[], n: number): Point[] {
  const out: Point[] = [];
  let sum = 0;
  for (let i = 0; i < c.length; i++) {
    sum += c[i]!.close;
    if (i >= n) sum -= c[i - n]!.close;
    if (i >= n - 1) out.push({ time: c[i]!.time, value: sum / n });
  }
  return out;
}

export function ema(c: Candle[], n: number): Point[] {
  if (c.length < n) return [];
  const k = 2 / (n + 1);
  let prev = c.slice(0, n).reduce((s, x) => s + x.close, 0) / n;
  const out: Point[] = [{ time: c[n - 1]!.time, value: prev }];
  for (let i = n; i < c.length; i++) {
    prev = c[i]!.close * k + prev * (1 - k);
    out.push({ time: c[i]!.time, value: prev });
  }
  return out;
}

/**
 * Volume-weighted average of the typical price, reset at each New York calendar day (session VWAP
 * for intraday bars). Bars without volume are skipped.
 */
export function sessionVwap(c: Candle[], dayKey: (t: number) => string = nyDay): Point[] {
  const out: Point[] = [];
  let day = "";
  let pv = 0;
  let vol = 0;
  for (const x of c) {
    const d = dayKey(x.time);
    if (d !== day) {
      day = d;
      pv = 0;
      vol = 0;
    }
    if (x.volume <= 0) continue;
    pv += ((x.high + x.low + x.close) / 3) * x.volume;
    vol += x.volume;
    out.push({ time: x.time, value: pv / vol });
  }
  return out;
}

const nyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

/** New York calendar date of a UTC epoch-seconds time, e.g. "2026-10-16". */
export function nyDay(epochSeconds: number): string {
  return nyFmt.format(new Date(epochSeconds * 1000));
}

export type IndicatorId = "sma20" | "sma50" | "ema20" | "vwap";

export const INDICATORS: readonly { id: IndicatorId; label: string; color: string }[] = [
  { id: "sma20", label: "SMA 20", color: "--color-chart-1" },
  { id: "sma50", label: "SMA 50", color: "--color-chart-2" },
  { id: "ema20", label: "EMA 20", color: "--color-chart-3" },
  { id: "vwap", label: "VWAP", color: "--color-chart-4" },
];

export function compute(id: IndicatorId, c: Candle[]): Point[] {
  switch (id) {
    case "sma20":
      return sma(c, 20);
    case "sma50":
      return sma(c, 50);
    case "ema20":
      return ema(c, 20);
    case "vwap":
      return sessionVwap(c);
    default: {
      const never: never = id;
      throw new Error(`unknown indicator ${String(never)}`);
    }
  }
}
