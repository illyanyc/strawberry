import { useQuery } from "@tanstack/react-query";

import { api, type TradingStatus } from "./api";
import type { AccountsResponse, EquityCurve, StrategiesResponse } from "./trading";

/** Shared server-state hooks used by several pages (kept out of route modules so pages split cleanly). */

export function useAccounts() {
  return useQuery({ queryKey: ["accounts"], queryFn: () => api<AccountsResponse>("/api/v1/accounts"), refetchInterval: 15_000 });
}

export function useStrategies() {
  return useQuery({ queryKey: ["strategies"], queryFn: () => api<StrategiesResponse>("/api/v1/strategies"), refetchInterval: 15_000 });
}

export function useStatus() {
  return useQuery({ queryKey: ["status"], queryFn: () => api<TradingStatus>("/api/v1/system/status") });
}

/** The ledger's equity curve for an owner (strategy ID or `manual`) in one account. */
export function useEquityCurve(owner: string, account: string, enabled = true) {
  return useQuery({
    queryKey: ["equity", owner, account],
    enabled: enabled && owner !== "" && account !== "",
    queryFn: () => api<EquityCurve>(`/api/v1/strategies/${encodeURIComponent(owner)}/equity?account=${encodeURIComponent(account)}`),
    refetchInterval: 60_000,
  });
}
