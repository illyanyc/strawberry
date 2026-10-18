import { QueryClient } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, lazyRouteComponent, Outlet, redirect } from "@tanstack/react-router";

import { AppShell } from "@/components/AppShell";
import { RouteError, RoutePending } from "@/components/RouteStates";
import { StepUpDialog } from "@/components/StepUpDialog";
import { api, type AuthState } from "@/lib/api";
import { LoginPage, SetupPage } from "@/routes/auth";
import { DashboardPage } from "@/routes/dashboard";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: true } },
});

const authState = () => api<AuthState>("/auth/state", { allow401: true });

/*
 * Route-level code splitting (UI-043): the login screens, the shell, and the dashboard are in the
 * entry chunk so the first authenticated paint needs one script; every other page (charts and the
 * research pages carry the heavy libraries) is its own chunk, prefetched on link hover/focus
 * (`defaultPreload: "intent"`). Chunks are same-origin files, so the strict CSP is unchanged.
 */
const lazy = {
  analyst: lazyRouteComponent(() => import("@/routes/analyst"), "AnalystPage"),
  audit: lazyRouteComponent(() => import("@/routes/audit"), "AuditPage"),
  backtests: lazyRouteComponent(() => import("@/routes/backtests"), "BacktestsPage"),
  tearsheet: lazyRouteComponent(() => import("@/routes/backtests"), "TearsheetPage"),
  compare: lazyRouteComponent(() => import("@/routes/backtests"), "ComparePage"),
  charts: lazyRouteComponent(() => import("@/routes/charts"), "ChartsPage"),
  orderTicket: lazyRouteComponent(() => import("@/routes/order-ticket"), "OrderTicketPage"),
  orders: lazyRouteComponent(() => import("@/routes/orders"), "OrdersPage"),
  overview: lazyRouteComponent(() => import("@/routes/overview"), "OverviewPage"),
  portfolio: lazyRouteComponent(() => import("@/routes/portfolio"), "PortfolioPage"),
  risk: lazyRouteComponent(() => import("@/routes/risk"), "RiskPage"),
  security: lazyRouteComponent(() => import("@/routes/security"), "SecurityPage"),
  brokers: lazyRouteComponent(() => import("@/routes/settings"), "BrokersPage"),
  platform: lazyRouteComponent(() => import("@/routes/settings"), "PlatformSettingsPage"),
  notifications: lazyRouteComponent(() => import("@/routes/notifications"), "NotificationsPage"),
  tradingview: lazyRouteComponent(() => import("@/routes/tradingview"), "TradingViewPage"),
  strategies: lazyRouteComponent(() => import("@/routes/strategies"), "StrategiesPage"),
  strategy: lazyRouteComponent(() => import("@/routes/strategies"), "StrategyDetailPage"),
  training: lazyRouteComponent(() => import("@/routes/training"), "TrainingPage"),
  trainingJob: lazyRouteComponent(() => import("@/routes/training"), "TrainingJobPage"),
  run: lazyRouteComponent(() => import("@/routes/training"), "RunPage"),
  experiments: lazyRouteComponent(() => import("@/routes/experiments"), "ExperimentsPage"),
  lineage: lazyRouteComponent(() => import("@/routes/experiments"), "LineagePage"),
  study: lazyRouteComponent(() => import("@/routes/experiments"), "StudyPage"),
  candidate: lazyRouteComponent(() => import("@/routes/experiments"), "CandidatePage"),
};

const rootRoute = createRootRoute({
  component: () => (
    <>
      <Outlet />
      <StepUpDialog />
    </>
  ),
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  beforeLoad: async () => {
    const s = await authState();
    if (s.setup_required) throw redirect({ to: "/setup" });
    if (s.authenticated) throw redirect({ to: "/" });
  },
  component: LoginPage,
});

const setupRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/setup",
  beforeLoad: async () => {
    const s = await authState();
    if (!s.setup_required) throw redirect({ to: s.authenticated ? "/" : "/login" });
  },
  component: SetupPage,
});

// Every page under this layout requires a session; the server enforces it regardless (SE-004).
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: async () => {
    const s = await authState();
    if (s.setup_required) throw redirect({ to: "/setup" });
    if (!s.authenticated) throw redirect({ to: "/login" });
  },
  component: AppShell,
});

const page = <P extends string>(path: P, component: Parameters<typeof createRoute>[0]["component"]) => createRoute({ getParentRoute: () => appRoute, path, component });

/** Chart deep links: /charts?symbol=AAPL&tf=15m&layout=2 (from the palette, strategy pages, and alerts). */
export interface ChartSearch {
  symbol?: string;
  tf?: string;
  layout?: "1" | "2" | "4";
  strategy?: string;
}

const chartsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/charts",
  validateSearch: (s: Record<string, unknown>): ChartSearch => ({
    symbol: typeof s.symbol === "string" && /^[A-Za-z][A-Za-z0-9.]{0,9}$/.test(s.symbol) ? s.symbol.toUpperCase() : undefined,
    tf: typeof s.tf === "string" ? s.tf : undefined,
    layout: s.layout === "2" || s.layout === "4" || s.layout === 2 || s.layout === 4 ? (String(s.layout) as "2" | "4") : undefined,
    strategy: typeof s.strategy === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(s.strategy) ? s.strategy : undefined,
  }),
  component: lazy.charts,
});

/** Run comparison: /backtests/compare?a=<runId>&b=<runId>. */
export interface CompareSearch {
  a?: string;
  b?: string;
}

const compareRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/backtests/compare",
  validateSearch: (s: Record<string, unknown>): CompareSearch => ({
    a: typeof s.a === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(s.a) ? s.a : undefined,
    b: typeof s.b === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(s.b) ? s.b : undefined,
  }),
  component: lazy.compare,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  setupRoute,
  appRoute.addChildren([
    page("/", DashboardPage),
    page("/overview", lazy.overview),
    page("/portfolio", lazy.portfolio),
    page("/strategies", lazy.strategies),
    page("/strategies/$strategyId", lazy.strategy),
    page("/orders", lazy.orders),
    page("/orders/new", lazy.orderTicket),
    chartsRoute,
    page("/risk", lazy.risk),
    page("/training", lazy.training),
    page("/training/jobs/$jobId", lazy.trainingJob),
    page("/training/runs/$runId", lazy.run),
    page("/experiments", lazy.experiments),
    page("/experiments/lineages/$lineageId", lazy.lineage),
    page("/experiments/studies/$studyId", lazy.study),
    page("/experiments/candidates/$candidateId", lazy.candidate),
    page("/backtests", lazy.backtests),
    compareRoute,
    page("/backtests/runs/$runId", lazy.tearsheet),
    page("/analyst", lazy.analyst),
    page("/settings/security", lazy.security),
    page("/settings/brokers", lazy.brokers),
    page("/settings/platform", lazy.platform),
    page("/settings/notifications", lazy.notifications),
    page("/settings/tradingview", lazy.tradingview),
    page("/audit", lazy.audit),
  ]),
]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultPendingComponent: RoutePending,
  defaultPendingMs: 150,
  defaultErrorComponent: RouteError,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
