/**
 * Types and helpers for the gateway's trading resources. Responses are the canonical protojson form
 * of the tb.* contract messages (ADR-0028), so the types are the generated `*Json` types from
 * `@strawberry/contracts` (never hand-written): lowerCamelCase fields, all optional; decimals are
 * `{ value }`, int64 timestamps are UTC-nanosecond strings, enums are their full names.
 * The UI never computes P&L, ownership, or risk; it only displays what services return.
 */

import type { DecimalJson } from "@strawberry/contracts/tb/common/v1/common_pb";
import type { BreakJson } from "@strawberry/contracts/tb/ledger/v1/ledger_pb";
import type {
  AccountViewJson,
  EquityPointJson,
  GetEquityCurveResponseJson,
  GetStrategyPortfolioResponseJson,
  InstrumentBreakdownJson,
  ListAccountsResponseJson,
  OwnerViewJson,
} from "@strawberry/contracts/tb/ledger/v1/ledger_service_pb";
import type { BarJson, FeedStatusJson, GetBarsResponseJson, GetFeedStatusResponseJson, ResolveSymbolsResponseJson } from "@strawberry/contracts/tb/marketdata/v1/marketdata_pb";
import type {
  FillJson,
  GetRiskLimitsResponseJson,
  GetTraceResponseJson,
  IntentRecordJson,
  KillSwitchJson,
  ListFillsResponseJson,
  ListIntentsResponseJson,
  ListKillSwitchesResponseJson,
  ListOrdersResponseJson,
  ListStrategiesResponseJson,
  PreviewIntentResponseJson,
  RiskLimitsJson,
  StrategyJson,
  SubmitIntentResponseJson,
  TradingStatusJson,
} from "@strawberry/contracts/tb/orders/v1/execution_pb";
import type { ExecutionReportJson, OrderJson, RiskCheckJson } from "@strawberry/contracts/tb/orders/v1/orders_pb";
import type { SignalJson } from "@strawberry/contracts/tb/signals/v1/signals_pb";

/**
 * The gateway marshals with `EmitUnpopulated` (ADR-0028), so scalars, enums, and lists are always
 * present and singular message fields are an object or `null`. Unset oneof members (for example
 * `OrderIntent.quantity` vs `targetPosition`) are omitted; read them with `?.` like any message.
 */
type Field<V> = NonNullable<V> extends (infer E)[] ? Emitted<E>[] : NonNullable<V> extends object ? Emitted<NonNullable<V>> | null : NonNullable<V>;
export type Emitted<T> = T extends (infer E)[] ? Emitted<E>[] : T extends object ? { [K in keyof T]-?: Field<T[K]> } : T;

export type Dec = Emitted<DecimalJson>;
export type AccountView = Emitted<AccountViewJson>;
export type AccountsResponse = Emitted<ListAccountsResponseJson>;
export type OwnerView = Emitted<OwnerViewJson>;
export type InstrumentBreakdown = Emitted<InstrumentBreakdownJson>;
export type LedgerBreak = Emitted<BreakJson>;
export type EquityPoint = Emitted<EquityPointJson>;
export type EquityCurve = Emitted<GetEquityCurveResponseJson>;
export type StrategyPortfolio = Emitted<GetStrategyPortfolioResponseJson>;
export type Strategy = Emitted<StrategyJson>;
export type StrategiesResponse = Emitted<ListStrategiesResponseJson>;
export type KillSwitch = Emitted<KillSwitchJson>;
export type KillSwitchesResponse = Emitted<ListKillSwitchesResponseJson>;
export type TradingCore = Emitted<TradingStatusJson>;
export type RiskLimits = Emitted<RiskLimitsJson>;
export type RiskLimitsResponse = Emitted<GetRiskLimitsResponseJson>;
export type Order = Emitted<OrderJson>;
export type OrdersResponse = Emitted<ListOrdersResponseJson>;
export type IntentRecord = Emitted<IntentRecordJson>;
export type IntentsResponse = Emitted<ListIntentsResponseJson>;
export type Fill = Emitted<FillJson>;
export type FillsResponse = Emitted<ListFillsResponseJson>;
export type TraceResponse = Emitted<GetTraceResponseJson>;
export type RiskCheck = Emitted<RiskCheckJson>;
export type PreviewResponse = Emitted<PreviewIntentResponseJson>;
export type SubmitResponse = Emitted<SubmitIntentResponseJson>;
export type Bar = Emitted<BarJson>;
export type BarsResponse = Emitted<GetBarsResponseJson>;
export type ExecutionReport = Emitted<ExecutionReportJson>;
export type ResolveResponse = Emitted<ResolveSymbolsResponseJson>;
export type FeedStatus = Emitted<FeedStatusJson>;
export type Signal = Emitted<SignalJson>;
/** GET /api/v1/signals (UI-049). */
export interface SignalsResponse {
  signals: Signal[];
}
export type FeedStatusResponse = Emitted<GetFeedStatusResponseJson>;

/** The gateway's own order-ticket body (UI-015; contracts/openapi ManualOrder). */
export interface ManualTicket {
  broker_account_id: string;
  instrument_id: string;
  side: "buy" | "sell" | "sell_short";
  quantity: string;
  order_type: "market" | "limit";
  limit_price: string;
  tif: "day" | "gtc";
  reason: string;
  intent_id?: string;
}

/** Decimal string from a protojson Decimal, or "—" when absent. Never parsed into a float for math. */
export function dec(d: Dec | null | undefined): string {
  return d?.value ? d.value : "—";
}

/** Lower-case label for a proto enum name, e.g. ("ORDER_STATE_FILLED", "ORDER_STATE_") -> "filled". */
export function enumLabel(name: string | undefined, prefix: string): string {
  if (!name) return "—";
  return name.replace(prefix, "").toLowerCase().replaceAll("_", " ");
}

/** UTC nanoseconds (int64 string) to epoch milliseconds, exact for any realistic timestamp. */
export function nsToMs(ns: string | number | undefined): number {
  if (ns === undefined || ns === "" || ns === 0 || ns === "0") return 0;
  return Number(BigInt(ns) / 1_000_000n);
}

/** Sign of a decimal string: -1, 0, or 1 (for icons and colors; the text always shows the sign). */
export function decSign(d: Dec | null | undefined): -1 | 0 | 1 {
  const v = d?.value?.trim() ?? "";
  if (v === "" || /^-?0*(\.0*)?$/.test(v)) return 0;
  return v.startsWith("-") ? -1 : 1;
}

export const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "1d"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];

const TERMINAL = new Set(["ORDER_STATE_FILLED", "ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "ORDER_STATE_EXPIRED", "ORDER_STATE_REPLACED"]);

export function isTerminal(state: string | undefined): boolean {
  return state !== undefined && TERMINAL.has(state);
}

/** Age in seconds between a UTC-ns timestamp and `nowMs`, or null when the timestamp is unset. */
export function ageSeconds(ns: string | undefined, nowMs: number): number | null {
  const ms = nsToMs(ns);
  return ms === 0 ? null : Math.max(0, Math.round((nowMs - ms) / 1000));
}

/** Compact age label: "12s", "4m", "3h", "2d". */
export function ageLabel(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}
