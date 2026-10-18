import { QueryClient } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, Outlet, redirect } from "@tanstack/react-router";

import { AppShell } from "@/components/AppShell";
import { StepUpDialog } from "@/components/StepUpDialog";
import { api, type AuthState } from "@/lib/api";
import { AuditPage } from "@/routes/audit";
import { ChartsPage } from "@/routes/charts";
import { OrderTicketPage } from "@/routes/order-ticket";
import { OrdersPage } from "@/routes/orders";
import { PortfolioPage } from "@/routes/portfolio";
import { RiskPage } from "@/routes/risk";
import { LoginPage, SetupPage } from "@/routes/auth";
import { DashboardPage } from "@/routes/dashboard";
import { CandidatePage, ExperimentsPage, LineagePage, StudyPage } from "@/routes/experiments";
import { SecurityPage } from "@/routes/security";
import { BrokersPage, PlatformSettingsPage } from "@/routes/settings";
import { StrategiesPage, StrategyDetailPage } from "@/routes/strategies";
import { RunPage, TrainingJobPage, TrainingPage } from "@/routes/training";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: true } },
});

const authState = () => api<AuthState>("/auth/state", { allow401: true });

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

const dashboardRoute = createRoute({ getParentRoute: () => appRoute, path: "/", component: DashboardPage });
const securityRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings/security", component: SecurityPage });
const brokersRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings/brokers", component: BrokersPage });
const platformRoute = createRoute({ getParentRoute: () => appRoute, path: "/settings/platform", component: PlatformSettingsPage });
const auditRoute = createRoute({ getParentRoute: () => appRoute, path: "/audit", component: AuditPage });
const portfolioRoute = createRoute({ getParentRoute: () => appRoute, path: "/portfolio", component: PortfolioPage });
const strategiesRoute = createRoute({ getParentRoute: () => appRoute, path: "/strategies", component: StrategiesPage });
const strategyRoute = createRoute({ getParentRoute: () => appRoute, path: "/strategies/$strategyId", component: StrategyDetailPage });
const ordersRoute = createRoute({ getParentRoute: () => appRoute, path: "/orders", component: OrdersPage });
const orderTicketRoute = createRoute({ getParentRoute: () => appRoute, path: "/orders/new", component: OrderTicketPage });
const chartsRoute = createRoute({ getParentRoute: () => appRoute, path: "/charts", component: ChartsPage });
const riskRoute = createRoute({ getParentRoute: () => appRoute, path: "/risk", component: RiskPage });
const trainingRoute = createRoute({ getParentRoute: () => appRoute, path: "/training", component: TrainingPage });
const trainingJobRoute = createRoute({ getParentRoute: () => appRoute, path: "/training/jobs/$jobId", component: TrainingJobPage });
const runRoute = createRoute({ getParentRoute: () => appRoute, path: "/training/runs/$runId", component: RunPage });
const experimentsRoute = createRoute({ getParentRoute: () => appRoute, path: "/experiments", component: ExperimentsPage });
const lineageRoute = createRoute({ getParentRoute: () => appRoute, path: "/experiments/lineages/$lineageId", component: LineagePage });
const studyRoute = createRoute({ getParentRoute: () => appRoute, path: "/experiments/studies/$studyId", component: StudyPage });
const candidateRoute = createRoute({ getParentRoute: () => appRoute, path: "/experiments/candidates/$candidateId", component: CandidatePage });

const routeTree = rootRoute.addChildren([
  loginRoute,
  setupRoute,
  appRoute.addChildren([
    dashboardRoute,
    portfolioRoute,
    strategiesRoute,
    strategyRoute,
    ordersRoute,
    orderTicketRoute,
    chartsRoute,
    riskRoute,
    trainingRoute,
    trainingJobRoute,
    runRoute,
    experimentsRoute,
    lineageRoute,
    studyRoute,
    candidateRoute,
    securityRoute,
    brokersRoute,
    platformRoute,
    auditRoute,
  ]),
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
