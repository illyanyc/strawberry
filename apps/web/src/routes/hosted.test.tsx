import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { berryPath } from "@/components/BrandMark";
import { clearMockAccount, writeMockAccount } from "@/lib/mock-account";
import { BillingPage, ConnectionsPage } from "@/routes/hosted";
import { SignUpPage } from "@/routes/sign";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function renderPage(ui: ReactNode) {
  const root = createRootRoute({ component: Outlet });
  const index = createRoute({ getParentRoute: () => root, path: "/", component: () => ui });
  const login = createRoute({ getParentRoute: () => root, path: "/login", component: () => <p>login</p> });
  const router = createRouter({
    routeTree: root.addChildren([index, login]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await router.load();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  clearMockAccount();
});

describe("hosted product shell (UI-055)", () => {
  it("keeps the logo path in the repo source", () => {
    const svg = readFileSync(resolve(process.cwd(), "../../brand/logo.svg"), "utf8");
    expect(svg).toContain(berryPath);
    expect(svg).not.toContain("Alpha Inertia");
  });

  it("mocks signup without a card and without localStorage", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem");
    await renderPage(<SignUpPage />);
    expect(await screen.findByText(/Local mock/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Create free account" })).toBeTruthy();
    expect(screen.queryByLabelText(/card/i)).toBeNull();
    const localWrites = set.mock.calls.filter((call) => call[0] !== "strwbrry.mock.account" && String(call[0]).includes("strwbrry"));
    expect(localWrites).toEqual([]);
    set.mockRestore();
  });

  it("refuses the live Alpaca slot and does not ask for a live secret", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/api/v1/broker/credentials")) return Promise.resolve(json(200, { credentials: [] }));
      if (url.includes("/api/v1/risk-disclosure")) return Promise.resolve(json(200, { accepted: false }));
      if (url.includes("/api/v1/system/status")) return Promise.resolve(json(200, { live_enabled: false, mode: "paper" }));
      return Promise.resolve(json(404, { message: "missing" }));
    });
    writeMockAccount({ email: "a@example.com", plan: "free", mfa: true, disclosure: true, trial: false });
    await renderPage(<ConnectionsPage />);
    expect(await screen.findByRole("button", { name: "Store live key" })).toBeDisabled();
    expect(screen.getByText(/Live keys stay refused/)).toBeTruthy();
    expect(screen.queryByText(/Connect IBKR/)).toBeNull();
  });

  it("selects a plan locally and does not enable live trading", async () => {
    writeMockAccount({ email: "a@example.com", plan: "free", mfa: true, disclosure: true, trial: false });
    await renderPage(<BillingPage />);
    expect(await screen.findByText(/No card number is collected/)).toBeTruthy();
    screen.getByRole("button", { name: "Select Live" }).click();
    expect(await screen.findByText(/Live trading stays off/)).toBeTruthy();
    expect(sessionStorage.getItem("strwbrry.mock.account")).toContain('"plan":"live"');
    expect(localStorage.getItem("strwbrry.mock.account")).toBeNull();
  });
});
