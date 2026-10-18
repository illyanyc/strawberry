import { useEffect, useRef, useState } from "react";

import type { Bar, ExecutionReport, Timeframe } from "./trading";

export interface MarketHandlers {
  onBar: (bar: Bar) => void;
  onFill: (fill: ExecutionReport) => void;
  /** The server dropped events (slow client) or the stream reconnected: reload history. */
  onResync: () => void;
  onSessionEnded: () => void;
}

/**
 * Live chart data over SSE (`/api/v1/stream/market`, UI-012): final and corrected bars, partial
 * bars coalesced by the gateway to at most 4 Hz, and executions in the instrument. Reconnects are
 * left to EventSource; every reconnect after the first open triggers a history reload.
 */
export function useMarketStream(instrumentId: string | null, timeframe: Timeframe, handlers: MarketHandlers): { connected: boolean } {
  const [connected, setConnected] = useState(false);
  const h = useRef(handlers);
  useEffect(() => {
    h.current = handlers;
  }, [handlers]);

  useEffect(() => {
    if (!instrumentId) return;
    const params = new URLSearchParams({ instrument_id: instrumentId, timeframe });
    const es = new EventSource(`/api/v1/stream/market?${params}`, { withCredentials: true });
    let opened = false;
    const parse = <T,>(e: Event) => JSON.parse((e as MessageEvent<string>).data) as T;
    es.addEventListener("open", () => {
      setConnected(true);
      if (opened) h.current.onResync();
      opened = true;
    });
    es.addEventListener("error", () => setConnected(false));
    es.addEventListener("bar", (e) => h.current.onBar(parse<Bar>(e)));
    es.addEventListener("partial", (e) => h.current.onBar(parse<Bar>(e)));
    es.addEventListener("fill", (e) => h.current.onFill(parse<ExecutionReport>(e)));
    es.addEventListener("resync", () => h.current.onResync());
    es.addEventListener("session-ended", () => {
      es.close();
      h.current.onSessionEnded();
    });
    return () => {
      es.close();
      setConnected(false);
    };
  }, [instrumentId, timeframe]);

  return { connected };
}
