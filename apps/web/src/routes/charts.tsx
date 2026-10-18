import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { ExternalLink, LayoutGrid, Search } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type ChartMarker, type Legend, orderLines, PriceChart, type ScoreSeries } from "@/components/PriceChart";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Alert, Badge, Input } from "@/components/ui/form";
import { Page, PageHeader, Segmented } from "@/components/ui/layout";
import { api, ApiError } from "@/lib/api";
import { INDICATORS, type IndicatorId } from "@/lib/indicators";
import { useResolveSymbol } from "@/lib/instruments";
import { useMarketStream } from "@/lib/market";
import { useStrategies } from "@/lib/queries";
import {
  dec,
  nsToMs,
  TIMEFRAMES,
  type Bar,
  type BarsResponse,
  type ExecutionReport,
  type FillsResponse,
  type OrdersResponse,
  type Signal,
  type SignalsResponse,
  type Timeframe,
} from "@/lib/trading";
import { cn } from "@/lib/utils";
import type { ChartSearch } from "@/router";

type Layout = "1" | "2" | "4";
type Overlay = "fills" | "orders" | "signals" | "volume";

const TV_INTERVAL: Record<Timeframe, string> = { "1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "D" };

/** Deep link to the symbol on tradingview.com (opened in a new tab; nothing is sent before a click). */
export function tradingViewUrl(symbol: string, tf: Timeframe): string {
  return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}&interval=${TV_INTERVAL[tf]}`;
}

/** TradingView's free embeddable chart widget (only rendered when `ui.tradingview_widget_enabled` is on). */
export function tradingViewWidgetUrl(symbol: string, tf: Timeframe, theme: "dark" | "light"): string {
  const q = new URLSearchParams({ symbol, interval: TV_INTERVAL[tf], theme, style: "1", locale: "en", hidesidetoolbar: "1", saveimage: "0", withdateranges: "1" });
  return `https://s.tradingview.com/widgetembed/?${q}`;
}

export function signalDirection(s: Signal): "long" | "short" | "flat" {
  return s.direction === "DIRECTION_LONG" ? "long" : s.direction === "DIRECTION_SHORT" ? "short" : "flat";
}

/** Signal markers: one per signal, labelled with the strategy and direction (and score when known). */
export function signalMarkers(signals: Signal[]): ChartMarker[] {
  const seen = new Set<string>();
  const out: ChartMarker[] = [];
  for (const s of signals) {
    if (seen.has(s.signalId)) continue;
    seen.add(s.signalId);
    const dir = signalDirection(s);
    const score = typeof s.score === "number" && s.score !== 0 ? ` ${s.score.toFixed(3)}` : "";
    out.push({ timeNs: s.asOf, side: dir === "long" ? "buy" : "sell", kind: "signal", direction: dir, text: `${s.strategyId} ${dir}${score}` });
  }
  return out;
}

/** Per-strategy score series for the score pane (model signals carry scores; webhook signals do not). */
export function scoreSeries(signals: Signal[]): ScoreSeries[] {
  const by = new Map<string, ScoreSeries>();
  for (const s of signals) {
    if (typeof s.score !== "number" || !s.modelVersion) continue;
    const id = `${s.strategyId} score`;
    const cur = by.get(id) ?? { id, points: [] };
    cur.points.push({ timeNs: s.asOf, value: s.score });
    by.set(id, cur);
  }
  return [...by.values()];
}

/**
 * Charts (UI-012, UI-049): candles from the platform's own bars with live final and partial bars
 * over SSE; overlays of fills, resting orders and protective stops, model and webhook signals (with
 * a score pane), volume, and drawing-only indicators; split layouts of 1, 2, or 4 charts; feed
 * label, data age, attribution, and a TradingView deep link.
 */
