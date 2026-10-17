import { useEffect, useRef, useState } from "react";

import type { TradingStatus } from "./api";

/** How old the last event may be before values are shown as stale. */
export const STALE_AFTER_MS = 45_000;

export interface StatusStream {
  status: TradingStatus | null;
  connected: boolean;
  lastEventAt: number | null;
}

/**
 * Subscribes to the gateway's SSE stream (D14). EventSource reconnects on its own and resends
 * Last-Event-ID; a `session-ended` event closes the stream and reports the session as gone.
 */
export function useTradingStatus(onSessionEnded: () => void): StatusStream {
  const [state, setState] = useState<StatusStream>({ status: null, connected: false, lastEventAt: null });
  // Keep the latest callback without re-opening the stream when its identity changes.
  const onEnded = useRef(onSessionEnded);
  useEffect(() => {
    onEnded.current = onSessionEnded;
  }, [onSessionEnded]);

  useEffect(() => {
    const es = new EventSource("/api/v1/events", { withCredentials: true });
    es.addEventListener("open", () => setState((s) => ({ ...s, connected: true })));
    es.addEventListener("error", () => setState((s) => ({ ...s, connected: false })));
    es.addEventListener("trading-status", (e) => {
      const status = JSON.parse((e as MessageEvent<string>).data) as TradingStatus;
      setState({ status, connected: true, lastEventAt: Date.now() });
    });
    es.addEventListener("session-ended", () => {
      es.close();
      onEnded.current();
    });
    return () => es.close();
  }, []);

  return state;
}

/** Re-renders every `ms` so staleness indicators update without new events. */
export function useNow(ms = 5_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
