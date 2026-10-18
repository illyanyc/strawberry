/**
 * Navigation registry (UI-041): the single list the sidebar, the mobile tab bar and "More" sheet,
 * the command palette, and the `g <key>` shortcuts are built from. A page appears everywhere by
 * adding one entry here and its route in `router.tsx`.
 *
 * Sections: `overview`, `trading`, `research`, `operations` (risk, monitoring, alerts), `settings`.
 * Section 13's monitoring pages plug into `operations`.
 */

import {
  Activity,
  BellRing,
  Bot,
  CandlestickChart,
  ClipboardList,
  Database,
  FlaskConical,
  Gauge,
  HeartPulse,
  KeyRound,
  LayoutDashboard,
  type LucideIcon,
  PieChart,
  Radio,
  ScrollText,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sigma,
  Workflow,
} from "lucide-react";

export type NavSection = "overview" | "trading" | "research" | "operations" | "settings";

export const NAV_SECTIONS: readonly { id: NavSection; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "trading", label: "Trading" },
  { id: "research", label: "Research" },
  { id: "operations", label: "Operations" },
  { id: "settings", label: "Settings" },
];

/** Every path the registry links to (each is a route in router.tsx). */
export type NavPath =
  | "/"
  | "/overview"
  | "/portfolio"
  | "/strategies"
  | "/orders"
  | "/charts"
  | "/data"
  | "/training"
  | "/experiments"
  | "/backtests"
  | "/analyst"
  | "/risk"
  | "/monitoring"
  | "/settings/notifications"
  | "/settings/tradingview"
  | "/settings/security"
  | "/settings/brokers"
  | "/settings/platform"
  | "/audit";

export interface NavItem {
  to: NavPath;
  label: string;
  /** Label in the mobile tab bar and "More" sheet. */
  short: string;
  icon: LucideIcon;
  section: NavSection;
  /** `primary` items sit in the phone's tab bar (at most four); the rest are under "More". */
  mobile?: "primary";
  /** Second key of the `g <key>` shortcut. */
  key?: string;
  /** Extra words the command palette matches. */
  keywords?: string;
}

export const NAV: readonly NavItem[] = [
  { to: "/", label: "Dashboard", short: "Home", icon: LayoutDashboard, section: "overview", mobile: "primary", key: "d", keywords: "home summary" },
  { to: "/overview", label: "Portfolio overview", short: "Overview", icon: PieChart, section: "overview", key: "v", keywords: "end of day eod manual strategy owners exposure" },
  { to: "/portfolio", label: "Portfolio", short: "Portfolio", icon: Gauge, section: "trading", mobile: "primary", key: "p", keywords: "positions ownership reconciliation breaks lots" },
  { to: "/strategies", label: "Strategies", short: "Strategies", icon: Workflow, section: "trading", mobile: "primary", key: "s", keywords: "mode allocation limits" },
  { to: "/orders", label: "Orders", short: "Orders", icon: ClipboardList, section: "trading", key: "o", keywords: "blotter fills intents rejects trace" },
  { to: "/charts", label: "Charts", short: "Charts", icon: CandlestickChart, section: "trading", mobile: "primary", key: "c", keywords: "chart price bars signals" },
  { to: "/data", label: "Data explorer", short: "Data", icon: Database, section: "trading", key: "x", keywords: "freshness feeds bars coverage stale" },
  { to: "/training", label: "Training", short: "Train", icon: Sigma, section: "research", key: "t", keywords: "model lightgbm ridge job" },
  { to: "/experiments", label: "Experiments", short: "Research", icon: FlaskConical, section: "research", key: "e", keywords: "studies lineage optuna registry holdout" },
  { to: "/backtests", label: "Backtests & tearsheets", short: "Tearsheets", icon: Activity, section: "research", key: "b", keywords: "tearsheet runs compare backtest" },
  { to: "/analyst", label: "AI analyst", short: "Analyst", icon: Bot, section: "research", key: "a", keywords: "assistant journal chat" },
  { to: "/risk", label: "Risk & kill switches", short: "Risk", icon: ShieldAlert, section: "operations", key: "r", keywords: "kill switch halt limits freeze" },
  { to: "/monitoring", label: "Monitoring", short: "Monitor", icon: HeartPulse, section: "operations", key: "m", keywords: "health drift divergence shortfall containment alerts inbox daily report graduation" },
  { to: "/settings/notifications", label: "Notifications", short: "Alerts", icon: BellRing, section: "settings", key: "n", keywords: "push pwa install pushover web push" },
  { to: "/settings/tradingview", label: "TradingView", short: "TradingView", icon: Radio, section: "settings", keywords: "webhook tv signals cloudflare" },
  { to: "/settings/security", label: "Security", short: "Security", icon: ShieldCheck, section: "settings", keywords: "passkey totp recovery sessions" },
  { to: "/settings/brokers", label: "Broker connections", short: "Brokers", icon: KeyRound, section: "settings", keywords: "credentials alpaca ibkr" },
  { to: "/settings/platform", label: "Platform settings", short: "Settings", icon: Settings2, section: "settings", keywords: "flags live mobile order entry" },
  { to: "/audit", label: "Audit log", short: "Audit", icon: ScrollText, section: "settings", keywords: "audit chain verify" },
];

/** Items of one section, in registry order. */
export function navSection(section: NavSection): NavItem[] {
  return NAV.filter((n) => n.section === section);
}

/** The phone's tab-bar items (the rest go under "More"). */
export function mobilePrimary(): NavItem[] {
  return NAV.filter((n) => n.mobile === "primary");
}

/** Resolve the second key of a `g <key>` shortcut. */
export function navByKey(key: string): NavItem | undefined {
  return NAV.find((n) => n.key === key);
}

/**
 * Command-palette matching: every query word must appear in the label, section, or keywords
 * (case-insensitive); results keep registry order with label-prefix matches first.
 */
export function matchNav(query: string, items: readonly NavItem[] = NAV): NavItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...items];
  const hay = (n: NavItem) => `${n.label} ${n.short} ${n.section} ${n.keywords ?? ""}`.toLowerCase();
  const hits = items.filter((n) => words.every((w) => hay(n).includes(w)));
  const first = words[0]!;
  return [...hits.filter((n) => n.label.toLowerCase().startsWith(first)), ...hits.filter((n) => !n.label.toLowerCase().startsWith(first))];
}
