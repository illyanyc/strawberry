import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useCallback, useMemo, useState } from "react";

import { orderLines, PriceChart, type ChartMarker } from "@/components/PriceChart";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Alert, Badge, Field } from "@/components/ui/form";
import { api, ApiError } from "@/lib/api";
import { useResolveSymbol } from "@/lib/instruments";
import { useMarketStream } from "@/lib/market";
import { dec, TIMEFRAMES, type Bar, type BarsResponse, type ExecutionReport, type FillsResponse, type OrdersResponse, type Timeframe } from "@/lib/trading";

/**
 * Charts (UI-012): candles from the platform's own bar cache with live final and partial bars over
 * SSE, fill markers (history plus live executions), and price lines for resting orders and
 * protective stops in the instrument. The feed label comes from the bars themselves.
 */
export function ChartsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [symbol, setSymbol] = useState("");
  const [instrument, setInstrument] = useState<{ id: string; symbol: string } | null>(null);
  const [tf, setTf] = useState<Timeframe>("15m");
  const [live, setLive] = useState<Bar | null>(null);
  const [liveFills, setLiveFills] = useState<ExecutionReport[]>([]);
  const resolve = useResolveSymbol();
  const id = instrument?.id ?? null;

  const bars = useQuery({
    queryKey: ["bars", id, tf],
    enabled: id !== null,
    queryFn: () => api<BarsResponse>(`/api/v1/bars?instrument_id=${encodeURIComponent(id!)}&timeframe=${tf}&limit=500`),
  });
  const fills = useQuery({
    queryKey: ["fills", { instrument: id }],
    enabled: id !== null,
    queryFn: () => api<FillsResponse>(`/api/v1/fills?instrument_id=${encodeURIComponent(id!)}&limit=500`),
  });
  const orders = useQuery({
    queryKey: ["orders", { open: true }],
    enabled: id !== null,
    queryFn: () => api<OrdersResponse>("/api/v1/orders?open_only=true&limit=500"),
    refetchInterval: 10_000,
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
    onResync: useCallback(() => void queryClient.invalidateQueries({ queryKey: ["bars"] }), [queryClient]),
    onSessionEnded: useCallback(() => void navigate({ to: "/login" }), [navigate]),
  });

  const markers = useMemo<ChartMarker[]>(() => {
    const seen = new Set<string>();
    const out: ChartMarker[] = [];
    const add = (exec: string, timeNs: string, side: string, qty: string, price: string, who: string) => {
      if (seen.has(exec)) return;
      seen.add(exec);
      out.push({ timeNs, side: side === "SIDE_BUY" ? "buy" : "sell", text: `${who} ${qty} @ ${price}` });
    };
    for (const f of fills.data?.fills ?? []) add(f.brokerExecId, f.eventTime, f.side, dec(f.qty), dec(f.price), f.strategyId || "manual");
    for (const f of liveFills) add(f.brokerExecId, f.eventTime, f.side, dec(f.qty), dec(f.price), f.clientOrderId.startsWith("tb1-") ? f.clientOrderId.split("-")[1]! : "manual");
    return out;
  }, [fills.data, liveFills]);
  const lines = useMemo(() => orderLines((orders.data?.orders ?? []).filter((o) => o.instrumentId === id)), [orders.data, id]);
  const feed = bars.data?.bars.at(-1)?.feed;

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Charts</h1>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const s = symbol.trim().toUpperCase();
          resolve.mutate(s, {
            onSuccess: (instrumentId) => {
              setInstrument({ id: instrumentId, symbol: s });
              setLive(null);
              setLiveFills([]);
            },
          });
        }}
      >
        <div className="w-40">
          <Field label="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        </div>
        <Button type="submit" variant="secondary" disabled={!symbol} busy={resolve.isPending}>
          Show
        </Button>
        <div role="group" aria-label="Timeframe" className="ml-2 flex gap-1">
          {TIMEFRAMES.map((t) => (
            <Button
              key={t}
              size="sm"
              variant={tf === t ? "secondary" : "ghost"}
              aria-pressed={tf === t}
              onClick={() => {
                setTf(t);
                setLive(null);
              }}
            >
              {t}
            </Button>
          ))}
        </div>
      </form>
      {resolve.isError && <Alert>{resolve.error instanceof Error ? resolve.error.message : "Lookup failed"}</Alert>}
      <Card>
        <CardHeader
          title={instrument ? `${instrument.symbol} · ${tf}` : "Choose a symbol"}
          description={instrument ? `instrument ${instrument.id}` : "History comes from the platform's bar cache (last 500 bars per timeframe)."}
          action={
            instrument && (
              <span className="flex gap-2">
                {feed && <Badge tone="neutral">{feed}</Badge>}
                <Badge tone={stream.connected ? "ok" : "warn"}>{stream.connected ? "live" : "stale"}</Badge>
              </span>
            )
          }
        />
        <CardBody>
          {!instrument ? (
            <p className="text-sm text-muted">Enter a ticker to load its chart.</p>
          ) : bars.isError ? (
            <Alert>{bars.error instanceof ApiError ? bars.error.message : "Bars unavailable"}</Alert>
          ) : (
            <>
              {bars.isPending && <p className="text-sm text-muted">Loading…</p>}
              {bars.isSuccess && bars.data.bars.length === 0 && <p className="text-sm text-muted">No cached bars yet for this timeframe; live bars appear as they close.</p>}
              <PriceChart bars={bars.data?.bars ?? []} live={live} markers={markers} lines={lines} timeframe={tf} className="h-[min(60vh,520px)]" />
            </>
          )}
          <p className="mt-2 text-right text-[11px] text-muted">
            Charting by{" "}
            <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer" className="underline">
              TradingView
            </a>{" "}
            Lightweight Charts™
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