export function ChartsPage() {
  const search = useSearch({ from: "/app/charts" }) as ChartSearch;
  const navigate = useNavigate();
  const layout: Layout = search.layout ?? "1";
  const [indicators, setIndicators] = useState<IndicatorId[]>([]);
  const [overlays, setOverlays] = useState<Set<Overlay>>(new Set(["fills", "orders", "signals", "volume"]));
  const strategies = useStrategies();
  const strategy = search.strategy ?? "";
  const widget = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<{ settings: Record<string, { value: unknown; version: number }> }>("/api/v1/settings"),
    select: (d) => d.settings["ui.tradingview_widget_enabled"]?.value === true,
  });

  const setSearch = (patch: Partial<ChartSearch>) => void navigate({ to: "/charts", search: { ...search, ...patch }, replace: true });
  const toggleOverlay = (o: Overlay) =>
    setOverlays((s) => {
      const n = new Set(s);
      if (n.has(o)) n.delete(o);
      else n.add(o);
      return n;
    });
  const toggleIndicator = (id: IndicatorId) => setIndicators((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]));
  const panes = Number(layout);

  return (
    <Page wide>
      <PageHeader
        title="Charts"
        description="Platform bars with live updates, fills, orders and stops, and model or webhook signals."
        actions={
          <>
            <label className="flex items-center gap-2 text-xs text-muted">
              Owner
              <select
                value={strategy}
                onChange={(e) => setSearch({ strategy: e.target.value || undefined })}
                className="h-8 rounded-lg border border-border bg-panel-2 px-2 text-xs text-fg"
                aria-label="Show overlays for owner"
              >
                <option value="">All owners</option>
                <option value="manual">manual</option>
                {(strategies.data?.strategies ?? []).map((s) => (
                  <option key={s.strategyId} value={s.strategyId}>
                    {s.strategyId}
                  </option>
                ))}
              </select>
            </label>
            <Segmented
              label="Layout"
              value={layout}
              onChange={(v) => setSearch({ layout: v === "1" ? undefined : v })}
              options={[
                { value: "1", label: "1", title: "Single chart" },
                { value: "2", label: "2", title: "Two charts" },
                { value: "4", label: <LayoutGrid />, title: "Four charts" },
              ]}
            />
          </>
        }
      />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2" role="toolbar" aria-label="Chart overlays">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wider text-subtle">Overlays</span>
          {(["fills", "orders", "signals", "volume"] as const).map((o) => (
            <Chip key={o} on={overlays.has(o)} onClick={() => toggleOverlay(o)}>
              {o}
            </Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wider text-subtle">Indicators</span>
          {INDICATORS.map((i) => (
            <Chip key={i.id} on={indicators.includes(i.id)} onClick={() => toggleIndicator(i.id)}>
              {i.label}
            </Chip>
          ))}
        </div>
      </div>
      <div className={cn("grid gap-4", panes === 2 && "xl:grid-cols-2", panes === 4 && "lg:grid-cols-2")}>
        {Array.from({ length: panes }, (_, i) => (
          <ChartPane
            key={i}
            index={i}
            initialSymbol={i === 0 ? (search.symbol ?? "") : ""}
            initialTf={(TIMEFRAMES as readonly string[]).includes(search.tf ?? "") ? (search.tf as Timeframe) : "15m"}
            compact={panes > 1}
            overlays={overlays}
            indicators={indicators}
            owner={strategy}
            widget={widget.data === true}
            onPrimaryChange={i === 0 ? (symbol, tf) => setSearch({ symbol: symbol || undefined, tf: tf === "15m" ? undefined : tf }) : undefined}
          />
        ))}
      </div>
    </Page>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "h-7 rounded-md border px-2.5 text-xs capitalize transition-colors",
        on ? "border-info/50 bg-info/15 text-fg" : "border-border text-muted hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

function ChartPane({
  index,
  initialSymbol,
  initialTf,
  compact,
  overlays,
  indicators,
  owner,
  widget,
  onPrimaryChange,
}: {
  index: number;
  initialSymbol: string;
  initialTf: Timeframe;
  compact: boolean;
  overlays: Set<Overlay>;
  indicators: IndicatorId[];
  owner: string;
  widget: boolean;
  onPrimaryChange?: (symbol: string, tf: Timeframe) => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [symbol, setSymbol] = useState(initialSymbol);
  const [instrument, setInstrument] = useState<{ id: string; symbol: string } | null>(null);
  const [tf, setTf] = useState<Timeframe>(initialTf);
  const [live, setLive] = useState<Bar | null>(null);
  const [liveFills, setLiveFills] = useState<ExecutionReport[]>([]);
  const [liveSignals, setLiveSignals] = useState<Signal[]>([]);
  const [legend, setLegend] = useState<Legend | null>(null);
  const [showWidget, setShowWidget] = useState(false);
  const resolve = useResolveSymbol();
  const id = instrument?.id ?? null;

  const load = useCallback(
    (s: string) => {
      const sym = s.trim().toUpperCase();
      if (!sym) return;
      resolve.mutate(sym, {
        onSuccess: (instrumentId) => {
          setInstrument({ id: instrumentId, symbol: sym });
          setLive(null);
          setLiveFills([]);
          setLiveSignals([]);
        },
      });
    },
    [resolve],
  );

  // Load a deep-linked symbol (palette, strategy pages) once.
  const autoloaded = useRef(false);
  useEffect(() => {
    if (autoloaded.current || !initialSymbol) return;
    autoloaded.current = true;
    load(initialSymbol);
  }, [initialSymbol, load]);

  const bars = useQuery({
    queryKey: ["bars", id, tf],
    enabled: id !== null,
    queryFn: () => api<BarsResponse>(`/api/v1/bars?instrument_id=${encodeURIComponent(id!)}&timeframe=${tf}&limit=500`),
  });
  const firstNs = bars.data?.bars[0]?.tsOpen ?? "";
  const fills = useQuery({
    queryKey: ["fills", { instrument: id }],
    enabled: id !== null && overlays.has("fills"),
    queryFn: () => api<FillsResponse>(`/api/v1/fills?instrument_id=${encodeURIComponent(id!)}&limit=500`),
  });
  const orders = useQuery({
    queryKey: ["orders", { open: true }],
    enabled: id !== null && overlays.has("orders"),
    queryFn: () => api<OrdersResponse>("/api/v1/orders?open_only=true&limit=500"),
    refetchInterval: 10_000,
  });
  const signals = useQuery({
    queryKey: ["signals", id, firstNs],
    enabled: id !== null && overlays.has("signals") && firstNs !== "",
    queryFn: () => api<SignalsResponse>(`/api/v1/signals?instrument_id=${encodeURIComponent(id!)}&since=${firstNs}&limit=2000`),
    retry: false,
  });

  const stream = useMarketStream(id, tf, {
    onBar: useCallback((b: Bar) => setLive(b), []),
    onFill: useCallback(
      (f: ExecutionReport) => {
        setLiveFills((s) => [...s.slice(-200), f]);
        void queryClient.invalidateQueries({ queryKey: ["orders", { open: true }] });
      },
      [queryClient],
    ),
    onSignal: useCallback((s: Signal) => setLiveSignals((xs) => [...xs.slice(-500), s]), []),
    onResync: useCallback(() => void queryClient.invalidateQueries({ queryKey: ["bars"] }), [queryClient]),
    onSessionEnded: useCallback(() => void navigate({ to: "/login" }), [navigate]),
  });

  const ownerOk = useCallback((who: string) => owner === "" || who === owner, [owner]);
  const markers = useMemo<ChartMarker[]>(() => {
    const out: ChartMarker[] = [];
    if (overlays.has("fills")) {
      const seen = new Set<string>();
      const add = (exec: string, timeNs: string, side: string, qty: string, price: string, who: string) => {
        if (seen.has(exec) || !ownerOk(who)) return;
        seen.add(exec);
        out.push({ timeNs, side: side === "SIDE_BUY" ? "buy" : "sell", kind: "fill", text: `${who} ${qty} @ ${price}` });
      };
      for (const f of fills.data?.fills ?? []) add(f.brokerExecId, f.eventTime, f.side, dec(f.qty), dec(f.price), f.strategyId || "manual");
      for (const f of liveFills) add(f.brokerExecId, f.eventTime, f.side, dec(f.qty), dec(f.price), f.clientOrderId.startsWith("tb1-") ? f.clientOrderId.split("-")[1]! : "manual");
    }
    if (overlays.has("signals")) {
      out.push(...signalMarkers([...(signals.data?.signals ?? []), ...liveSignals].filter((s) => s.instrumentId === id && ownerOk(s.strategyId))));
    }
    return out;
  }, [overlays, fills.data, liveFills, signals.data, liveSignals, id, ownerOk]);
  const scores = useMemo(
    () => (overlays.has("signals") ? scoreSeries([...(signals.data?.signals ?? []), ...liveSignals].filter((s) => s.instrumentId === id && ownerOk(s.strategyId))) : []),
    [overlays, signals.data, liveSignals, id, ownerOk],
  );
  const lines = useMemo(
    () => (overlays.has("orders") ? orderLines((orders.data?.orders ?? []).filter((o) => o.instrumentId === id && ownerOk(o.strategyId || "manual"))) : []),
    [orders.data, id, overlays, ownerOk],
  );
  const lastBar = bars.data?.bars.at(-1);
  const feed = lastBar?.feed;
  const lastMs = live ? nsToMs(live.tsClose) : lastBar ? nsToMs(lastBar.tsClose) : 0;
  const theme = typeof document !== "undefined" && document.documentElement.classList.contains("light") ? "light" : "dark";

  return (
    <Card className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            load(symbol);
            onPrimaryChange?.(symbol.trim().toUpperCase(), tf);
          }}
        >
          <label htmlFor={`sym-${index}`} className="sr-only">
            Symbol{compact ? ` (chart ${index + 1})` : ""}
          </label>
          <Input
            id={`sym-${index}`}
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            placeholder="Symbol"
            className="h-8 w-28 font-mono uppercase"
            autoComplete="off"
            spellCheck={false}
          />
          <Button type="submit" variant="secondary" size="sm" disabled={!symbol} busy={resolve.isPending} aria-label={compact ? `Show chart ${index + 1}` : "Show"}>
            <Search />
            {!compact && "Show"}
          </Button>
        </form>
        <Segmented
          size="xs"
          label={`Timeframe${compact ? ` (chart ${index + 1})` : ""}`}
          value={tf}
          onChange={(t) => {
            setTf(t);
            setLive(null);
            onPrimaryChange?.(instrument?.symbol ?? "", t);
          }}
          options={TIMEFRAMES.map((t) => ({ value: t, label: t }))}
        />
        {instrument && (
          <span className="ml-auto flex items-center gap-1.5">
            {feed && <Badge tone="neutral">{feed}</Badge>}
            <Badge tone={stream.connected ? "ok" : "warn"}>{stream.connected ? "live" : "stale"}</Badge>
          </span>
        )}
      </div>
      {resolve.isError && (
        <div className="px-3 pt-3">
          <Alert>{resolve.error instanceof Error ? resolve.error.message : "Lookup failed"}</Alert>
        </div>
      )}
      <div className="relative flex-1 p-2">
        {!instrument ? (
          <div className={cn("flex items-center justify-center text-sm text-muted", compact ? "h-[300px]" : "h-[min(62vh,560px)]")}>Enter a ticker to load its chart.</div>
        ) : bars.isError ? (
          <Alert>{bars.error instanceof ApiError ? bars.error.message : "Bars unavailable"}</Alert>
        ) : showWidget && widget ? (
          <iframe
            title={`TradingView chart for ${instrument.symbol}`}
            src={tradingViewWidgetUrl(instrument.symbol, tf, theme)}
            sandbox="allow-scripts allow-same-origin allow-popups"
            referrerPolicy="no-referrer"
            className={cn("w-full rounded-md border-0", compact ? "h-[300px]" : "h-[min(62vh,560px)]")}
          />
        ) : (
          <>
            <ChartLegend symbol={instrument.symbol} tf={tf} legend={legend} />
            {bars.isPending && <p className="absolute inset-x-0 top-1/2 text-center text-sm text-muted">Loading…</p>}
            {bars.isSuccess && bars.data.bars.length === 0 && <p className="px-2 py-1 text-sm text-muted">No cached bars yet for this timeframe; live bars appear as they close.</p>}
            <PriceChart
              bars={bars.data?.bars ?? []}
              live={live}
              markers={markers}
              lines={lines}
              timeframe={tf}
              indicators={indicators}
              scores={scores}
              showVolume={overlays.has("volume")}
              onLegend={setLegend}
              label={`${instrument.symbol} ${tf} price chart`}
              className={compact ? "h-[300px]" : "h-[min(62vh,560px)]"}
            />
          </>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-1.5 text-[11px] text-muted">
        {instrument && <span className="font-mono">{instrument.id}</span>}
        {lastMs > 0 && <span>last bar {new Date(lastMs).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}</span>}
        {signals.isError && overlays.has("signals") && <span className="text-warn">signals unavailable</span>}
        <span className="ml-auto flex items-center gap-3">
          {instrument && widget && (
            <button type="button" className="underline-offset-2 hover:text-fg hover:underline" onClick={() => setShowWidget((v) => !v)} aria-pressed={showWidget}>
              {showWidget ? "Platform chart" : "TradingView widget"}
            </button>
          )}
          {instrument && (
            <a href={tradingViewUrl(instrument.symbol, tf)} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 hover:text-fg">
              Open on TradingView <ExternalLink className="size-3" aria-hidden />
            </a>
          )}
          <span>
            Charting by{" "}
            <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer" className="underline">
              TradingView
            </a>{" "}
            Lightweight Charts™
          </span>
        </span>
      </div>
    </Card>
  );
}

/** OHLCV under the crosshair (drawing values, for reading the chart only). */
function ChartLegend({ symbol, tf, legend }: { symbol: string; tf: Timeframe; legend: Legend | null }) {
  return (
    <div className="pointer-events-none absolute left-4 top-3 z-10 flex flex-wrap items-baseline gap-x-3 font-mono text-[11px]" aria-live="off">
      <span className="font-sans text-xs font-semibold text-fg">
        {symbol} · {tf}
      </span>
      {legend && (
        <>
          <span className="text-muted">
            O <span className="text-fg">{legend.open}</span>
          </span>
          <span className="text-muted">
            H <span className="text-fg">{legend.high}</span>
          </span>
          <span className="text-muted">
            L <span className="text-fg">{legend.low}</span>
          </span>
          <span className="text-muted">
            C <span className={legend.close >= legend.open ? "text-ok" : "text-live"}>{legend.close}</span>
          </span>
          <span className="text-muted">
            V <span className="text-fg">{legend.volume.toLocaleString()}</span>
          </span>
        </>
      )}
    </div>
  );
}
