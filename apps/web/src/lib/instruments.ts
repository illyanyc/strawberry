import { useMutation } from "@tanstack/react-query";

import { api } from "./api";
import type { ResolveResponse } from "./trading";

/** Resolve a current ticker to its instrument_id (UI-011); everything downstream keys on the ID. */
export async function resolveSymbol(symbol: string): Promise<string> {
  const r = await api<ResolveResponse>(`/api/v1/instruments/resolve?symbols=${encodeURIComponent(symbol.trim().toUpperCase())}`);
  const m = r.matches[0];
  if (!m) throw new Error(`Unknown symbol ${symbol.toUpperCase()}`);
  return m.instrumentId;
}

export function useResolveSymbol() {
  return useMutation({ mutationFn: resolveSymbol });
}
