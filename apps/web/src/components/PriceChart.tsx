import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  LineStyle,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Bar, Order } from "@/lib/trading";
import { nsToMs } from "@/lib/trading";
import { cn } from "@/lib/utils";

/** A fill or signal marker on the chart (UI-012). */
export interface ChartMarker {
  timeNs: string;
  side: "buy" | "sell";
  text: string;
}

/** A resting order price (limit, stop, protective stop) drawn as a horizontal line. */
export interface ChartLine {
  price: string;
  title: string;
  kind: "limit" | "stop";
}

const css = (name: string, fallback: string) =>
  (typeof document !== "undefined" && getComputedStyle(document.documentElement).getPropertyValue(name).trim()) || fallback;

/**
 * Chart values are plotted as numbers; this is display only. Money shown as text elsewhere stays a
 * server decimal string (UI-008).
 */
export function toCandle(b: Bar): CandlestickData<UTCTimestamp> | null {
  if (!b.open || !b.high || !b.low || !b.close) return null;
  return {
    time: Math.floor(nsToMs(b.tsOpen) / 1000) as UTCTimestamp,
    open: Number(b.open.value),
    high: Number(b.high.value),
    low: Number(b.low.value),
    close: Number(b.close.value),
  };
}

/** Sorted, de-duplicated candles (the newest version of a bar wins), as the library requires. */
export function toCandles(bars: Bar[]): CandlestickData<UTCTimestamp>[] {
  const byTime = new Map<number, CandlestickData<UTCTimestamp>>();
  for (const b of bars) {
    const c = toCandle(b);
    if (c) byTime.set(c.time, c);
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

/** Markers snap to the bar containing the execution so they render on the candle. */
export function toMarkers(markers: ChartMarker[], barSeconds: number): SeriesMarker<Time>[] {
  return markers
    .map((m) => {
      const t = Math.floor(nsToMs(m.timeNs) / 1000);
      return {
        time: (t - (t % barSeconds)) as UTCTimestamp,
        position: m.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
        shape: m.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
        color: m.side === "buy" ? css("--color-ok", "#46a758") : css("--color-live", "#ff2d55"),
        text: m.text,
      };
    })
    .sort((a, b) => a.time - b.time);
}

/** Price lines for resting orders: limit and stop prices of open orders in the instrument. */
export function orderLines(orders: Order[]): ChartLine[] {
  const out: ChartLine[] = [];
  for (const o of orders) {
    const who = o.strategyId || "manual";
    if (o.stopPrice?.value) out.push({ price: o.stopPrice.value, title: `${who} stop`, kind: "stop" });
    else if (o.limitPrice?.value) out.push({ price: o.limitPrice.value, title: `${who} ${o.side.replace("SIDE_", "").toLowerCase()}`, kind: "limit" });
  }
  return out;
}

export const TF_SECONDS: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400 };

/**
 * Candlestick chart on TradingView Lightweight Charts (D46, ADR-0028). The built-in attribution
 * logo is disabled because it injects an inline <style> the strict CSP blocks (UI-005); the page
 * renders the required attribution link itself.
 */
export function PriceChart({
  bars,
  live,
  markers,
  lines,
  timeframe,
  className = "h-[420px]",
}: {
  bars: Bar[];
  live: Bar | null;
  markers: ChartMarker[];
  lines: ChartLine[];
  timeframe: string;
  /** Height as a Tailwind class (no inline styles, UI-005). */
  className?: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markerApi = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: css("--color-panel", "#121821") }, textColor: css("--color-muted", "#8b98a9"), attributionLogo: false },
      grid: { vertLines: { color: css("--color-border", "#243041") }, horzLines: { color: css("--color-border", "#243041") } },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: css("--color-border", "#243041") },
    });
    const s = c.addSeries(CandlestickSeries, {
      upColor: css("--color-ok", "#46a758"),
      downColor: css("--color-live", "#ff2d55"),
      wickUpColor: css("--color-ok", "#46a758"),
      wickDownColor: css("--color-live", "#ff2d55"),
      borderVisible: false,
    });
    chart.current = c;
    series.current = s;
    markerApi.current = createSeriesMarkers(s, []);
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
      markerApi.current = null;
      priceLines.current = [];
    };
  }, []);

  useEffect(() => {
    series.current?.setData(toCandles(bars));
    chart.current?.timeScale().fitContent();
  }, [bars]);

  useEffect(() => {
    if (!live || !series.current) return;
    const c = toCandle(live);
    const data = series.current.data();
    const last = data.length ? (data[data.length - 1]!.time as number) : 0;
    if (c && c.time >= last) series.current.update(c);
  }, [live]);

  useEffect(() => {
    markerApi.current?.setMarkers(toMarkers(markers, TF_SECONDS[timeframe] ?? 60));
  }, [markers, timeframe]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    for (const pl of priceLines.current) s.removePriceLine(pl);
    priceLines.current = lines.map((l) =>
      s.createPriceLine({
        price: Number(l.price),
        title: l.title,
        color: l.kind === "stop" ? css("--color-warn", "#f5a524") : css("--color-paper", "#3b82f6"),
        lineStyle: l.kind === "stop" ? LineStyle.Dashed : LineStyle.Dotted,
        lineWidth: 1,
        axisLabelVisible: true,
      }),
    );
  }, [lines]);

  return <div ref={el} className={cn("w-full", className)} role="img" aria-label="Price chart" />;
}
