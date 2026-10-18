import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  LineStyle,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { compute, type Candle, type IndicatorId, INDICATORS } from "@/lib/indicators";
import { useTheme } from "@/lib/theme";
import type { Bar, Order } from "@/lib/trading";
import { nsToMs } from "@/lib/trading";
import { cn } from "@/lib/utils";

/**
 * A marker on the chart (UI-012, UI-049): a fill (arrow, side colored) or a signal (circle for
 * long, square for flat/exit, arrow down for short), always with a text label so nothing is conveyed
 * by color or shape alone.
 */
export interface ChartMarker {
  timeNs: string;
  side: "buy" | "sell";
  text: string;
  kind?: "fill" | "signal";
  direction?: "long" | "short" | "flat";
}

/** A resting order price (limit, stop, protective stop) drawn as a horizontal line. */
export interface ChartLine {
  price: string;
  title: string;
  kind: "limit" | "stop";
}

/** A per-bar score series (model signal scores) drawn in its own pane. */
export interface ScoreSeries {
  id: string;
  points: { timeNs: string; value: number }[];
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
  return toOhlcv(bars).map(({ time, open, high, low, close }) => ({ time: time as UTCTimestamp, open, high, low, close }));
}

/** Candles with volume (for the volume pane and indicators), sorted and de-duplicated. */
export function toOhlcv(bars: Bar[]): Candle[] {
  const byTime = new Map<number, Candle>();
  for (const b of bars) {
    const c = toCandle(b);
    if (c) byTime.set(c.time, { ...c, volume: Number(b.volume?.value ?? 0) });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

/** Markers snap to the bar containing the event so they render on the candle. */
export function toMarkers(markers: ChartMarker[], barSeconds: number): SeriesMarker<Time>[] {
  return markers
    .map((m) => {
      const t = Math.floor(nsToMs(m.timeNs) / 1000);
      const time = (t - (t % barSeconds)) as UTCTimestamp;
      if (m.kind === "signal") {
        const dir = m.direction ?? (m.side === "buy" ? "long" : "flat");
        return {
          time,
          position: dir === "short" ? ("aboveBar" as const) : ("belowBar" as const),
          shape: dir === "long" ? ("circle" as const) : dir === "short" ? ("arrowDown" as const) : ("square" as const),
          color: dir === "long" ? css("--color-info", "#5b9cf6") : dir === "short" ? css("--color-chart-2", "#c084fc") : css("--color-subtle", "#5f6c7d"),
          text: m.text,
        };
      }
      return {
        time,
        position: m.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
        shape: m.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
        color: m.side === "buy" ? css("--color-ok", "#3fb27f") : css("--color-live", "#ff2d55"),
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

export interface Legend {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

function themeOptions() {
  return {
    layout: {
      background: { color: css("--color-panel", "#0f141b") },
      textColor: css("--color-muted", "#8b98a9"),
      attributionLogo: false,
      panes: { separatorColor: css("--color-border", "#222b38"), separatorHoverColor: css("--color-border-strong", "#2f3a4a") },
    },
    grid: { vertLines: { color: css("--color-border", "#222b38") }, horzLines: { color: css("--color-border", "#222b38") } },
    rightPriceScale: { borderColor: css("--color-border", "#222b38") },
    timeScale: { borderColor: css("--color-border", "#222b38"), timeVisible: true, secondsVisible: false },
    crosshair: { vertLine: { labelBackgroundColor: css("--color-panel-3", "#1b2330") }, horzLine: { labelBackgroundColor: css("--color-panel-3", "#1b2330") } },
  };
}

/**
 * Candlestick chart on TradingView Lightweight Charts (D12, D46): candles, a volume pane, optional
 * indicator overlays, fill and signal markers, resting-order price lines, and a model-score pane.
 * The library's attribution logo is disabled because it injects an inline <style> the strict CSP
 * blocks (UI-005); pages render the required attribution link themselves. Live updates arrive
 * already coalesced (≤ 4 Hz per chart, UI-012) and are applied with `update()`, never a full reset.
 */
export function PriceChart({
  bars,
  live,
  markers,
  lines,
  timeframe,
  indicators = [],
  scores = [],
  showVolume = true,
  onLegend,
  className = "h-[420px]",
  label = "Price chart",
}: {
  bars: Bar[];
  live: Bar | null;
  markers: ChartMarker[];
  lines: ChartLine[];
  timeframe: string;
  indicators?: IndicatorId[];
  scores?: ScoreSeries[];
  showVolume?: boolean;
  onLegend?: (l: Legend | null) => void;
  /** Height as a Tailwind class (no inline styles, UI-005). */
  className?: string;
  label?: string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerApi = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const overlays = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const scoreSeries = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const ohlcv = useRef<Map<number, Candle>>(new Map());
  const legendCb = useRef(onLegend);
  const [theme] = useTheme();

  useEffect(() => {
    legendCb.current = onLegend;
  }, [onLegend]);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, { autoSize: true, ...themeOptions() });
    const s = c.addSeries(CandlestickSeries, {
      upColor: css("--color-ok", "#3fb27f"),
      downColor: css("--color-live", "#ff2d55"),
      wickUpColor: css("--color-ok", "#3fb27f"),
      wickDownColor: css("--color-live", "#ff2d55"),
      borderVisible: false,
    });
    chart.current = c;
    series.current = s;
    markerApi.current = createSeriesMarkers(s, []);
    const onMove = (p: MouseEventParams<Time>) => {
      const cb = legendCb.current;
      if (!cb) return;
      if (p.time === undefined) return cb(null);
      const k = ohlcv.current.get(p.time as number);
      cb(k ? { ...k } : null);
    };
    c.subscribeCrosshairMove(onMove);
    const ov = overlays.current;
    const sc = scoreSeries.current;
    return () => {
      c.unsubscribeCrosshairMove(onMove);
      c.remove();
      chart.current = null;
      series.current = null;
      volume.current = null;
      markerApi.current = null;
      priceLines.current = [];
      ov.clear();
      sc.clear();
    };
  }, []);

  // Theme changes re-read the CSS variables.
  useEffect(() => {
    chart.current?.applyOptions(themeOptions());
    series.current?.applyOptions({
      upColor: css("--color-ok", "#3fb27f"),
      downColor: css("--color-live", "#ff2d55"),
      wickUpColor: css("--color-ok", "#3fb27f"),
      wickDownColor: css("--color-live", "#ff2d55"),
    });
  }, [theme]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    if (showVolume && !volume.current) {
      volume.current = c.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, color: css("--color-border-strong", "#2f3a4a"), priceLineVisible: false, lastValueVisible: false }, 1);
      c.panes()[1]?.setHeight(80);
    } else if (!showVolume && volume.current) {
      c.removeSeries(volume.current);
      volume.current = null;
    }
  }, [showVolume]);

  useEffect(() => {
    const data = toOhlcv(bars);
    ohlcv.current = new Map(data.map((k) => [k.time, k]));
    series.current?.setData(data.map(({ time, open, high, low, close }) => ({ time: time as UTCTimestamp, open, high, low, close })));
    volume.current?.setData(volumeData(data));
    const c = chart.current;
    if (c) {
      for (const [id, line] of overlays.current) {
        line.setData(compute(id as IndicatorId, data).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
      }
      c.timeScale().fitContent();
    }
  }, [bars, showVolume]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const want = new Set(indicators);
    for (const [id, line] of overlays.current) {
      if (!want.has(id as IndicatorId)) {
        c.removeSeries(line);
        overlays.current.delete(id);
      }
    }
    const data = [...ohlcv.current.values()].sort((a, b) => a.time - b.time);
    for (const id of indicators) {
      if (overlays.current.has(id)) continue;
      const spec = INDICATORS.find((i) => i.id === id)!;
      const line = c.addSeries(LineSeries, { color: css(spec.color, "#5b9cf6"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      line.setData(compute(id, data).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
      overlays.current.set(id, line);
    }
  }, [indicators]);

  useEffect(() => {
    if (!live || !series.current) return;
    const c = toCandle(live);
    const data = series.current.data();
    const last = data.length ? (data[data.length - 1]!.time as number) : 0;
    if (c && c.time >= last) {
      series.current.update(c);
      const k = { ...c, volume: Number(live.volume?.value ?? 0) };
      ohlcv.current.set(c.time, k);
      volume.current?.update(volumeData([k])[0]!);
    }
  }, [live]);

  useEffect(() => {
    markerApi.current?.setMarkers(toMarkers(markers, TF_SECONDS[timeframe] ?? 60));
  }, [markers, timeframe]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const pane = showVolume ? 2 : 1;
    const want = new Set(scores.map((s) => s.id));
    for (const [id, line] of scoreSeries.current) {
      if (!want.has(id)) {
        c.removeSeries(line);
        scoreSeries.current.delete(id);
      }
    }
    const bucket = TF_SECONDS[timeframe] ?? 60;
    scores.forEach((s, i) => {
      let line = scoreSeries.current.get(s.id);
      if (!line) {
        const color = css(INDICATORS[i % INDICATORS.length]!.color, "#5b9cf6");
        line = c.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, title: s.id, lastValueVisible: true }, pane);
        scoreSeries.current.set(s.id, line);
        c.panes()[pane]?.setHeight(90);
      }
      const byTime = new Map<number, number>();
      for (const p of s.points) {
        const t = Math.floor(nsToMs(p.timeNs) / 1000);
        if (Number.isFinite(p.value)) byTime.set(t - (t % bucket), p.value);
      }
      line.setData([...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value })));
    });
  }, [scores, showVolume, timeframe]);

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

  return <div ref={el} className={cn("w-full", className)} role="img" aria-label={label} />;
}

function volumeData(c: Candle[]): HistogramData<UTCTimestamp>[] {
  const up = css("--color-ok", "#3fb27f");
  const down = css("--color-live", "#ff2d55");
  return c.map((k) => ({ time: k.time as UTCTimestamp, value: k.volume, color: withAlpha(k.close >= k.open ? up : down, 0.45) }));
}

/** `#rrggbb` plus an alpha byte (canvas colors must be plain CSS colors). */
export function withAlpha(hex: string, a: number): string {
  if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return hex;
  return hex + Math.round(Math.min(1, Math.max(0, a)) * 255).toString(16).padStart(2, "0");
}
